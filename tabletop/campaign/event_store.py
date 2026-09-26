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
from typing import Any, Callable, Mapping, Sequence, assert_never

from tabletop.api.actions import GameAction
from tabletop.api.events import GameEvent
from tabletop.api.resolution import (
    Resolution,
    ResolutionStatus,
    RollResult,
    StateChange,
)
from tabletop.api.rules import RuleReference
from tabletop.campaign.invariants import detach, promote, reveal
from tabletop.campaign.models import Fact
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import transaction


CURRENT_EVENT_SCHEMA_VERSION = 1


class EventType(str, Enum):
    """Closed set of campaign event type strings."""

    FACT_PROPOSED = "fact.proposed"
    FACT_PROMOTED = "fact.promoted"
    FACT_REVEALED = "fact.revealed"
    FACT_DETACHED = "fact.detached"
    PROVENANCE_PURGED = "provenance.purged"
    DOCUMENT_PURGED = "document.purged"
    CANON_CONTRADICTION_DETECTED = "canon.contradiction_detected"
    ACTION_RESOLVED = "action.resolved"
    RULING_RECORDED = "ruling.recorded"
    RULING_PROMOTED = "ruling.promoted"
    SCENE_OPENED = "scene.opened"
    SCENE_CLOSED = "scene.closed"
    SCENE_ENTITY_ENTERED = "scene.entity_entered"
    SCENE_ENTITY_EXITED = "scene.entity_exited"
    SCENE_LOCATION_CHANGED = "scene.location_changed"
    SCENE_TIME_CHANGED = "scene.time_changed"
    SESSION_STARTED = "session.started"
    SESSION_ENDED = "session.ended"
    QUEST_MUTATED = "quest.mutated"
    CAMPAIGN_ARCHIVED = "campaign.archived"
    CAMPAIGN_RESTORED = "campaign.restored"
    PARTICIPANT_ADDED = "participant.added"
    PARTICIPANT_REMOVED = "participant.removed"
    CHARACTER_CONTROL_GRANTED = "character_control.granted"
    CHARACTER_CONTROL_ENDED = "character_control.ended"
    CAMPAIGN_FORKED = "campaign.forked"


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
        case EventType.PROVENANCE_PURGED | EventType.DOCUMENT_PURGED:
            return "provenance"
        case EventType.CAMPAIGN_FORKED:
            return "audit"
        case (
            EventType.ACTION_RESOLVED
            | EventType.RULING_RECORDED
            | EventType.RULING_PROMOTED
            | EventType.SCENE_OPENED
            | EventType.SCENE_CLOSED
            | EventType.SCENE_ENTITY_ENTERED
            | EventType.SCENE_ENTITY_EXITED
            | EventType.SCENE_LOCATION_CHANGED
            | EventType.SCENE_TIME_CHANGED
            | EventType.SESSION_STARTED
            | EventType.SESSION_ENDED
            | EventType.QUEST_MUTATED
            | EventType.CAMPAIGN_ARCHIVED
            | EventType.CAMPAIGN_RESTORED
            | EventType.PARTICIPANT_ADDED
            | EventType.PARTICIPANT_REMOVED
            | EventType.CHARACTER_CONTROL_GRANTED
            | EventType.CHARACTER_CONTROL_ENDED
        ):
            return "play"
        case _:
            assert_never(event_type)


def open_scene_event(
    *,
    scene_id: str,
    name: str,
    started_at: str,
    session_id: str | None = None,
    location_entity_id: str | None = None,
    in_world_started_at: str | None = None,
) -> GameEvent:
    """Build the ``scene.opened`` event for one new scene."""

    return GameEvent(
        event_type=EventType.SCENE_OPENED.value,
        payload={
            "scene_id": scene_id,
            "name": name,
            "session_id": session_id,
            "location_entity_id": location_entity_id,
            "in_world_started_at": in_world_started_at,
            "started_at": started_at,
        },
    )


def close_scene_event(
    *,
    scene_id: str,
    ended_at: str,
    exited_entity_ids: Sequence[str] = (),
    in_world_ended_at: str | None = None,
) -> GameEvent:
    """Build the ``scene.closed`` event, naming everyone the close removed.

    ``exited_entity_ids`` is explicit so replay can reproduce the presence
    rows the close ended without re-deriving them from a default.
    """

    return GameEvent(
        event_type=EventType.SCENE_CLOSED.value,
        payload={
            "scene_id": scene_id,
            "ended_at": ended_at,
            "in_world_ended_at": in_world_ended_at,
            "exited_entity_ids": list(exited_entity_ids),
        },
    )


def entity_entered_event(
    *,
    scene_id: str,
    entity_id: str,
    presence_type: str,
    entered_at: str,
) -> GameEvent:
    """Build the ``scene.entity_entered`` event for one presence interval."""

    return GameEvent(
        event_type=EventType.SCENE_ENTITY_ENTERED.value,
        payload={
            "scene_id": scene_id,
            "entity_id": entity_id,
            "presence_type": presence_type,
            "entered_at": entered_at,
        },
    )


def entity_exited_event(
    *,
    scene_id: str,
    entity_id: str,
    exited_at: str,
) -> GameEvent:
    """Build the ``scene.entity_exited`` event ending one presence interval."""

    return GameEvent(
        event_type=EventType.SCENE_ENTITY_EXITED.value,
        payload={
            "scene_id": scene_id,
            "entity_id": entity_id,
            "exited_at": exited_at,
        },
    )


