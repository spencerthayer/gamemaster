"""Tests for purging facts owned by source-document provenance."""

from __future__ import annotations

from pathlib import Path

import pytest

from tabletop.campaign.event_store import EventStore, EventType
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState
from tabletop.campaign.store import CampaignStore
from tabletop.documents.provenance import (
    purge_document,
    purge_facts_for_document,
    purge_facts_for_document_in_transaction,
)
from tabletop.storage.sqlite import connect, migrate, transaction


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "provenance.db")
    migrate(connection)
    connection.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("setting-1", "Test Setting", "2026-09-22T00:00:00Z"),
    )
    CampaignStore(connection).create_campaign(
        "campaign-1",
        "First",
        "test",
        setting_id="setting-1",
    )
    CampaignStore(connection).create_campaign(
        "campaign-2",
        "Second",
        "test",
        setting_id="setting-1",
    )
    try:
        yield connection
    finally:
        connection.close()


def _campaign_fact(
    fact_id: str,
    *,
    campaign_id: str = "campaign-1",
    document_id: str | None = "document-1",
    canon_state: CanonState = CanonState.PROPOSED,
    source_ownership: str = "attached",
) -> Fact:
    return Fact(
        fact_id=fact_id,
        fact_scope=FactScope.CAMPAIGN,
        setting_id=None,
        campaign_id=campaign_id,
        subject_id=None,
        predicate="note",
        value=fact_id,
        canon_state=canon_state,
        knowledge_state=KnowledgeState.UNREVEALED,
        source_document_id=document_id,
        import_job_id=None if document_id is None else "job-1",
        source_ownership=source_ownership,
        created_at="2026-09-22T00:00:00Z",
    )


def _fact_ids(conn) -> list[str]:
    rows = conn.execute("SELECT fact_id FROM facts ORDER BY fact_id").fetchall()
    return [row["fact_id"] for row in rows]


def test_purge_removes_only_attached_facts_for_document(conn) -> None:
    facts = [
        _campaign_fact("imported-proposed"),
        _campaign_fact("imported-confirmed", canon_state=CanonState.CONFIRMED),
        _campaign_fact(
            "imported-detached",
            canon_state=CanonState.CONFIRMED,
            source_ownership="detached",
        ),
        _campaign_fact("hand-authored", document_id=None),
        _campaign_fact("other-document", document_id="document-2"),
    ]
    store = CampaignStore(conn)
    for fact in facts:
        store.add_fact(fact)

    removed = purge_facts_for_document(conn, "document-1")

    assert removed == ["imported-confirmed", "imported-proposed"]
    assert _fact_ids(conn) == [
        "hand-authored",
        "imported-detached",
        "other-document",
    ]
    events = EventStore(conn).read("campaign-1")
    assert len(events) == 1
    assert events[0].event_type == EventType.PROVENANCE_PURGED.value
    assert events[0].payload == {"fact_ids": removed}


def test_in_transaction_form_uses_callers_transaction(conn) -> None:
    CampaignStore(conn).add_fact(_campaign_fact("caller-owned"))
    statements: list[str] = []
    conn.set_trace_callback(statements.append)

    with transaction(conn):
        removed = purge_facts_for_document_in_transaction(conn, "document-1")

    conn.set_trace_callback(None)
    assert removed == ["caller-owned"]
    assert sum(statement == "BEGIN IMMEDIATE" for statement in statements) == 1
    assert _fact_ids(conn) == []
    assert EventStore(conn).read("campaign-1")[0].payload == {
        "fact_ids": ["caller-owned"]
    }


