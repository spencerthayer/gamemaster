"""Tests for the append-only SQLite event store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.api.entities import EntityRef
from tabletop.api.events import GameEvent
from tabletop.campaign.event_store import (
    CURRENT_EVENT_SCHEMA_VERSION,
    EventStore,
    PersistedEvent,
)
from tabletop.campaign.projections import project_campaign
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate, transaction


@pytest.fixture
def event_store(tmp_path: Path) -> EventStore:
    conn = connect(tmp_path / "events.db")
    migrate(conn)
    campaign_store = CampaignStore(conn)
    campaign_store.create_campaign("campaign-1", "First", "test")
    campaign_store.create_campaign("campaign-2", "Second", "test")
    result = EventStore(conn)
    try:
        yield result
    finally:
        conn.close()


def _event(label: str) -> GameEvent:
    return GameEvent(
        event_type=f"test.{label}",
        payload={"label": label},
        actor=EntityRef("actor-1"),
        target=EntityRef("target-1"),
    )


def test_append_assigns_independent_monotonic_campaign_sequences(
    event_store: EventStore,
) -> None:
    first = event_store.append("campaign-1", _event("first"))
    other_first = event_store.append("campaign-2", _event("other-first"))
    second = event_store.append("campaign-1", _event("second"))
    other_second = event_store.append("campaign-2", _event("other-second"))
    third = event_store.append("campaign-1", _event("third"))

    assert [first.sequence, second.sequence, third.sequence] == [1, 2, 3]
    assert [other_first.sequence, other_second.sequence] == [1, 2]


def test_append_in_transaction_uses_caller_owned_transaction(
    event_store: EventStore,
) -> None:
    statements: list[str] = []
    event_store.conn.set_trace_callback(statements.append)

    with transaction(event_store.conn):
        persisted = event_store.append_in_transaction(
            event_store.conn,
            "campaign-1",
            _event("transactional"),
            session_id="session-1",
            scene_id="scene-1",
            occurred_at="2026-09-22T01:00:00+00:00",
        )

    event_store.conn.set_trace_callback(None)
    assert sum(statement.startswith("BEGIN") for statement in statements) == 1
    assert persisted == PersistedEvent(
        campaign_id="campaign-1",
        sequence=1,
        event_type="test.transactional",
        session_id="session-1",
        scene_id="scene-1",
        actor_id="actor-1",
        target_id="target-1",
        payload={"label": "transactional"},
        occurred_at="2026-09-22T01:00:00+00:00",
        event_schema_version=1,
    )


def test_append_opens_one_transaction(event_store: EventStore) -> None:
    statements: list[str] = []
    event_store.conn.set_trace_callback(statements.append)

    event_store.append("campaign-1", _event("standalone"))

    event_store.conn.set_trace_callback(None)
    assert sum(statement.startswith("BEGIN") for statement in statements) == 1


def test_read_filters_by_sequence_orders_and_limits(event_store: EventStore) -> None:
    for label in ("first", "second", "third"):
        event_store.append("campaign-1", _event(label))

    result = event_store.read("campaign-1", since=1, limit=1)

    assert [event.sequence for event in result] == [2]
    assert result[0].payload == {"label": "second"}


def test_events_are_immutable_and_prevent_campaign_deletion(
    event_store: EventStore,
) -> None:
    event_store.append("campaign-1", _event("immutable"))

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        event_store.conn.execute(
            "UPDATE events SET event_type = ? WHERE campaign_id = ?",
            ("changed", "campaign-1"),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        event_store.conn.execute(
            "DELETE FROM events WHERE campaign_id = ?", ("campaign-1",)
        )
    with pytest.raises(sqlite3.IntegrityError):
        event_store.conn.execute(
            "DELETE FROM campaigns WHERE campaign_id = ?", ("campaign-1",)
        )


def test_new_appends_store_schema_generation_1(event_store: EventStore) -> None:
    persisted = event_store.append("campaign-1", _event("versioned"))
    assert persisted.event_schema_version == CURRENT_EVENT_SCHEMA_VERSION == 1
    assert "event_schema_version" not in GameEvent.__dataclass_fields__
    reread = event_store.read("campaign-1")
    assert reread[0].event_schema_version == 1


def test_legacy_rows_stay_generation_0(event_store: EventStore) -> None:
    event_store.conn.execute(
        "INSERT INTO events "
        "(campaign_id, sequence, event_type, payload, occurred_at, event_schema_version) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "campaign-1",
            1,
            "fact.proposed",
            "{}",
            "2026-09-22T00:00:00Z",
            0,
        ),
    )
    assert event_store.read("campaign-1")[0].event_schema_version == 0


def test_unknown_schema_generation_is_rejected() -> None:
    with pytest.raises(ValueError, match="schema generation"):
        project_campaign(
            (
                PersistedEvent(
                    campaign_id="campaign-1",
                    sequence=1,
                    event_type="canon.contradiction_detected",
                    session_id=None,
                    scene_id=None,
                    actor_id=None,
                    target_id=None,
                    payload={},
                    occurred_at="2026-09-22T00:00:00Z",
                    event_schema_version=99,
                ),
            )
        )

