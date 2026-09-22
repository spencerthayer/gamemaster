"""Tests for purging facts owned by source-document provenance."""

from __future__ import annotations

from pathlib import Path

import pytest

from tabletop.campaign.event_store import EventStore, EventType
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState
from tabletop.campaign.store import CampaignStore
from tabletop.documents.provenance import (
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
