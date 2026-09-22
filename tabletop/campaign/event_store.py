"""Append-only event store.

Immutable events with a per-campaign sequence; state projections derive from
events where practical. Events are never rewritten or deleted.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from tabletop.api.events import GameEvent
from tabletop.storage.sqlite import transaction


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
