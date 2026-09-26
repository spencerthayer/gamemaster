"""Authoritative campaign membership and character control."""

from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from tabletop.api.events import GameEvent
from tabletop.campaign.event_store import EventStore, EventType
from tabletop.storage.sqlite import transaction

PARTICIPANT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ROLES = frozenset({"gm", "player"})
_CONTROLS = frozenset({"owner", "shared", "gm", "temporary"})


def validate_participant_id(participant_id: str) -> str:
    if not PARTICIPANT_ID_PATTERN.fullmatch(participant_id):
        raise ValueError(
            f"participant id {participant_id!r} must match "
            f"{PARTICIPANT_ID_PATTERN.pattern}"
        )
    return participant_id


class MembershipStore:
    """Persist participants, principals, and character controls."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._events = EventStore(conn)

    def add_participant(
        self,
        campaign_id: str,
        participant_id: str,
        display_name: str,
        role: str,
        *,
        created_at: str | None = None,
    ) -> None:
        with transaction(self.conn):
            self.add_participant_in_transaction(
                campaign_id,
                participant_id,
                display_name,
                role,
                created_at=created_at,
            )

    def add_participant_in_transaction(
        self,
        campaign_id: str,
        participant_id: str,
        display_name: str,
        role: str,
        *,
        created_at: str | None = None,
    ) -> None:
        """Insert a participant using a transaction already owned by the caller."""

        participant_id = validate_participant_id(participant_id)
        if role not in _ROLES:
            raise ValueError(f"role must be one of {sorted(_ROLES)}")
        if created_at is None:
            created_at = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO participants "
            "(participant_id, campaign_id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (participant_id, campaign_id, display_name, role, created_at),
        )
        self._events.append_in_transaction(
            self.conn,
            campaign_id,
            GameEvent(
                event_type=EventType.PARTICIPANT_ADDED.value,
                payload={
                    "participant_id": participant_id,
                    "display_name": display_name,
                    "role": role,
                    "created_at": created_at,
                },
            ),
            occurred_at=created_at,
        )

    def remove_participant(self, campaign_id: str, participant_id: str) -> None:
        ended_at = datetime.now(timezone.utc).isoformat()
        with transaction(self.conn):
            active = self.conn.execute(
                "SELECT control_id, entity_id, control FROM character_controls "
                "WHERE campaign_id = ? AND participant_id = ? AND ended_at IS NULL",
                (campaign_id, participant_id),
            ).fetchall()
            for row in active:
                self.conn.execute(
                    "UPDATE character_controls SET ended_at = ? "
                    "WHERE campaign_id = ? AND control_id = ?",
                    (ended_at, campaign_id, row["control_id"]),
                )
                self._events.append_in_transaction(
                    self.conn,
                    campaign_id,
                    GameEvent(
                        event_type=EventType.CHARACTER_CONTROL_ENDED.value,
                        payload={
                            "control_id": row["control_id"],
                            "participant_id": participant_id,
                            "entity_id": row["entity_id"],
                            "control": row["control"],
                            "ended_at": ended_at,
                        },
                    ),
                    occurred_at=ended_at,
                )
            cursor = self.conn.execute(
                "DELETE FROM participants "
                "WHERE campaign_id = ? AND participant_id = ?",
                (campaign_id, participant_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(
                    f"participant {participant_id!r} not found in {campaign_id!r}"
                )
            self._events.append_in_transaction(
                self.conn,
                campaign_id,
                GameEvent(
                    event_type=EventType.PARTICIPANT_REMOVED.value,
                    payload={
                        "participant_id": participant_id,
                        "removed_at": ended_at,
                    },
                ),
                occurred_at=ended_at,
            )

    def bind_principal(
        self,
        campaign_id: str,
        participant_id: str,
        channel: str,
        external_id: str,
        *,
        principal_id: str | None = None,
    ) -> str:
        with transaction(self.conn):
            return self.bind_principal_in_transaction(
                campaign_id,
                participant_id,
                channel,
                external_id,
                principal_id=principal_id,
            )

    def bind_principal_in_transaction(
        self,
        campaign_id: str,
        participant_id: str,
        channel: str,
        external_id: str,
        *,
        principal_id: str | None = None,
    ) -> str:
        """Bind one channel principal using a caller-owned transaction."""

        if principal_id is None:
            principal_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO participant_principals "
            "(principal_id, campaign_id, participant_id, channel, external_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (principal_id, campaign_id, participant_id, channel, external_id),
        )
        return principal_id

    def unbind_principal(
        self,
        campaign_id: str,
        participant_id: str,
        channel: str,
    ) -> None:
        cursor = self.conn.execute(
            "DELETE FROM participant_principals "
            "WHERE campaign_id = ? AND participant_id = ? AND channel = ?",
            (campaign_id, participant_id, channel),
        )
        if cursor.rowcount != 1:
            raise LookupError(
                f"no principal binding for {participant_id!r} on {channel!r}"
            )

    def grant_control(
        self,
        campaign_id: str,
        participant_id: str,
        entity_id: str,
        control: str,
        *,
        control_id: str | None = None,
        created_at: str | None = None,
    ) -> str:
        with transaction(self.conn):
            return self.grant_control_in_transaction(
                campaign_id,
                participant_id,
                entity_id,
                control,
                control_id=control_id,
                created_at=created_at,
            )

    def grant_control_in_transaction(
        self,
        campaign_id: str,
        participant_id: str,
        entity_id: str,
        control: str,
        *,
        control_id: str | None = None,
        created_at: str | None = None,
    ) -> str:
        """Grant control using a transaction already owned by the caller."""

        if control not in _CONTROLS:
            raise ValueError(f"control must be one of {sorted(_CONTROLS)}")
        if control_id is None:
            control_id = str(uuid.uuid4())
        if created_at is None:
            created_at = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO character_controls "
            "(control_id, campaign_id, participant_id, entity_id, control, "
            "created_at, ended_at) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (
                control_id,
                campaign_id,
                participant_id,
                entity_id,
                control,
                created_at,
            ),
        )
        self._events.append_in_transaction(
            self.conn,
            campaign_id,
            GameEvent(
                event_type=EventType.CHARACTER_CONTROL_GRANTED.value,
                payload={
                    "control_id": control_id,
                    "participant_id": participant_id,
                    "entity_id": entity_id,
                    "control": control,
                    "created_at": created_at,
                },
            ),
            occurred_at=created_at,
        )
        return control_id

    def revoke_control(
        self,
        campaign_id: str,
        control_id: str,
        *,
        ended_at: str | None = None,
    ) -> None:
        if ended_at is None:
            ended_at = datetime.now(timezone.utc).isoformat()
        with transaction(self.conn):
            row = self.conn.execute(
                "SELECT participant_id, entity_id, control FROM character_controls "
                "WHERE campaign_id = ? AND control_id = ? AND ended_at IS NULL",
                (campaign_id, control_id),
            ).fetchone()
            if row is None:
                raise LookupError(f"active control {control_id!r} not found")
            self.conn.execute(
                "UPDATE character_controls SET ended_at = ? "
                "WHERE campaign_id = ? AND control_id = ?",
                (ended_at, campaign_id, control_id),
            )
            self._events.append_in_transaction(
                self.conn,
                campaign_id,
                GameEvent(
                    event_type=EventType.CHARACTER_CONTROL_ENDED.value,
                    payload={
                        "control_id": control_id,
                        "participant_id": row["participant_id"],
                        "entity_id": row["entity_id"],
                        "control": row["control"],
                        "ended_at": ended_at,
                    },
                ),
                occurred_at=ended_at,
            )

    def list_participants(self, campaign_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT participant_id, campaign_id, display_name, role, created_at "
            "FROM participants WHERE campaign_id = ? "
            "ORDER BY created_at, participant_id",
            (campaign_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_principals(self, campaign_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT principal_id, campaign_id, participant_id, channel, external_id "
            "FROM participant_principals WHERE campaign_id = ? "
            "ORDER BY channel, external_id",
            (campaign_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_controls(
        self,
        campaign_id: str,
        *,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        if active_only:
            rows = self.conn.execute(
                "SELECT control_id, campaign_id, participant_id, entity_id, control, "
                "created_at, ended_at FROM character_controls "
                "WHERE campaign_id = ? AND ended_at IS NULL "
                "ORDER BY created_at, control_id",
                (campaign_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT control_id, campaign_id, participant_id, entity_id, control, "
                "created_at, ended_at FROM character_controls "
                "WHERE campaign_id = ? ORDER BY created_at, control_id",
                (campaign_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def controlled_entity_ids(
        self,
        campaign_id: str,
        participant_id: str,
    ) -> frozenset[str]:
        rows = self.conn.execute(
            "SELECT entity_id FROM character_controls "
            "WHERE campaign_id = ? AND participant_id = ? AND ended_at IS NULL",
            (campaign_id, participant_id),
        ).fetchall()
        return frozenset(str(row["entity_id"]) for row in rows)
