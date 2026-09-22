"""Tests for bounded session records and the closing checklist."""

from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest

from tabletop.api.events import GameEvent
from tabletop.api.workspace import Workspace
from tabletop.campaign.event_store import EventStore
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.session import (
    END_SESSION_CHECKLIST,
    EndSessionStep,
    SessionLifecycle,
)
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate


@pytest.fixture
def session_database(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    connection = connect(tmp_path / "sessions.db")
    migrate(connection)
    CampaignStore(connection).create_campaign(
        "campaign-1",
        "First Campaign",
        "freeform",
        system_state={"clocks": [{"id": "doom", "ticks": 2}]},
    )
    CampaignStore(connection).upsert_entity(
        "campaign-1",
        "npc-1",
        "The Warden",
        entity_type="npc",
        system_state={"agenda": ["Seal the gate"]},
    )
    connection.execute(
        "INSERT INTO sessions "
        "(session_id, campaign_id, started_at, participants, transcript_reference, "
        "important_facts, open_threads) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "session-1",
            "campaign-1",
            "2026-09-22T01:00:00+00:00",
            '["hero","warden"]',
            "transcripts/session-1.jsonl",
            '["The gate is weakening"]',
            '["Who built the gate?"]',
        ),
    )
    connection.execute(
        "INSERT INTO rulings "
        "(ruling_id, campaign_id, system_id, question, decision, scope, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "ruling-1",
            "campaign-1",
            "freeform",
            "Can the gate be forced?",
            "Yes, with a hard check.",
            "campaign",
            "2026-09-22T01:05:00+00:00",
        ),
    )
    events = EventStore(connection)
    events.append(
        "campaign-1",
        GameEvent(event_type="scene.opened", payload={}),
        occurred_at="1999-01-01T00:00:00+00:00",
    )
    events.append(
        "campaign-1",
        GameEvent(event_type="action.resolved", payload={"state_changes": []}),
        session_id="session-1",
        occurred_at="2099-01-01T00:00:00+00:00",
    )
    events.append(
        "campaign-1",
        GameEvent(event_type="scene.closed", payload={}),
        session_id="session-1",
        occurred_at="1999-01-01T00:00:00+00:00",
    )
    projection_directory = tmp_path / "campaign-1"
    try:
        yield connection, projection_directory
    finally:
        connection.close()


def _rows(connection: sqlite3.Connection, sql: str) -> list[dict[str, object]]:
    return [dict(row) for row in connection.execute(sql).fetchall()]


def test_session_record_contains_complete_lifecycle_data(
    session_database: tuple[sqlite3.Connection, Path],
) -> None:
    connection, projection_directory = session_database
    lifecycle = SessionLifecycle(connection, projection_directory)

    result = lifecycle.end_session(
        "campaign-1",
        summary_provider=lambda _: "The party sealed the gate.",
        ended_at="2026-09-22T03:00:00+00:00",
    )

    assert result.to_dict() == {
        "session_id": "session-1",
        "campaign_id": "campaign-1",
        "started_at": "2026-09-22T01:00:00+00:00",
        "ended_at": "2026-09-22T03:00:00+00:00",
        "participants": ["hero", "warden"],
        "transcript_reference": "transcripts/session-1.jsonl",
        "event_range": {"start": 2, "end": 3},
        "summary": "The party sealed the gate.",
        "important_facts": ["The gate is weakening"],
        "open_threads": ["Who built the gate?"],
    }


def test_closing_checklist_has_explicit_order_and_runs_each_step(
    session_database: tuple[sqlite3.Connection, Path],
) -> None:
    connection, projection_directory = session_database
    calls: list[EndSessionStep] = []

    lifecycle = SessionLifecycle(
        connection,
        projection_directory,
        projection_writer=lambda *_: None,
        retrieval_updater=lambda *_: None,
        step_observer=calls.append,
    )
    lifecycle.end_session(
        "campaign-1",
        summary_provider=lambda _: "Summary",
        ended_at="2026-09-22T03:00:00+00:00",
    )

    observed_steps = [
        step
        for step in calls
        if step
        in {
            EndSessionStep.CLOSE_EVENT_RANGE,
            EndSessionStep.WRITE_SUMMARY,
        }
    ]
    assert END_SESSION_CHECKLIST == (
        EndSessionStep.CLOSE_EVENT_RANGE,
        EndSessionStep.WRITE_SUMMARY,
        EndSessionStep.REGENERATE_PROJECTIONS,
        EndSessionStep.UPDATE_RETRIEVAL,
    )
    assert observed_steps == list(END_SESSION_CHECKLIST[:2])
    assert calls == list(END_SESSION_CHECKLIST)