def scene_location_changed_event(
    *,
    scene_id: str,
    location_entity_id: str | None,
    changed_at: str,
) -> GameEvent:
    """Build the ``scene.location_changed`` event for a move."""

    return GameEvent(
        event_type=EventType.SCENE_LOCATION_CHANGED.value,
        payload={
            "scene_id": scene_id,
            "location_entity_id": location_entity_id,
            "changed_at": changed_at,
        },
    )


def scene_time_changed_event(
    *,
    in_world_label: str | None,
    in_world_minutes: int | None,
    changed_at: str,
) -> GameEvent:
    """Build the ``scene.time_changed`` event for the campaign clock.

    This event carries no ``scene_id``: the clock belongs to the campaign, so
    attributing it to whichever scene happened to be open would misstate it.
    """

    return GameEvent(
        event_type=EventType.SCENE_TIME_CHANGED.value,
        payload={
            "in_world_label": in_world_label,
            "in_world_minutes": in_world_minutes,
            "changed_at": changed_at,
        },
    )


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
    event_schema_version: int = 0


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
            "target_id, payload, occurred_at, event_schema_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                CURRENT_EVENT_SCHEMA_VERSION,
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
            event_schema_version=CURRENT_EVENT_SCHEMA_VERSION,
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
            "actor_id, target_id, payload, occurred_at, event_schema_version "
            "FROM events "
            "WHERE campaign_id = ? AND sequence > ? ORDER BY sequence"
        )
        parameters: tuple[Any, ...] = (campaign_id, since)
        if limit is not None:
            sql += " LIMIT ?"
            parameters += (limit,)
        rows = self.conn.execute(sql, parameters).fetchall()
        return [_event_from_row(row) for row in rows]


def _require_updated_fact_row(cursor: sqlite3.Cursor, fact_id: str) -> None:
    if cursor.rowcount != 1:
        raise LookupError(f"fact not found: {fact_id}")


def promote_fact(conn: sqlite3.Connection, fact: Fact) -> Fact:
    """Confirm a fact and append exactly one ``fact.promoted`` event."""

    if fact.campaign_id is None:
        raise ValueError("promote_fact requires a campaign-scoped fact")
    with transaction(conn):
        return promote_fact_in_transaction(conn, fact)


def promote_fact_in_transaction(conn: sqlite3.Connection, fact: Fact) -> Fact:
    """Confirm a fact inside a caller-owned transaction."""

    if fact.campaign_id is None:
        raise ValueError("promote_fact requires a campaign-scoped fact")
    promoted = promote(fact)
    store = EventStore(conn)
    cursor = conn.execute(
        "UPDATE facts SET canon_state = ? WHERE fact_id = ?",
        (promoted.canon_state.value, promoted.fact_id),
    )
    _require_updated_fact_row(cursor, fact.fact_id)
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
        cursor = conn.execute(
            "UPDATE facts SET knowledge_state = ? WHERE fact_id = ?",
            (revealed.knowledge_state.value, revealed.fact_id),
        )
        _require_updated_fact_row(cursor, fact.fact_id)
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
        cursor = conn.execute(
            "UPDATE facts SET source_ownership = ? WHERE fact_id = ?",
            (detached.source_ownership, detached.fact_id),
        )
        _require_updated_fact_row(cursor, fact.fact_id)
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

    with transaction(conn):
        return record_contradiction_in_transaction(conn, campaign_id, payload)


def record_contradiction_in_transaction(
    conn: sqlite3.Connection,
    campaign_id: str,
    payload: Mapping[str, Any],
) -> PersistedEvent:
    """Append a contradiction event inside a caller-owned transaction."""

    store = EventStore(conn)
    return store.append_in_transaction(
        conn,
        campaign_id,
        GameEvent(
            event_type=EventType.CANON_CONTRADICTION_DETECTED.value,
            payload=payload,
        ),
    )


def apply_resolved_action(
    conn: sqlite3.Connection,
    campaign_id: str,
    action: GameAction,
    resolution: Resolution,
    *,
    scene_id: str | None = None,
) -> PersistedEvent:
    """Append ``action.resolved`` and apply its ``state_changes`` in one transaction."""

    with transaction(conn):
        return apply_resolved_action_in_transaction(
            conn, campaign_id, action, resolution, scene_id=scene_id
        )


def apply_resolved_action_in_transaction(
    conn: sqlite3.Connection,
    campaign_id: str,
    action: GameAction,
    resolution: Resolution,
    *,
    scene_id: str | None = None,
    on_committed: Callable[[PersistedEvent], None] | None = None,
) -> PersistedEvent:
    """Apply one resolved action inside a caller-owned transaction.

    ``on_committed`` runs after the event and its state changes are written but
    before the transaction commits. A durable turn uses it to record the
    effect claim, so the claim, the event, and the state change all land
    together or not at all. A claim written outside this transaction could
    survive a crash while its effect did not, or the reverse.
    """

    payload = action_resolved_payload(
        action=action,
        status=resolution.status,
        outcome=resolution.outcome,
        rolls=resolution.rolls,
        state_changes=resolution.state_changes,
        rule_references=resolution.rule_references,
    )
    event_store = EventStore(conn)
    campaign_store = CampaignStore(conn)
    persisted = event_store.append_in_transaction(
        conn,
        campaign_id,
        GameEvent(
            event_type=EventType.ACTION_RESOLVED.value,
            payload=payload,
            actor=action.actor,
            target=action.targets[0] if action.targets else None,
        ),
        scene_id=scene_id,
    )
    campaign_store.apply_state_changes_in_transaction(
        campaign_id,
        resolution.state_changes,
        scene_id=scene_id,
    )
    if on_committed is not None:
        on_committed(persisted)
    return persisted


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
        event_schema_version=int(row["event_schema_version"]),
    )
