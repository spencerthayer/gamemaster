"""Advisory contradiction detection tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.api.events import GameEvent
from tabletop.campaign.contradictions import Claim, FactSource, check_claim
from tabletop.campaign.event_store import EventStore, EventType
from tabletop.campaign.models import CanonState, Fact, FactScope
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "contradictions.db")
    migrate(connection)
    store = CampaignStore(connection)
    store.create_campaign("campaign-1", "First", "freeform")
    store.upsert_entity("campaign-1", "warden", "The Warden", entity_type="npc")
    try:
        yield connection
    finally:
        connection.close()


def _rows(conn: sqlite3.Connection, table: str) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
    ]


def _establish_fact(conn: sqlite3.Connection) -> Fact:
    fact = Fact(
        fact_id="fact-1",
        fact_scope=FactScope.CAMPAIGN,
        setting_id=None,
        campaign_id="campaign-1",
        subject_id="warden",
        predicate="home",
        value="North Gate",
        canon_state=CanonState.CONFIRMED,
        source_document_id="document-1",
        source_chunk_id="chunk-7",
        import_job_id="job-2",
        extraction_method="manual",
        created_at="2026-09-22T01:00:00Z",
    )
    CampaignStore(conn).add_fact(fact)
    EventStore(conn).append(
        "campaign-1",
        GameEvent(
            event_type=EventType.FACT_PROPOSED.value,
            payload={"fact_id": fact.fact_id},
        ),
        session_id="session-4",
    )
    return fact


def test_conflicting_claim_returns_gm_advisory_without_rewriting_canon(conn) -> None:
    fact = _establish_fact(conn)
    authoritative_before = {
        table: _rows(conn, table)
        for table in ("facts", "entities", "relationships")
    }

    conflict = check_claim(
        conn,
        Claim(
            campaign_id="campaign-1",
            subject_id="warden",
            predicate="home",
            value="South Gate",
            session_id="session-9",
        ),
    )

    assert conflict is not None
    assert conflict.existing_fact == fact
    assert conflict.source == FactSource(
        document_id="document-1",
        chunk_id="chunk-7",
        import_job_id="job-2",
        extraction_method="manual",
    )
    assert conflict.session == "session-4"
    assert conflict.reason == (
        "Established canon has home='North Gate'; the claim proposes 'South Gate'."
    )
    assert conflict.visibility == "GM"
    assert conflict.resolved is False
    assert {
        table: _rows(conn, table)
        for table in ("facts", "entities", "relationships")
    } == authoritative_before

    events = EventStore(conn).read("campaign-1")
    assert [event.event_type for event in events] == [
        EventType.FACT_PROPOSED.value,
        EventType.CANON_CONTRADICTION_DETECTED.value,
    ]
    assert events[-1].payload == {
        "claim": {
            "subject_id": "warden",
            "predicate": "home",
            "value": "South Gate",
        },
        "existing_fact_id": "fact-1",
        "existing_value": "North Gate",
        "reason": conflict.reason,
        "visibility": "GM",
        "resolved": False,
        "claim_session_id": "session-9",
        "existing_session_id": "session-4",
    }


def test_matching_claim_returns_no_conflict_and_writes_nothing(conn) -> None:
    _establish_fact(conn)
    events_before = EventStore(conn).read("campaign-1")

    result = check_claim(
        conn,
        Claim(
            campaign_id="campaign-1",
            subject_id="warden",
            predicate="home",
            value="North Gate",
        ),
    )

    assert result is None
    assert EventStore(conn).read("campaign-1") == events_before


def test_unknown_subject_returns_no_conflict(conn) -> None:
    _establish_fact(conn)
    events_before = EventStore(conn).read("campaign-1")

    result = check_claim(
        conn,
        Claim(
            campaign_id="campaign-1",
            subject_id="unknown",
            predicate="home",
            value="South Gate",
        ),
    )

    assert result is None
    assert EventStore(conn).read("campaign-1") == events_before
