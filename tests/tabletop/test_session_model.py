"""Tests for bounded session records and the closing checklist."""

from __future__ import annotations

import json
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
        GameEvent(
            event_type="session.started",
            payload={
                "session_id": "session-1",
                "started_at": "2026-09-22T01:00:00+00:00",
            },
        ),
        session_id="session-1",
        occurred_at="2026-09-22T01:00:00+00:00",
    )
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


def _stop_before(target: EndSessionStep):
    def observer(step: EndSessionStep) -> None:
        if step is target:
            raise RuntimeError(f"stop before {target.value}")

    return observer


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
        "event_range": {"start": 1, "end": 5},
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


def test_close_event_range_step_persists_actual_bounds(
    session_database: tuple[sqlite3.Connection, Path],
) -> None:
    connection, projection_directory = session_database
    lifecycle = SessionLifecycle(
        connection,
        projection_directory,
        step_observer=_stop_before(EndSessionStep.WRITE_SUMMARY),
    )

    with pytest.raises(RuntimeError, match="stop before write_summary"):
        lifecycle.end_session(
            "campaign-1",
            ended_at="2026-09-22T03:00:00+00:00",
        )

    row = connection.execute(
        "SELECT ended_at, event_start_sequence, event_end_sequence, checklist_step "
        "FROM sessions WHERE session_id = 'session-1'"
    ).fetchone()
    assert dict(row) == {
        "ended_at": "2026-09-22T03:00:00+00:00",
        "event_start_sequence": 1,
        "event_end_sequence": 5,
        "checklist_step": 1,
    }


def test_write_summary_step_persists_summary(
    session_database: tuple[sqlite3.Connection, Path],
) -> None:
    connection, projection_directory = session_database
    lifecycle = SessionLifecycle(
        connection,
        projection_directory,
        step_observer=_stop_before(EndSessionStep.REGENERATE_PROJECTIONS),
    )

    with pytest.raises(RuntimeError, match="stop before regenerate_projections"):
        lifecycle.end_session(
            "campaign-1",
            summary_provider=lambda _: "Focused summary",
        )

    row = connection.execute(
        "SELECT summary, checklist_step FROM sessions "
        "WHERE session_id = 'session-1'"
    ).fetchone()
    assert dict(row) == {"summary": "Focused summary", "checklist_step": 2}


def test_regenerate_projections_step_writes_projection(
    session_database: tuple[sqlite3.Connection, Path],
) -> None:
    connection, projection_directory = session_database
    lifecycle = SessionLifecycle(
        connection,
        projection_directory,
        step_observer=_stop_before(EndSessionStep.UPDATE_RETRIEVAL),
    )

    with pytest.raises(RuntimeError, match="stop before update_retrieval"):
        lifecycle.end_session("campaign-1")

    assert (projection_directory / "campaign.yaml").is_file()
    checklist_step = connection.execute(
        "SELECT checklist_step FROM sessions WHERE session_id = 'session-1'"
    ).fetchone()[0]
    assert checklist_step == 3


def test_update_retrieval_step_indexes_session(
    session_database: tuple[sqlite3.Connection, Path],
) -> None:
    connection, projection_directory = session_database

    SessionLifecycle(connection, projection_directory).end_session("campaign-1")

    row = connection.execute(
        "SELECT document_id FROM fts_campaign "
        "WHERE chunk_id = 'session:session-1'"
    ).fetchone()
    checklist_step = connection.execute(
        "SELECT checklist_step FROM sessions WHERE session_id = 'session-1'"
    ).fetchone()[0]
    assert row["document_id"] == "session-1"
    assert checklist_step == len(END_SESSION_CHECKLIST)


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
    assert "Event range: 1 through 5" in row["text"]
    assert "The gate was sealed." in row["text"]


@pytest.mark.parametrize(
    ("failed_step", "error_message"),
    [
        (EndSessionStep.REGENERATE_PROJECTIONS, "projection failed"),
        (EndSessionStep.UPDATE_RETRIEVAL, "retrieval failed"),
    ],
)
def test_retry_resumes_after_projection_or_retrieval_failure(
    session_database: tuple[sqlite3.Connection, Path],
    failed_step: EndSessionStep,
    error_message: str,
) -> None:
    connection, projection_directory = session_database
    projection_calls = 0
    retrieval_calls = 0

    def projection_writer(*_: object) -> None:
        nonlocal projection_calls
        projection_calls += 1
        if failed_step is EndSessionStep.REGENERATE_PROJECTIONS:
            raise OSError("projection failed")

    def retrieval_updater(*_: object) -> None:
        nonlocal retrieval_calls
        retrieval_calls += 1
        if failed_step is EndSessionStep.UPDATE_RETRIEVAL:
            raise OSError("retrieval failed")

    lifecycle = SessionLifecycle(
        connection,
        projection_directory,
        projection_writer=projection_writer,
        retrieval_updater=retrieval_updater,
    )
    before = {
        "agendas": _rows(
            connection,
            "SELECT entity_id, system_state FROM entities WHERE entity_type = 'npc'",
        ),
        "rulings": _rows(connection, "SELECT * FROM rulings"),
        "events": _rows(connection, "SELECT * FROM events ORDER BY sequence"),
    }

    with pytest.raises(OSError, match=error_message):
        lifecycle.end_session(
            "campaign-1",
            summary_provider=lambda _: "Durable summary",
            ended_at="2026-09-22T03:00:00+00:00",
        )

    retry = SessionLifecycle(
        connection,
        projection_directory,
        projection_writer=lambda *_: None,
        retrieval_updater=lambda *_: None,
    ).end_session(
        "campaign-1",
        summary_provider=lambda _: "Must not replace the durable summary",
        ended_at="2026-09-22T04:00:00+00:00",
    )

    assert retry.ended_at == "2026-09-22T03:00:00+00:00"
    assert retry.summary == "Durable summary"
    assert retry.event_range.start == 1
    assert retry.event_range.end == 5
    assert projection_calls == 1
    assert retrieval_calls == (
        0 if failed_step is EndSessionStep.REGENERATE_PROJECTIONS else 1
    )
    after_events = _rows(connection, "SELECT * FROM events ORDER BY sequence")
    assert {
        "agendas": _rows(
            connection,
            "SELECT entity_id, system_state FROM entities WHERE entity_type = 'npc'",
        ),
        "rulings": _rows(connection, "SELECT * FROM rulings"),
    } == {
        "agendas": before["agendas"],
        "rulings": before["rulings"],
    }
    assert after_events[:-1] == before["events"]
    assert after_events[-1]["event_type"] == "session.ended"


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
    assert after["agendas"] == before["agendas"]
    assert after["clocks"] == before["clocks"]
    assert after["rulings"] == before["rulings"]
    assert after["events"][:-1] == before["events"]
    assert after["events"][-1]["event_type"] == "session.ended"
    ended_payload = json.loads(after["events"][-1]["payload"])
    assert ended_payload["session_id"] == "session-1"
    assert isinstance(ended_payload["ended_at"], str)


def test_event_range_uses_session_sequence_numbers_not_timestamps(
    session_database: tuple[sqlite3.Connection, Path],
) -> None:
    connection, projection_directory = session_database

    session = SessionLifecycle(connection, projection_directory).end_session(
        "campaign-1",
        summary_provider=lambda _: "Summary",
    )

    assert session.event_range.start == 1
    assert session.event_range.end == 5


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
        "event_start_sequence": 1,
        "event_end_sequence": 5,
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
    assert payload["data"]["session"]["event_range"] == {"start": 1, "end": 5}
