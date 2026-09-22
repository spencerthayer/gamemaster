"""Tests for migration 0002 facts schema."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.storage.sqlite import connect, migrate


def _seed_setting_and_campaign(conn: sqlite3.Connection) -> tuple[str, str]:
    conn.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("setting-1", "Test Setting", "2026-01-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO campaigns (campaign_id, name, system_id, setting_id, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "campaign-1",
            "Test Campaign",
            "test-system",
            "setting-1",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    return "setting-1", "campaign-1"


@pytest.fixture
def migrated_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "schema.db")
    migrate(conn)
    yield conn
    conn.close()


def test_migrate_creates_facts_table(migrated_conn: sqlite3.Connection) -> None:
    rows = migrated_conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'facts'"
    ).fetchall()
    assert len(rows) == 1


def test_setting_scoped_fact_accepts_setting_id_no_campaign_id(
    migrated_conn: sqlite3.Connection,
) -> None:
    setting_id, _ = _seed_setting_and_campaign(migrated_conn)
    migrated_conn.execute(
        "INSERT INTO facts "
        "(fact_id, fact_scope, setting_id, predicate, value, created_at) "
        "VALUES (?, 'setting', ?, ?, ?, ?)",
        ("fact-1", setting_id, "has_trait", "ancient", "2026-01-01T00:00:00+00:00"),
    )
    row = migrated_conn.execute(
        "SELECT fact_scope, setting_id, campaign_id FROM facts WHERE fact_id = ?",
        ("fact-1",),
    ).fetchone()
    assert row["fact_scope"] == "setting"
    assert row["setting_id"] == setting_id
    assert row["campaign_id"] is None


def test_campaign_scoped_fact_accepts_campaign_id(
    migrated_conn: sqlite3.Connection,
) -> None:
    _, campaign_id = _seed_setting_and_campaign(migrated_conn)
    migrated_conn.execute(
        "INSERT INTO facts "
        "(fact_id, fact_scope, campaign_id, predicate, value, created_at) "
        "VALUES (?, 'campaign', ?, ?, ?, ?)",
        ("fact-2", campaign_id, "knows", "secret", "2026-01-01T00:00:00+00:00"),
    )
    row = migrated_conn.execute(
        "SELECT fact_scope, campaign_id FROM facts WHERE fact_id = ?",
        ("fact-2",),
    ).fetchone()
    assert row["fact_scope"] == "campaign"
    assert row["campaign_id"] == campaign_id


def test_campaign_scoped_fact_may_carry_setting_id(
    migrated_conn: sqlite3.Connection,
) -> None:
    setting_id, campaign_id = _seed_setting_and_campaign(migrated_conn)
    migrated_conn.execute(
        "INSERT INTO facts "
        "(fact_id, fact_scope, setting_id, campaign_id, predicate, value, created_at) "
        "VALUES (?, 'campaign', ?, ?, ?, ?, ?)",
        (
            "fact-3",
            setting_id,
            campaign_id,
            "overlays",
            "base lore",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    row = migrated_conn.execute(
        "SELECT setting_id, campaign_id FROM facts WHERE fact_id = ?",
        ("fact-3",),
    ).fetchone()
    assert row["setting_id"] == setting_id
    assert row["campaign_id"] == campaign_id


def test_setting_scoped_fact_rejects_campaign_id(
    migrated_conn: sqlite3.Connection,
) -> None:
    setting_id, campaign_id = _seed_setting_and_campaign(migrated_conn)
    with pytest.raises(sqlite3.IntegrityError):
        migrated_conn.execute(
            "INSERT INTO facts "
            "(fact_id, fact_scope, setting_id, campaign_id, predicate, value, created_at) "
            "VALUES (?, 'setting', ?, ?, ?, ?, ?)",
            (
                "fact-bad",
                setting_id,
                campaign_id,
                "has_trait",
                "ancient",
                "2026-01-01T00:00:00+00:00",
            ),
        )


def test_canon_state_rejects_invalid_values(
    migrated_conn: sqlite3.Connection,
) -> None:
    setting_id, _ = _seed_setting_and_campaign(migrated_conn)
    with pytest.raises(sqlite3.IntegrityError):
        migrated_conn.execute(
            "INSERT INTO facts "
            "(fact_id, fact_scope, setting_id, predicate, value, canon_state, created_at) "
            "VALUES (?, 'setting', ?, ?, ?, ?, ?)",
            (
                "fact-bad-canon",
                setting_id,
                "has_trait",
                "ancient",
                "rejected",
                "2026-01-01T00:00:00+00:00",
            ),
        )


def test_knowledge_state_rejects_invalid_values(
    migrated_conn: sqlite3.Connection,
) -> None:
    setting_id, _ = _seed_setting_and_campaign(migrated_conn)
    with pytest.raises(sqlite3.IntegrityError):
        migrated_conn.execute(
            "INSERT INTO facts "
            "(fact_id, fact_scope, setting_id, predicate, value, knowledge_state, created_at) "
            "VALUES (?, 'setting', ?, ?, ?, ?, ?)",
            (
                "fact-bad-knowledge",
                setting_id,
                "has_trait",
                "ancient",
                "partial",
                "2026-01-01T00:00:00+00:00",
            ),
        )


def test_fact_without_provenance_columns_allowed(
    migrated_conn: sqlite3.Connection,
) -> None:
    setting_id, _ = _seed_setting_and_campaign(migrated_conn)
    migrated_conn.execute(
        "INSERT INTO facts "
        "(fact_id, fact_scope, setting_id, predicate, value, created_at) "
        "VALUES (?, 'setting', ?, ?, ?, ?)",
        (
            "fact-hand-authored",
            setting_id,
            "authored_by",
            "gm",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    row = migrated_conn.execute(
        "SELECT source_document_id, source_chunk_id, import_job_id, extraction_method "
        "FROM facts WHERE fact_id = ?",
        ("fact-hand-authored",),
    ).fetchone()
    assert row["source_document_id"] is None
    assert row["source_chunk_id"] is None
    assert row["import_job_id"] is None
    assert row["extraction_method"] is None


def test_proposed_and_known_allowed(
    migrated_conn: sqlite3.Connection,
) -> None:
    setting_id, _ = _seed_setting_and_campaign(migrated_conn)
    migrated_conn.execute(
        "INSERT INTO facts "
        "(fact_id, fact_scope, setting_id, predicate, value, "
        "canon_state, knowledge_state, created_at) "
        "VALUES (?, 'setting', ?, ?, ?, ?, ?, ?)",
        (
            "fact-proposed-known",
            setting_id,
            "has_trait",
            "ancient",
            "proposed",
            "known",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    row = migrated_conn.execute(
        "SELECT canon_state, knowledge_state FROM facts WHERE fact_id = ?",
        ("fact-proposed-known",),
    ).fetchone()
    assert row["canon_state"] == "proposed"
    assert row["knowledge_state"] == "known"
