"""Append-only history for setting-owned rows.

Campaign events require a campaign id. Setting mutations use this log instead.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, assert_never

from tabletop.campaign.event_store import CURRENT_EVENT_SCHEMA_VERSION


class SettingEventType(str, Enum):
    """Closed set of setting event type strings."""

    SETTING_EDITED = "setting.edited"
    WORLD_ENTITY_UPSERTED = "world_entity.upserted"
    WORLD_FACT_RECORDED = "world_fact.recorded"


@dataclass(frozen=True)
class PersistedSettingEvent:
    """A decoded setting-event row."""

    setting_id: str
    sequence: int
    event_type: str
    payload: Mapping[str, Any]
    occurred_at: str
    event_schema_version: int


@dataclass(frozen=True)
class SettingProjection:
    """Setting rows rebuilt from setting events."""

    settings: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    entities: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    facts: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


class SettingEventStore:
    """Append and read immutable setting events."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def append_in_transaction(
        self,
        conn: sqlite3.Connection,
        setting_id: str,
        event_type: SettingEventType,
        payload: Mapping[str, Any],
        *,
        occurred_at: str | None = None,
    ) -> PersistedSettingEvent:
        if occurred_at is None:
            occurred_at = datetime.now(timezone.utc).isoformat()
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM setting_events "
            "WHERE setting_id = ?",
            (setting_id,),
        ).fetchone()
        sequence = int(row[0])
        encoded = json.dumps(dict(payload), separators=(",", ":"), ensure_ascii=False)
        conn.execute(
            "INSERT INTO setting_events "
            "(setting_id, sequence, event_schema_version, event_type, payload, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                setting_id,
                sequence,
                CURRENT_EVENT_SCHEMA_VERSION,
                event_type.value,
                encoded,
                occurred_at,
            ),
        )
        return PersistedSettingEvent(
            setting_id=setting_id,
            sequence=sequence,
            event_type=event_type.value,
            payload=dict(payload),
            occurred_at=occurred_at,
            event_schema_version=CURRENT_EVENT_SCHEMA_VERSION,
        )

    def read(self, setting_id: str) -> list[PersistedSettingEvent]:
        rows = self.conn.execute(
            "SELECT setting_id, sequence, event_schema_version, event_type, payload, "
            "occurred_at FROM setting_events WHERE setting_id = ? ORDER BY sequence",
            (setting_id,),
        ).fetchall()
        return [
            PersistedSettingEvent(
                setting_id=row["setting_id"],
                sequence=row["sequence"],
                event_type=row["event_type"],
                payload=json.loads(row["payload"]),
                occurred_at=row["occurred_at"],
                event_schema_version=int(row["event_schema_version"]),
            )
            for row in rows
        ]


def project_setting(events: list[PersistedSettingEvent] | tuple[PersistedSettingEvent, ...]) -> SettingProjection:
    """Fold setting events into a projection. Does not write SQLite."""

    settings: dict[str, dict[str, Any]] = {}
    entities: dict[str, dict[str, Any]] = {}
    facts: dict[str, dict[str, Any]] = {}
    for event in events:
        event_type = SettingEventType(event.event_type)
        match event_type:
            case SettingEventType.SETTING_EDITED:
                settings[event.setting_id] = {
                    "setting_id": event.setting_id,
                    "name": event.payload["name"],
                    "created_at": event.payload.get("created_at"),
                }
            case SettingEventType.WORLD_ENTITY_UPSERTED:
                entity_id = str(event.payload["entity_id"])
                entities[entity_id] = dict(event.payload)
            case SettingEventType.WORLD_FACT_RECORDED:
                fact_id = str(event.payload["fact_id"])
                facts[fact_id] = dict(event.payload)
            case _:
                assert_never(event_type)
    return SettingProjection(settings=settings, entities=entities, facts=facts)
