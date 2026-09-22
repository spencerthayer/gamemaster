"""Append-only event store.

Immutable events with a per-campaign sequence; state projections derive from
events where practical. Events are never rewritten or deleted.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence, assert_never

from tabletop.api.actions import GameAction
from tabletop.api.events import GameEvent
from tabletop.api.resolution import ResolutionStatus, RollResult, StateChange
from tabletop.api.rules import RuleReference
from tabletop.campaign.invariants import detach, promote, reveal
from tabletop.campaign.models import Fact
from tabletop.storage.sqlite import transaction


class EventType(str, Enum):
    """Closed set of campaign event type strings."""

    FACT_PROPOSED = "fact.proposed"
    FACT_PROMOTED = "fact.promoted"
    FACT_REVEALED = "fact.revealed"
    FACT_DETACHED = "fact.detached"
    PROVENANCE_PURGED = "provenance.purged"
    CANON_CONTRADICTION_DETECTED = "canon.contradiction_detected"
    ACTION_RESOLVED = "action.resolved"
    RULING_RECORDED = "ruling.recorded"
    SCENE_OPENED = "scene.opened"
    SCENE_CLOSED = "scene.closed"
    SESSION_STARTED = "session.started"
    SESSION_ENDED = "session.ended"


def event_domain(event_type: EventType) -> str:
    """Classify an event type for dispatch and exhaustive coverage checks."""

    match event_type:
        case (
            EventType.FACT_PROPOSED
            | EventType.FACT_PROMOTED
            | EventType.FACT_REVEALED
            | EventType.FACT_DETACHED
            | EventType.CANON_CONTRADICTION_DETECTED
        ):
            return "canon"
        case EventType.PROVENANCE_PURGED:
            return "provenance"
        case (
            EventType.ACTION_RESOLVED
            | EventType.RULING_RECORDED
            | EventType.SCENE_OPENED
            | EventType.SCENE_CLOSED
            | EventType.SESSION_STARTED
            | EventType.SESSION_ENDED
        ):
            return "play"
        case _:
            assert_never(event_type)


def action_resolved_payload(
    *,
    action: GameAction,
    status: ResolutionStatus,
    outcome: Mapping[str, Any],
    rolls: Sequence[RollResult],
    state_changes: Sequence[StateChange],
    rule_references: Sequence[RuleReference],
) -> dict[str, Any]:
    """Serialize the exact resolution data carried by ``action.resolved``.

    There is no separate ``state.changed`` event. Replay rebuilds
    ``system_state`` from ``state_changes`` in this payload.
    """

    return {
        "action": action.to_dict(),
        "status": status.value if isinstance(status, ResolutionStatus) else status,
        "outcome": dict(outcome),
        "rolls": [roll.to_dict() for roll in rolls],
        "state_changes": [change.to_dict() for change in state_changes],
        "rule_references": [ref.to_dict() for ref in rule_references],
    }


@dataclass(frozen=True)
class PersistedEvent:
    """A decoded event-store row."""

    campaign_id: str
    sequence: int
    event_type: str
    session_id: str | None
    scene_id: str | None
    actor_id: str | None
    target_id: str | None
    payload: Mapping[str, Any]
    occurred_at: str


class EventStore:
    """Append and read immutable campaign events."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def append(
        self,
        campaign_id: str,
        event: GameEvent,
        *,
        session_id: str | None = None,
        scene_id: str | None = None,
        occurred_at: str | None = None,
    ) -> PersistedEvent:
        with transaction(self.conn):
            return self.append_in_transaction(
                self.conn,
                campaign_id,
                event,
                session_id=session_id,
                scene_id=scene_id,
                occurred_at=occurred_at,
            )

    def append_in_transaction(
        self,
        conn: sqlite3.Connection,
        campaign_id: str,
        event: GameEvent,
        *,
        session_id: str | None = None,
        scene_id: str | None = None,
        occurred_at: str | None = None,
    ) -> PersistedEvent:
        """Append using a transaction already owned by the caller."""

        if occurred_at is None:
            occurred_at = datetime.now(timezone.utc).isoformat()
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 "
            "FROM events WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        sequence = int(row[0])
        payload = event.to_dict()["payload"]
        conn.execute(
            "INSERT INTO events "
            "(campaign_id, sequence, event_type, session_id, scene_id, actor_id, "
            "target_id, payload, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                campaign_id,
                sequence,
                event.event_type,
                session_id,
                scene_id,
                None if event.actor is None else event.actor.id,
                None if event.target is None else event.target.id,
                json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
                occurred_at,
            ),
        )
        return PersistedEvent(
            campaign_id=campaign_id,
            sequence=sequence,
            event_type=event.event_type,
            session_id=session_id,
            scene_id=scene_id,
            actor_id=None if event.actor is None else event.actor.id,
            target_id=None if event.target is None else event.target.id,
            payload=payload,
            occurred_at=occurred_at,
        )

    def read(
        self,
        campaign_id: str,
        *,
        since: int = 0,
        limit: int | None = None,
    ) -> list[PersistedEvent]:
        sql = (
            "SELECT campaign_id, sequence, event_type, session_id, scene_id, "
            "actor_id, target_id, payload, occurred_at FROM events "
            "WHERE campaign_id = ? AND sequence > ? ORDER BY sequence"
        )
        parameters: tuple[Any, ...] = (campaign_id, since)
        if limit is not None:
            sql += " LIMIT ?"
            parameters += (limit,)
        rows = self.conn.execute(sql, parameters).fetchall()
        return [_event_from_row(row) for row in rows]


