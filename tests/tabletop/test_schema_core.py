"""Tests for migration 0001 core campaign schema."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from tabletop.storage.sqlite import connect, migrate

_CORE_TABLES = ("settings", "campaigns", "sessions", "scenes", "entities")

_FORBIDDEN_COLUMN_PATTERNS = (
    re.compile(r"hit[_\s]?points?", re.IGNORECASE),
    re.compile(r"armor[_\s]?class", re.IGNORECASE),
    re.compile(r"level", re.IGNORECASE),
    re.compile(r"(^|_)class($|_)", re.IGNORECASE),
    re.compile(r"^class$", re.IGNORECASE),
)


def _column_names(conn: sqlite3.Connection) -> list[str]:
    names: list[str] = []
    for table in _CORE_TABLES:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        names.extend(row["name"] for row in rows)
    return names


def _seed_setting_and_campaign(conn: sqlite3.Connection) -> tuple[str, str, str]:
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
    return "setting-1", "campaign-1", "entity-1"


@pytest.fixture
def migrated_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "schema.db")
    migrate(conn)
    yield conn
    conn.close()


def test_migrate_creates_core_tables(migrated_conn: sqlite3.Connection) -> None:
    rows = migrated_conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    table_names = {row["name"] for row in rows}
    for table in _CORE_TABLES:
        assert table in table_names


def test_session_insert_requires_existing_campaign(tmp_path: Path) -> None:
    conn = connect(tmp_path / "schema.db")
    try:
        migrate(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sessions (session_id, campaign_id, started_at) "
                "VALUES (?, ?, ?)",
                ("session-1", "missing-campaign", "2026-01-01T00:00:00+00:00"),
            )
    finally:
        conn.close()


def test_entities_system_state_accepts_json_blob(
    migrated_conn: sqlite3.Connection,
) -> None:
    _, campaign_id, entity_id = _seed_setting_and_campaign(migrated_conn)
    blob = {"custom": {"nested": [1, 2, 3]}, "flag": True}
    migrated_conn.execute(
        "INSERT INTO entities "
        "(entity_id, owner_scope, campaign_id, name, system_state) "
        "VALUES (?, 'campaign', ?, ?, ?)",
        (entity_id, campaign_id, "Blob Entity", json.dumps(blob)),
    )
    row = migrated_conn.execute(
        "SELECT system_state FROM entities WHERE entity_id = ? AND campaign_id = ?",
        (entity_id, campaign_id),
    ).fetchone()
    assert json.loads(row["system_state"]) == blob


def test_schema_has_no_game_specific_columns(migrated_conn: sqlite3.Connection) -> None:
    for column in _column_names(migrated_conn):
        for pattern in _FORBIDDEN_COLUMN_PATTERNS:
            assert not pattern.search(column), (
                f"column {column!r} matches forbidden pattern {pattern.pattern!r}"
            )


def test_setting_scoped_entity_stores_without_campaign_id(
    migrated_conn: sqlite3.Connection,
) -> None:
    setting_id, _, entity_id = _seed_setting_and_campaign(migrated_conn)
    migrated_conn.execute(
        "INSERT INTO entities "
        "(entity_id, owner_scope, setting_id, name) "
        "VALUES (?, 'setting', ?, ?)",
        (entity_id, setting_id, "Setting Entity"),
    )
    row = migrated_conn.execute(
        "SELECT setting_id, campaign_id FROM entities "
        "WHERE entity_id = ? AND owner_scope = 'setting'",
        (entity_id,),
    ).fetchone()
    assert row["setting_id"] == setting_id
    assert row["campaign_id"] is None


def test_campaign_scoped_entity_stores_with_campaign_id(
    migrated_conn: sqlite3.Connection,
) -> None:
    _, campaign_id, entity_id = _seed_setting_and_campaign(migrated_conn)
    migrated_conn.execute(
        "INSERT INTO entities "
        "(entity_id, owner_scope, campaign_id, name) "
        "VALUES (?, 'campaign', ?, ?)",
        (entity_id, campaign_id, "Campaign Entity"),
    )
    row = migrated_conn.execute(
        "SELECT campaign_id FROM entities "
        "WHERE entity_id = ? AND owner_scope = 'campaign'",
        (entity_id,),
    ).fetchone()
    assert row["campaign_id"] == campaign_id


def test_setting_scoped_entity_rejects_campaign_id(
    migrated_conn: sqlite3.Connection,
) -> None:
    setting_id, campaign_id, entity_id = _seed_setting_and_campaign(migrated_conn)
    with pytest.raises(sqlite3.IntegrityError):
        migrated_conn.execute(
            "INSERT INTO entities "
            "(entity_id, owner_scope, setting_id, campaign_id, name) "
            "VALUES (?, 'setting', ?, ?, ?)",
            (entity_id, setting_id, campaign_id, "Bad Setting Entity"),
        )


def test_same_entity_id_once_per_owner_with_override_shadow(
    migrated_conn: sqlite3.Connection,
) -> None:
    setting_id, campaign_id, entity_id = _seed_setting_and_campaign(migrated_conn)
    migrated_conn.execute(
        "INSERT INTO entities "
        "(entity_id, owner_scope, setting_id, name) "
        "VALUES (?, 'setting', ?, ?)",
        (entity_id, setting_id, "Base Deity"),
    )
    migrated_conn.execute(
        "INSERT INTO entities "
        "(entity_id, owner_scope, campaign_id, overrides_id, name) "
        "VALUES (?, 'campaign', ?, ?, ?)",
        (entity_id, campaign_id, entity_id, "Campaign Shadow"),
    )

    setting_row = migrated_conn.execute(
        "SELECT name FROM entities WHERE owner_scope = 'setting' AND entity_id = ?",
        (entity_id,),
    ).fetchone()
    campaign_row = migrated_conn.execute(
        "SELECT name, overrides_id FROM entities "
        "WHERE owner_scope = 'campaign' AND entity_id = ?",
        (entity_id,),
    ).fetchone()
    assert setting_row["name"] == "Base Deity"
    assert campaign_row["name"] == "Campaign Shadow"
    assert campaign_row["overrides_id"] == entity_id

    with pytest.raises(sqlite3.IntegrityError):
        migrated_conn.execute(
            "INSERT INTO entities "
            "(entity_id, owner_scope, setting_id, name) "
            "VALUES (?, 'setting', ?, ?)",
            (entity_id, setting_id, "Duplicate Setting Entity"),
        )

    with pytest.raises(sqlite3.IntegrityError):
        migrated_conn.execute(
            "INSERT INTO entities "
            "(entity_id, owner_scope, campaign_id, name) "
            "VALUES (?, 'campaign', ?, ?)",
            (entity_id, campaign_id, "Duplicate Campaign Entity"),
        )
