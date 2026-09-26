"""One read-only structured snapshot of where a campaign is right now.

Both operator resume and model prompt context read this function. It is the
only place that answers "what scene are we in, who is here, and what is the
campaign clock": no caller infers it from chat history, and no caller runs its
own scene SQL.

Every read is viewpoint-filtered through the existing campaign store, so a
player snapshot can never contain GM-only facts or private NPC state.
Building a snapshot writes nothing.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from tabletop.api.visibility import Viewpoint, VisibilityKind
from tabletop.campaign.event_store import EventStore
from tabletop.campaign.models import Scene, SceneMember
from tabletop.campaign.rulings import RulingStore
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore

_RECENT_EVENT_LIMIT = 10


def build_scene_snapshot(
    conn: sqlite3.Connection,
    campaign_id: str,
    *,
    viewpoint: Viewpoint,
) -> dict[str, Any]:
    """Return the authoritative scene snapshot for one campaign and viewpoint.

    ``viewpoint`` is required, never defaulted: choosing it implicitly is how
    GM-only state leaks into a player prompt.
    """

    store = CampaignStore(conn)
    campaign = store.get_campaign(campaign_id)
    if campaign is None:
        raise LookupError(f"campaign {campaign_id!r} not found")

    scene_store = SceneStore(conn)
    # Resume returns the latest scene even when closed, so scene B stays
    # inspectable after a restart instead of the snapshot reading "none".
    scene = _latest_scene(scene_store, campaign_id)
    members = (
        ()
        if scene is None
        else scene_store.get_members(campaign_id, scene.scene_id)
    )
    open_scene = scene_store.get_open_scene(campaign_id)
    clock = scene_store.get_game_time(campaign_id)

    return {
        "campaign": {
            "campaign_id": campaign_id,
            "name": campaign.get("name"),
            "system_id": campaign.get("system_id"),
            "archived_at": campaign.get("archived_at"),
        },
        "session": _latest_session(conn, campaign_id),
        "scene": None if scene is None else _scene_payload(scene),
        "open_scene_id": None if open_scene is None else open_scene.scene_id,
        "present_entities": _present_entities(conn, campaign_id, members),
        "recent_events": _recent_events(conn, campaign_id, scene),
        "active_rulings": _active_rulings(conn, campaign_id, viewpoint),
        "relevant_facts": [
            {
                "fact_id": fact.fact_id,
                "subject_id": fact.subject_id,
                "predicate": fact.predicate,
                "value": fact.value,
            }
            for fact in store.get_facts(campaign_id, viewpoint=viewpoint)
        ],
        "pending_contradictions": _pending_contradictions(conn, campaign_id),
        "game_time": (
            None
            if clock is None
            else {
                "in_world_label": clock.in_world_label,
                "in_world_minutes": clock.in_world_minutes,
                "updated_at": clock.updated_at,
            }
        ),
    }


def _latest_scene(store: SceneStore, campaign_id: str) -> Scene | None:
    """Return the most recent scene, open or closed."""

    scenes = store.list_scenes(campaign_id)
    return scenes[-1] if scenes else None


def _scene_payload(scene: Scene) -> dict[str, Any]:
    return {
        "scene_id": scene.scene_id,
        "name": scene.name,
        "status": scene.status.value,
        "session_id": scene.session_id,
        "location_entity_id": scene.location_entity_id,
        "started_at": scene.started_at,
        "ended_at": scene.ended_at,
    }


def _present_entities(
    conn: sqlite3.Connection,
    campaign_id: str,
    members: tuple[SceneMember, ...],
) -> list[dict[str, Any]]:
    """Name who is present, dropping anyone still marked as having exited."""

    store = CampaignStore(conn)
    present: list[dict[str, Any]] = []
    for member in members:
        if not member.is_present:
            continue
        entity = store.get_entity(campaign_id, member.entity_id)
        present.append(
            {
                "entity_id": member.entity_id,
                "name": None if entity is None else entity.get("name"),
                "presence_type": member.presence_type.value,
                "entered_at": member.entered_at,
            }
        )
    return present


def _latest_session(conn: sqlite3.Connection, campaign_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT session_id, started_at, ended_at FROM sessions "
        "WHERE campaign_id = ? ORDER BY started_at DESC, session_id DESC LIMIT 1",
        (campaign_id,),
    ).fetchone()
    return None if row is None else dict(row)


def _recent_events(
    conn: sqlite3.Connection,
    campaign_id: str,
    scene: Scene | None,
) -> list[dict[str, Any]]:
    """Return the tail of the event log, newest first.

    Events are authoritative records, not a transcript: this reads the log,
    never chat text.
    """

    events = EventStore(conn).read(campaign_id)
    tail = events[-_RECENT_EVENT_LIMIT:]
    return [
        {
            "sequence": event.sequence,
            "event_type": event.event_type,
            "scene_id": event.scene_id,
            "occurred_at": event.occurred_at,
        }
        for event in reversed(tail)
    ]


def _active_rulings(
    conn: sqlite3.Connection,
    campaign_id: str,
    viewpoint: Viewpoint,
) -> list[dict[str, Any]]:
    """Return confirmed campaign precedent, filtered to what the viewer may know.

    A player must not see a ruling the GM has not revealed, so both the
    visibility policy and the record's own knowledge state are honored.
    """

    rulings = RulingStore(conn).list_active(campaign_id)
    if viewpoint.scope.kind is not VisibilityKind.GM:
        rulings = tuple(
            ruling for ruling in rulings if ruling.knowledge_state.value == "known"
        )
    return [
        {
            "ruling_id": ruling.ruling_id,
            "question": ruling.question,
            "decision": ruling.decision,
            "scope": ruling.scope,
        }
        for ruling in rulings
    ]


def _pending_contradictions(conn: sqlite3.Connection, campaign_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM events "
        "WHERE campaign_id = ? AND event_type = 'canon.contradiction_detected'",
        (campaign_id,),
    ).fetchone()
    return int(row[0])