def promote_fact(conn: sqlite3.Connection, fact: Fact) -> Fact:
    """Confirm a fact and append exactly one ``fact.promoted`` event."""

    if fact.campaign_id is None:
        raise ValueError("promote_fact requires a campaign-scoped fact")
    promoted = promote(fact)
    store = EventStore(conn)
    with transaction(conn):
        conn.execute(
            "UPDATE facts SET canon_state = ? WHERE fact_id = ?",
            (promoted.canon_state.value, promoted.fact_id),
        )
        store.append_in_transaction(
            conn,
            fact.campaign_id,
            GameEvent(
                event_type=EventType.FACT_PROMOTED.value,
                payload={"fact_id": fact.fact_id},
            ),
        )
    return promoted


def reveal_fact(conn: sqlite3.Connection, fact: Fact) -> Fact:
    """Mark a fact known and append exactly one ``fact.revealed`` event."""

    if fact.campaign_id is None:
        raise ValueError("reveal_fact requires a campaign-scoped fact")
    revealed = reveal(fact)
    store = EventStore(conn)
    with transaction(conn):
        conn.execute(
            "UPDATE facts SET knowledge_state = ? WHERE fact_id = ?",
            (revealed.knowledge_state.value, revealed.fact_id),
        )
        store.append_in_transaction(
            conn,
            fact.campaign_id,
            GameEvent(
                event_type=EventType.FACT_REVEALED.value,
                payload={"fact_id": fact.fact_id},
            ),
        )
    return revealed


def detach_fact(conn: sqlite3.Connection, fact: Fact) -> Fact:
    """Transfer purge ownership and append exactly one ``fact.detached`` event."""

    if fact.campaign_id is None:
        raise ValueError("detach_fact requires a campaign-scoped fact")
    detached = detach(fact)
    store = EventStore(conn)
    with transaction(conn):
        conn.execute(
            "UPDATE facts SET source_ownership = ? WHERE fact_id = ?",
            (detached.source_ownership, detached.fact_id),
        )
        store.append_in_transaction(
            conn,
            fact.campaign_id,
            GameEvent(
                event_type=EventType.FACT_DETACHED.value,
                payload={"fact_id": fact.fact_id},
            ),
        )
    return detached


def record_contradiction(
    conn: sqlite3.Connection,
    campaign_id: str,
    payload: Mapping[str, Any],
) -> PersistedEvent:
    """Append ``canon.contradiction_detected`` without mutating any facts row."""

    store = EventStore(conn)
    with transaction(conn):
        return store.append_in_transaction(
            conn,
            campaign_id,
            GameEvent(
                event_type=EventType.CANON_CONTRADICTION_DETECTED.value,
                payload=payload,
            ),
        )


def _event_from_row(row: sqlite3.Row) -> PersistedEvent:
    return PersistedEvent(
        campaign_id=row["campaign_id"],
        sequence=row["sequence"],
        event_type=row["event_type"],
        session_id=row["session_id"],
        scene_id=row["scene_id"],
        actor_id=row["actor_id"],
        target_id=row["target_id"],
        payload=json.loads(row["payload"]),
        occurred_at=row["occurred_at"],
    )