def test_purge_groups_events_by_campaign_and_skips_null_campaign(conn) -> None:
    store = CampaignStore(conn)
    store.add_fact(_campaign_fact("campaign-1-fact"))
    store.add_fact(_campaign_fact("campaign-2-fact", campaign_id="campaign-2"))
    store.add_fact(
        Fact(
            fact_id="setting-fact",
            fact_scope=FactScope.SETTING,
            setting_id="setting-1",
            campaign_id=None,
            subject_id=None,
            predicate="note",
            value="setting-fact",
            source_document_id="document-1",
            import_job_id="job-1",
            source_ownership="attached",
            created_at="2026-09-22T00:00:00Z",
        )
    )

    removed = purge_facts_for_document(conn, "document-1")

    assert removed == ["campaign-1-fact", "campaign-2-fact", "setting-fact"]
    assert _fact_ids(conn) == []
    assert EventStore(conn).read("campaign-1")[0].payload == {
        "fact_ids": ["campaign-1-fact"]
    }
    assert EventStore(conn).read("campaign-2")[0].payload == {
        "fact_ids": ["campaign-2-fact"]
    }


def _seed_documents(conn) -> None:
    for document_id, content_hash in (
        ("document-1", "hash-document-1"),
        ("document-2", "hash-document-2"),
    ):
        conn.execute(
            "INSERT INTO documents "
            "(document_id, content_hash, source_path, title, document_shape, "
            "content_pack_id, system_id, visibility, ingested_at) "
            "VALUES (?, ?, ?, ?, 'prose', NULL, NULL, 'GM', ?)",
            (
                document_id,
                content_hash,
                f"/tmp/{document_id}.md",
                document_id,
                "2026-09-22T00:00:00Z",
            ),
        )
        conn.execute(
            "INSERT INTO document_chunks "
            "(chunk_id, document_id, ordinal, heading_path, page, text, "
            "content_hash, content_pack_id, system_id, visibility) "
            "VALUES (?, ?, 0, ?, NULL, ?, ?, NULL, NULL, 'GM')",
            (
                f"{document_id}-chunk-0",
                document_id,
                '["Root"]',
                f"text for {document_id}",
                f"chunk-{content_hash}",
            ),
        )


def test_purge_document_removes_attached_facts_chunks_and_document(conn) -> None:
    _seed_documents(conn)
    store = CampaignStore(conn)
    store.add_fact(_campaign_fact("imported-attached"))
    store.add_fact(
        _campaign_fact(
            "imported-detached",
            canon_state=CanonState.CONFIRMED,
            source_ownership="detached",
        )
    )
    store.add_fact(_campaign_fact("other-document", document_id="document-2"))

    removed = purge_document(conn, "document-1")

    assert removed == ["imported-attached"]
    assert _fact_ids(conn) == ["imported-detached", "other-document"]
    assert (
        conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE document_id = ?",
            ("document-1",),
        ).fetchone()["n"]
        == 0
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS n FROM document_chunks WHERE document_id = ?",
            ("document-1",),
        ).fetchone()["n"]
        == 0
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE document_id = ?",
            ("document-2",),
        ).fetchone()["n"]
        == 1
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS n FROM document_chunks WHERE document_id = ?",
            ("document-2",),
        ).fetchone()["n"]
        == 1
    )

    events = EventStore(conn).read("campaign-1")
    assert [event.event_type for event in events] == [
        EventType.PROVENANCE_PURGED.value,
        EventType.DOCUMENT_PURGED.value,
    ]
    assert events[0].payload == {"fact_ids": removed}
    assert events[1].payload == {"fact_ids": removed, "document_id": "document-1"}


def test_purge_document_is_one_transaction_and_rolls_back_document(conn) -> None:
    _seed_documents(conn)
    CampaignStore(conn).add_fact(_campaign_fact("caller-owned"))
    conn.execute(
        "CREATE TEMP TRIGGER fail_document_delete "
        "BEFORE DELETE ON documents BEGIN "
        "SELECT RAISE(ABORT, 'document delete failed'); "
        "END"
    )

    with pytest.raises(Exception, match="document delete failed"):
        purge_document(conn, "document-1")

    assert _fact_ids(conn) == ["caller-owned"]
    assert (
        conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE document_id = ?",
            ("document-1",),
        ).fetchone()["n"]
        == 1
    )
    assert EventStore(conn).read("campaign-1") == []