def test_projection_regeneration_and_retrieval_update_complete(
    session_database: tuple[sqlite3.Connection, Path],
) -> None:
    connection, projection_directory = session_database

    SessionLifecycle(connection, projection_directory).end_session(
        "campaign-1",
        summary_provider=lambda _: "The gate was sealed.",
    )

    assert (projection_directory / "campaign.yaml").is_file()
    row = connection.execute(
        "SELECT document_id, text FROM fts_campaign "
        "WHERE chunk_id = 'session:session-1'"
    ).fetchone()
    assert row["document_id"] == "session-1"
    assert "Event range: 2 through 3" in row["text"]
    assert "The gate was sealed." in row["text"]


def test_end_session_preserves_agendas_clocks_rulings_and_events(
    session_database: tuple[sqlite3.Connection, Path],
) -> None:
    connection, projection_directory = session_database
    before = {
        "agendas": _rows(
            connection,
            "SELECT entity_id, system_state FROM entities WHERE entity_type = 'npc'",
        ),
        "clocks": _rows(
            connection,
            "SELECT campaign_id, system_state FROM campaigns",
        ),
        "rulings": _rows(connection, "SELECT * FROM rulings"),
        "events": _rows(connection, "SELECT * FROM events ORDER BY sequence"),
    }

    SessionLifecycle(connection, projection_directory).end_session(
        "campaign-1",
        summary_provider=lambda _: "Summary",
    )

    after = {
        "agendas": _rows(
            connection,
            "SELECT entity_id, system_state FROM entities WHERE entity_type = 'npc'",
        ),
        "clocks": _rows(
            connection,
            "SELECT campaign_id, system_state FROM campaigns",
        ),
        "rulings": _rows(connection, "SELECT * FROM rulings"),
        "events": _rows(connection, "SELECT * FROM events ORDER BY sequence"),
    }
    assert after == before


def test_event_range_uses_session_sequence_numbers_not_timestamps(
    session_database: tuple[sqlite3.Connection, Path],
) -> None:
    connection, projection_directory = session_database

    session = SessionLifecycle(connection, projection_directory).end_session(
        "campaign-1",
        summary_provider=lambda _: "Summary",
    )

    assert session.event_range.start == 2
    assert session.event_range.end == 3


@pytest.mark.parametrize("failure", [None, RuntimeError("model unavailable")])
def test_missing_or_failed_summary_keeps_authoritative_event_range(
    session_database: tuple[sqlite3.Connection, Path],
    failure: RuntimeError | None,
) -> None:
    connection, projection_directory = session_database

    def summarize(_: object) -> None:
        if failure is not None:
            raise failure
        return None

    session = SessionLifecycle(connection, projection_directory).end_session(
        "campaign-1",
        summary_provider=summarize,
    )
    row = connection.execute(
        "SELECT summary, event_start_sequence, event_end_sequence "
        "FROM sessions WHERE session_id = 'session-1'"
    ).fetchone()

    assert session.summary is None
    assert dict(row) == {
        "summary": None,
        "event_start_sequence": 2,
        "event_end_sequence": 3,
    }


def test_runtime_end_session_uses_session_lifecycle(
    session_database: tuple[sqlite3.Connection, Path],
    tmp_path: Path,
) -> None:
    connection, _ = session_database
    campaign_root = tmp_path / "campaigns"
    (campaign_root / "campaign-1").mkdir(parents=True)
    runtime = TabletopRuntime(
        Path(__file__).parents[2],
        workspace=Workspace.CAMPAIGN,
        campaign_roots=[campaign_root],
        active_campaign="campaign-1",
        connection=connection,
    )

    payload = runtime.end_session()

    assert payload["ok"] is True
    assert payload["operation"] == "end-session"
    assert payload["data"]["session"]["event_range"] == {"start": 2, "end": 3}
