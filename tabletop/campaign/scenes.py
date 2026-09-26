"""Authoritative scene, presence, and campaign-clock persistence.

This is the single store boundary for scene invariants. Every mutation runs
inside ``transaction(self.conn)`` and emits nothing: the caller that owns the
campaign event log appends the matching event in the same transaction via the
``*_in_transaction`` variants. No other module may write these tables.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from tabletop.campaign.models import (
    GameTime,
    PresenceType,
    Scene,
    SceneInvariantError,
    SceneMember,
    SceneStatus,
)
from tabletop.storage.sqlite import transaction


def _now() -> str:
    return _stamp(datetime.now(timezone.utc))


def _stamp(moment: datetime) -> str:
    """Render one instant in the single canonical form the schema compares.

    The ``ended_at >= started_at`` and ``exited_at >= entered_at`` CHECK
    constraints compare text. Every timestamp this store writes therefore uses
    ``Z`` with no sub-second component, so two representations of the same
    moment can never sort differently in SQLite.
    """

    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _canonical(value: str, *, field_name: str) -> str:
    """Normalize a caller-supplied timestamp to the canonical stored form."""

    return _stamp(_instant(value, field_name=field_name))


def _instant(value: str, *, field_name: str) -> datetime:
    """Parse an ISO-8601 instant for comparison.

    Bounds are compared as instants, not as strings: ``Z`` and ``+00:00`` are
    the same moment but different text, so a string compare would order them
    wrongly and let a scene appear to end before it started.
    """

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SceneInvariantError(
            f"{field_name} must be an ISO-8601 timestamp, got {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise SceneInvariantError(
            f"{field_name} must carry a UTC offset, got {value!r}"
        )
    return parsed


class SceneStore:
    """Read and write authoritative scene, presence, and clock records."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -- reads ------------------------------------------------------------

    def get_scene(self, campaign_id: str, scene_id: str) -> Scene | None:
        row = self.conn.execute(
            "SELECT scene_id, campaign_id, session_id, name, status, location_entity_id, "
            "in_world_started_at, in_world_ended_at, started_at, ended_at, system_state "
            "FROM scenes WHERE campaign_id = ? AND scene_id = ?",
            (campaign_id, scene_id),
        ).fetchone()
        return _scene_from_row(row) if row is not None else None

    def require_scene(self, campaign_id: str, scene_id: str) -> Scene:
        scene = self.get_scene(campaign_id, scene_id)
        if scene is None:
            raise SceneInvariantError(
                f"scene {scene_id!r} does not exist in campaign {campaign_id!r}"
            )
        return scene

    def get_open_scene(self, campaign_id: str) -> Scene | None:
        """Return the campaign's one open scene, if any."""

        row = self.conn.execute(
            "SELECT scene_id, campaign_id, session_id, name, status, location_entity_id, "
            "in_world_started_at, in_world_ended_at, started_at, ended_at, system_state "
            "FROM scenes WHERE campaign_id = ? AND status = 'open'",
            (campaign_id,),
        ).fetchone()
        return _scene_from_row(row) if row is not None else None

    def list_scenes(self, campaign_id: str) -> tuple[Scene, ...]:
        """Return every scene, closed ones included, in start order."""

        rows = self.conn.execute(
            "SELECT scene_id, campaign_id, session_id, name, status, location_entity_id, "
            "in_world_started_at, in_world_ended_at, started_at, ended_at, system_state "
            "FROM scenes WHERE campaign_id = ? ORDER BY started_at, rowid",
            (campaign_id,),
        ).fetchall()
        return tuple(_scene_from_row(row) for row in rows)

    def get_members(self, campaign_id: str, scene_id: str) -> tuple[SceneMember, ...]:
        rows = self.conn.execute(
            "SELECT m.scene_id, m.entity_id, m.presence_type, m.entered_at, m.exited_at "
            "FROM scene_members m JOIN scenes s ON s.scene_id = m.scene_id "
            "WHERE s.campaign_id = ? AND m.scene_id = ? "
            "ORDER BY m.entity_id",
            (campaign_id, scene_id),
        ).fetchall()
        return tuple(_member_from_row(row) for row in rows)

    def get_present_entity_ids(self, campaign_id: str, scene_id: str) -> tuple[str, ...]:
        rows = self.conn.execute(
            "SELECT m.entity_id FROM scene_members m "
            "JOIN scenes s ON s.scene_id = m.scene_id "
            "WHERE s.campaign_id = ? AND m.scene_id = ? AND m.exited_at IS NULL "
            "ORDER BY m.entity_id",
            (campaign_id, scene_id),
        ).fetchall()
        return tuple(row["entity_id"] for row in rows)

    def get_game_time(self, campaign_id: str) -> GameTime | None:
        row = self.conn.execute(
            "SELECT campaign_id, in_world_label, in_world_minutes, updated_at "
            "FROM campaign_clock WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        if row is None:
            return None
        return GameTime(
            campaign_id=row["campaign_id"],
            in_world_label=row["in_world_label"],
            in_world_minutes=row["in_world_minutes"],
            updated_at=row["updated_at"],
        )

    # -- scene lifecycle --------------------------------------------------

    def open_scene(
        self,
        campaign_id: str,
        scene_id: str,
        name: str,
        *,
        session_id: str | None = None,
        location_entity_id: str | None = None,
        in_world_started_at: str | None = None,
        started_at: str | None = None,
        system_state: Mapping[str, Any] | None = None,
    ) -> Scene:
        with transaction(self.conn):
            return self.open_scene_in_transaction(
                campaign_id,
                scene_id,
                name,
                session_id=session_id,
                location_entity_id=location_entity_id,
                in_world_started_at=in_world_started_at,
                started_at=started_at,
                system_state=system_state,
            )

    def open_scene_in_transaction(
        self,
        campaign_id: str,
        scene_id: str,
        name: str,
        *,
        session_id: str | None = None,
        location_entity_id: str | None = None,
        in_world_started_at: str | None = None,
        started_at: str | None = None,
        system_state: Mapping[str, Any] | None = None,
    ) -> Scene:
        """Open one scene using a transaction already owned by the caller."""

        if self.get_scene(campaign_id, scene_id) is not None:
            raise SceneInvariantError(
                f"scene {scene_id!r} already exists in campaign {campaign_id!r}"
            )
        existing = self.get_open_scene(campaign_id)
        if existing is not None:
            raise SceneInvariantError(
                f"campaign {campaign_id!r} already has open scene "
                f"{existing.scene_id!r}; close it first"
            )
        started_at = _canonical(
            _now() if started_at is None else started_at, field_name="started_at"
        )
        self.conn.execute(
            "INSERT INTO scenes "
            "(scene_id, campaign_id, session_id, name, status, location_entity_id, "
            "in_world_started_at, started_at, system_state) "
            "VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?)",
            (
                scene_id,
                campaign_id,
                session_id,
                name,
                location_entity_id,
                in_world_started_at,
                started_at,
                json.dumps(dict(system_state or {}), separators=(",", ":")),
            ),
        )
        return self.require_scene(campaign_id, scene_id)

    def close_scene(
        self,
        campaign_id: str,
        scene_id: str,
        *,
        ended_at: str | None = None,
        in_world_ended_at: str | None = None,
    ) -> Scene:
        with transaction(self.conn):
            return self.close_scene_in_transaction(
                campaign_id,
                scene_id,
                ended_at=ended_at,
                in_world_ended_at=in_world_ended_at,
            )

    def close_scene_in_transaction(
        self,
        campaign_id: str,
        scene_id: str,
        *,
        ended_at: str | None = None,
        in_world_ended_at: str | None = None,
    ) -> Scene:
        """Close one open scene using a caller-owned transaction.

        Everyone still present exits at the same moment, so presence never
        outlives the scene it was recorded in.
        """

        scene = self.require_scene(campaign_id, scene_id)
        if scene.status is SceneStatus.CLOSED:
            raise SceneInvariantError(f"scene {scene_id!r} is already closed")
        ended_at = _canonical(
            _now() if ended_at is None else ended_at, field_name="ended_at"
        )
        if _instant(ended_at, field_name="ended_at") < _instant(
            scene.started_at, field_name="scene.started_at"
        ):
            raise SceneInvariantError("a scene cannot end before it started")
        self.conn.execute(
            "UPDATE scenes SET status = 'closed', ended_at = ?, "
            "in_world_ended_at = COALESCE(?, in_world_ended_at) "
            "WHERE campaign_id = ? AND scene_id = ?",
            (ended_at, in_world_ended_at, campaign_id, scene_id),
        )
        self.conn.execute(
            "UPDATE scene_members SET exited_at = ? "
            "WHERE scene_id = ? AND exited_at IS NULL",
            (ended_at, scene_id),
        )
        return self.require_scene(campaign_id, scene_id)

    def set_location(
        self,
        campaign_id: str,
        scene_id: str,
        location_entity_id: str | None,
        *,
        changed_at: str | None = None,
    ) -> Scene:
        with transaction(self.conn):
            return self.set_location_in_transaction(
                campaign_id,
                scene_id,
                location_entity_id,
                changed_at=changed_at,
            )

    def set_location_in_transaction(
        self,
        campaign_id: str,
        scene_id: str,
        location_entity_id: str | None,
        *,
        changed_at: str | None = None,
    ) -> Scene:
        """Record a scene's current location using a caller-owned transaction."""

        scene = self.require_scene(campaign_id, scene_id)
        if not scene.is_open:
            raise SceneInvariantError(
                f"cannot move scene {scene_id!r}: it is closed"
            )
        if location_entity_id is not None and not self._entity_in_campaign(
            campaign_id, location_entity_id
        ):
            raise SceneInvariantError(
                f"entity {location_entity_id!r} is not a campaign entity of "
                f"{campaign_id!r}"
            )
        if changed_at is None:
            changed_at = _now()
        self.conn.execute(
            "UPDATE scenes SET location_entity_id = ? "
            "WHERE campaign_id = ? AND scene_id = ?",
            (location_entity_id, campaign_id, scene_id),
        )
        return self.require_scene(campaign_id, scene_id)

    # -- presence ---------------------------------------------------------

    def enter(
        self,
        campaign_id: str,
        scene_id: str,
        entity_id: str,
        presence_type: PresenceType,
        *,
        entered_at: str | None = None,
    ) -> SceneMember:
        with transaction(self.conn):
            return self.enter_in_transaction(
                campaign_id,
                scene_id,
                entity_id,
                presence_type,
                entered_at=entered_at,
            )

    def enter_in_transaction(
        self,
        campaign_id: str,
        scene_id: str,
        entity_id: str,
        presence_type: PresenceType,
        *,
        entered_at: str | None = None,
    ) -> SceneMember:
        """Record presence using a transaction already owned by the caller."""

        scene = self.require_scene(campaign_id, scene_id)
        if not scene.is_open:
            raise SceneInvariantError(
                f"cannot enter closed scene {scene_id!r}"
            )
        if not isinstance(presence_type, PresenceType):
            raise SceneInvariantError("presence_type must be a PresenceType")
        if not self._entity_in_campaign(campaign_id, entity_id):
            raise SceneInvariantError(
                f"entity {entity_id!r} is not a campaign entity of {campaign_id!r}"
            )
        entered_at = _canonical(
            _now() if entered_at is None else entered_at, field_name="entered_at"
        )
        for member in self.get_members(campaign_id, scene_id):
            if member.entity_id == entity_id and member.is_present:
                raise SceneInvariantError(
                    f"entity {entity_id!r} is already present in scene {scene_id!r}"
                )
        if _instant(entered_at, field_name="entered_at") < _instant(
            scene.started_at, field_name="scene.started_at"
        ):
            raise SceneInvariantError("presence cannot precede the scene start")
        # Re-entry is a fresh presence interval: the previous exit is replaced.
        self.conn.execute(
            "DELETE FROM scene_members WHERE scene_id = ? AND entity_id = ?",
            (scene_id, entity_id),
        )
        self.conn.execute(
            "INSERT INTO scene_members "
            "(scene_id, entity_id, presence_type, entered_at) VALUES (?, ?, ?, ?)",
            (scene_id, entity_id, presence_type.value, entered_at),
        )
        return self._member(campaign_id, scene_id, entity_id)

    def exit(
        self,
        campaign_id: str,
        scene_id: str,
        entity_id: str,
        *,
        exited_at: str | None = None,
    ) -> SceneMember:
        with transaction(self.conn):
            return self.exit_in_transaction(
                campaign_id, scene_id, entity_id, exited_at=exited_at
            )

    def exit_in_transaction(
        self,
        campaign_id: str,
        scene_id: str,
        entity_id: str,
        *,
        exited_at: str | None = None,
    ) -> SceneMember:
        """End one presence interval using a caller-owned transaction."""

        member = self._member(campaign_id, scene_id, entity_id)
        if not member.is_present:
            raise SceneInvariantError(
                f"entity {entity_id!r} is not present in scene {scene_id!r}"
            )
        exited_at = _canonical(
            _now() if exited_at is None else exited_at, field_name="exited_at"
        )
        if _instant(exited_at, field_name="exited_at") < _instant(
            member.entered_at, field_name="presence.entered_at"
        ):
            raise SceneInvariantError("presence cannot exit before it entered")
        self.conn.execute(
            "UPDATE scene_members SET exited_at = ? "
            "WHERE scene_id = ? AND entity_id = ?",
            (exited_at, scene_id, entity_id),
        )
        return self._member(campaign_id, scene_id, entity_id)

    # -- campaign clock ---------------------------------------------------

    def set_game_time(
        self,
        campaign_id: str,
        *,
        in_world_label: str | None = None,
        in_world_minutes: int | None = None,
        updated_at: str | None = None,
    ) -> GameTime:
        with transaction(self.conn):
            return self.set_game_time_in_transaction(
                campaign_id,
                in_world_label=in_world_label,
                in_world_minutes=in_world_minutes,
                updated_at=updated_at,
            )

    def set_game_time_in_transaction(
        self,
        campaign_id: str,
        *,
        in_world_label: str | None = None,
        in_world_minutes: int | None = None,
        updated_at: str | None = None,
    ) -> GameTime:
        """Set the campaign clock using a caller-owned transaction."""

        if in_world_minutes is not None and in_world_minutes < 0:
            raise SceneInvariantError("in_world_minutes cannot be negative")
        updated_at = _canonical(
            _now() if updated_at is None else updated_at, field_name="updated_at"
        )
        self.conn.execute(
            "INSERT INTO campaign_clock "
            "(campaign_id, in_world_label, in_world_minutes, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(campaign_id) DO UPDATE SET "
            "in_world_label = excluded.in_world_label, "
            "in_world_minutes = excluded.in_world_minutes, "
            "updated_at = excluded.updated_at",
            (campaign_id, in_world_label, in_world_minutes, updated_at),
        )
        stored = self.get_game_time(campaign_id)
        if stored is None:  # pragma: no cover - the upsert above always writes
            raise SceneInvariantError(f"campaign clock for {campaign_id!r} was not written")
        return stored

    # -- helpers ----------------------------------------------------------

    def _entity_in_campaign(self, campaign_id: str, entity_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM entities "
            "WHERE owner_scope = 'campaign' AND campaign_id = ? AND entity_id = ?",
            (campaign_id, entity_id),
        ).fetchone()
        return row is not None

    def _member(self, campaign_id: str, scene_id: str, entity_id: str) -> SceneMember:
        for member in self.get_members(campaign_id, scene_id):
            if member.entity_id == entity_id:
                return member
        raise SceneInvariantError(
            f"entity {entity_id!r} has no presence record in scene {scene_id!r}"
        )


def _scene_from_row(row: sqlite3.Row) -> Scene:
    return Scene(
        scene_id=row["scene_id"],
        campaign_id=row["campaign_id"],
        session_id=row["session_id"],
        name=row["name"],
        status=SceneStatus(row["status"]),
        location_entity_id=row["location_entity_id"],
        in_world_started_at=row["in_world_started_at"],
        in_world_ended_at=row["in_world_ended_at"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        system_state=json.loads(row["system_state"]),
    )


def _member_from_row(row: sqlite3.Row) -> SceneMember:
    return SceneMember(
        scene_id=row["scene_id"],
        entity_id=row["entity_id"],
        presence_type=PresenceType(row["presence_type"]),
        entered_at=row["entered_at"],
        exited_at=row["exited_at"],
    )


class CampaignPaused(RuntimeError):
    """A campaign is paused and new player turns must not start."""


def is_paused(conn: sqlite3.Connection, campaign_id: str) -> bool:
    """True when the GM has paused this campaign."""
    row = conn.execute(
        "SELECT paused FROM campaign_pause WHERE campaign_id = ?", (campaign_id,)
    ).fetchone()
    return row is not None and bool(row["paused"])


def pause_campaign(
    conn: sqlite3.Connection, campaign_id: str, *, reason: str | None = None
) -> dict[str, Any]:
    """Pause a campaign.

    Queued turns are not discarded. They stay in ``turn_jobs``, remain
    inspectable, and continue in order on resume.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    with transaction(conn):
        conn.execute(
            "INSERT INTO campaign_pause (campaign_id, paused, reason, paused_at, "
            "updated_at) VALUES (?, 1, ?, ?, ?) "
            "ON CONFLICT(campaign_id) DO UPDATE SET paused = 1, reason = excluded.reason, "
            "paused_at = excluded.paused_at, updated_at = excluded.updated_at",
            (campaign_id, reason, now, now),
        )
    return {"campaign_id": campaign_id, "paused": True, "reason": reason}


def resume_campaign(conn: sqlite3.Connection, campaign_id: str) -> dict[str, Any]:
    """Resume a paused campaign. Queued turns continue in order."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    with transaction(conn):
        conn.execute(
            "INSERT INTO campaign_pause (campaign_id, paused, resumed_at, updated_at) "
            "VALUES (?, 0, ?, ?) "
            "ON CONFLICT(campaign_id) DO UPDATE SET paused = 0, reason = NULL, "
            "resumed_at = excluded.resumed_at, updated_at = excluded.updated_at",
            (campaign_id, now, now),
        )
    return {"campaign_id": campaign_id, "paused": False}


def require_not_paused(conn: sqlite3.Connection, campaign_id: str) -> None:
    """Refuse new player processing while a campaign is paused."""
    if is_paused(conn, campaign_id):
        raise CampaignPaused(
            f"campaign {campaign_id!r} is paused; the GM must resume it first"
        )
