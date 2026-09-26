"""Turn handling.

Drives one turn: action intake, deterministic resolution via the active
system plugin, event emission, and response assembly. The LLM proposes
intent; the runtime and plugins resolve mechanics.

Phase 9 filled in the resolution step and its guard. Event emission and
response assembly stay with Phases 12 and 30.

The guard is the point of this module: when the active system advertises
``Capability.ACTION_RESOLUTION``, :func:`resolve_action` must call that
plugin, and it returns the plugin's ``Resolution`` unchanged. There is no
branch here that manufactures a mechanical result, and callers have no
other supported path to one.

:func:`play_turn` is the surrounding loop. It builds a
``ResolutionContext`` from the campaign store, resolves only through
:func:`resolve_action`, persists a ``RESOLVED`` result atomically via
``apply_resolved_action``, and routes every non-resolved status to
adjudication with status-specific framing and no state writes.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from tabletop.api.actions import GameAction
from tabletop.api.capabilities import Capability
from tabletop.api.entities import EntityRef
from tabletop.api.errors import InvalidResolutionError
from tabletop.api.resolution import Resolution, ResolutionContext, ResolutionStatus
from tabletop.campaign.event_store import PersistedEvent, apply_resolved_action
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.adjudication import (
    AdjudicationRequest,
    adjudication_request,
    requires_adjudication,
)
from tabletop.plugins.registry import PluginRegistry


def resolve_action(
    registry: PluginRegistry,
    action: GameAction,
    context: ResolutionContext,
) -> Resolution:
    """Resolve one action through the system named by ``context.system_id``.

    Returns ``UNSUPPORTED`` when the active system does not advertise
    ``ACTION_RESOLUTION``. Otherwise the plugin decides, including its own
    ``UNSUPPORTED`` for action categories it does not cover.

    An unknown system id is a configuration failure, not a game outcome, so
    ``PluginNotFoundError`` propagates. A plugin returning a non-``Resolution``
    fails closed rather than being coerced into one.
    """
    plugin = registry.get(context.system_id)
    if not plugin.supports(Capability.ACTION_RESOLUTION):
        return Resolution(
            outcome={},
            status=ResolutionStatus.UNSUPPORTED,
            explanation=(
                f"system {context.system_id!r} does not implement action resolution"
            ),
        )
    resolution = plugin.resolve(action, context)
    if not isinstance(resolution, Resolution):
        raise InvalidResolutionError(
            f"system {context.system_id!r} returned "
            f"{type(resolution).__name__}, expected Resolution"
        )
    return resolution


@dataclass(frozen=True)
class TurnResult:
    """Assembled outcome of one play turn through the resolution guard."""

    action: GameAction
    context: ResolutionContext
    resolution: Resolution
    narration: str
    event: PersistedEvent | None = None
    adjudication: AdjudicationRequest | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": self.action.to_dict(),
            "context": self.context.to_dict(),
            "resolution": self.resolution.to_dict(),
            "narration": self.narration,
            "event": None
            if self.event is None
            else {
                "campaign_id": self.event.campaign_id,
                "sequence": self.event.sequence,
                "event_type": self.event.event_type,
                "session_id": self.event.session_id,
                "scene_id": self.event.scene_id,
                "actor_id": self.event.actor_id,
                "target_id": self.event.target_id,
                "payload": dict(self.event.payload),
                "occurred_at": self.event.occurred_at,
            },
            "adjudication": None
            if self.adjudication is None
            else {
                "status": self.adjudication.status.value,
                "headline": self.adjudication.headline,
                "detail": self.adjudication.detail,
                "rule_references": [
                    ref.to_dict() for ref in self.adjudication.rule_references
                ],
            },
        }
        return payload


def play_turn(
    registry: PluginRegistry,
    conn: sqlite3.Connection,
    action: GameAction,
    *,
    campaign_id: str,
    system_id: str,
    scene_id: str | None = None,
) -> TurnResult:
    """Run one play turn through the resolution guard.

    Builds plugin-visible context from the campaign store, resolves only via
    :func:`resolve_action`, then either persists state and the
    ``action.resolved`` event atomically or routes to adjudication without
    writing state.
    """
    started = time.perf_counter()
    # A turn with no explicit scene acts in the campaign's open scene, so
    # scene-scoped state changes land where play is actually happening.
    if scene_id is None:
        scene_id = current_open_scene_id(conn, campaign_id)
    context = build_resolution_context(
        conn,
        campaign_id=campaign_id,
        system_id=system_id,
        scene_id=scene_id,
    )
    resolution = resolve_action(registry, action, context)
    narration = assemble_narration(resolution)

    if requires_adjudication(resolution):
        result = TurnResult(
            action=action,
            context=context,
            resolution=resolution,
            narration=narration,
            event=None,
            adjudication=adjudication_request(resolution, action, context),
        )
        _write_turn_receipt(conn, result, campaign_id, system_id, started)
        return result

    event = apply_resolved_action(
        conn,
        campaign_id,
        action,
        resolution,
        scene_id=scene_id,
    )
    result = TurnResult(
        action=action,
        context=context,
        resolution=resolution,
        narration=narration,
        event=event,
        adjudication=None,
    )
    _write_turn_receipt(conn, result, campaign_id, system_id, started)
    return result


def _write_turn_receipt(
    conn: sqlite3.Connection,
    result: TurnResult,
    campaign_id: str,
    system_id: str,
    started: float,
) -> None:
    latency_ms = int((time.perf_counter() - started) * 1000)
    sequence = None if result.event is None else result.event.sequence
    conn.execute(
        "INSERT INTO turn_receipts "
        "(turn_id, campaign_id, system_id, status, event_sequence, retrieval_tier, "
        "latency_ms, provider, model, tokens_in, tokens_out) "
        "VALUES (?, ?, ?, ?, ?, NULL, ?, NULL, NULL, NULL, NULL)",
        (
            uuid.uuid4().hex,
            campaign_id,
            system_id,
            result.resolution.status.value,
            sequence,
            latency_ms,
        ),
    )
    conn.commit()


def build_resolution_context(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    system_id: str,
    scene_id: str | None = None,
) -> ResolutionContext:
    """Assemble the plugin-visible state snapshot for one resolve call."""

    store = CampaignStore(conn)
    campaign = store.get_campaign(campaign_id)
    if campaign is None:
        raise InvalidResolutionError(f"campaign {campaign_id!r} does not exist")

    entities = resolve_entity_overlay(conn, campaign_id, campaign.get("setting_id"))

    state: dict[str, Any] = {
        "campaign": {"system": dict(campaign["system_state"])},
        "entities": entities,
    }
    if scene_id is not None:
        scene_row = conn.execute(
            "SELECT system_state FROM scenes "
            "WHERE campaign_id = ? AND scene_id = ?",
            (campaign_id, scene_id),
        ).fetchone()
        if scene_row is not None:
            state["scene"] = {"system": json.loads(scene_row["system_state"])}

    return ResolutionContext(
        campaign_id=campaign_id,
        system_id=system_id,
        scene_id=scene_id,
        state=state,
    )


def current_open_scene_id(conn: sqlite3.Connection, campaign_id: str) -> str | None:
    """Return the campaign's open scene id, or None when no scene is open."""

    row = conn.execute(
        "SELECT scene_id FROM scenes WHERE campaign_id = ? AND status = 'open'",
        (campaign_id,),
    ).fetchone()
    return None if row is None else str(row["scene_id"])


def resolve_entity_overlay(
    conn: sqlite3.Connection,
    campaign_id: str,
    setting_id: str | None,
) -> dict[str, Any]:
    """Campaign entity ids win, then campaign overrides, then setting entities."""

    rows = conn.execute(
        "SELECT entity_id, owner_scope, overrides_id, system_state FROM entities "
        "WHERE (owner_scope = 'campaign' AND campaign_id = ?) "
        "OR (owner_scope = 'setting' AND setting_id = ?) "
        "ORDER BY entity_id",
        (campaign_id, setting_id),
    ).fetchall()
    setting_rows = {
        row["entity_id"]: row for row in rows if row["owner_scope"] == "setting"
    }
    campaign_rows = [row for row in rows if row["owner_scope"] == "campaign"]
    hidden_setting_ids = {row["entity_id"] for row in campaign_rows}
    hidden_setting_ids.update(
        row["overrides_id"]
        for row in campaign_rows
        if isinstance(row["overrides_id"], str) and row["overrides_id"]
    )
    entities: dict[str, Any] = {}
    for row in campaign_rows:
        entities[row["entity_id"]] = {"system": json.loads(row["system_state"])}
    for entity_id, row in setting_rows.items():
        if entity_id in hidden_setting_ids:
            continue
        entities[entity_id] = {"system": json.loads(row["system_state"])}
    return entities


def assemble_narration(resolution: Resolution) -> str:
    """Build turn narration from the plugin resolution, never replacing it."""

    if resolution.explanation:
        return resolution.explanation
    if resolution.ruling_question:
        return resolution.ruling_question
    return ""


def parse_game_action(payload: Mapping[str, Any]) -> GameAction:
    """Parse a JSON-compatible action mapping into a ``GameAction``."""

    if not isinstance(payload, Mapping):
        raise InvalidResolutionError("action payload must be an object")
    raw_actor = payload.get("actor")
    if not isinstance(raw_actor, Mapping):
        raise InvalidResolutionError("action.actor must be an object")
    actor_id = raw_actor.get("id")
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise InvalidResolutionError("action.actor.id must be a non-empty string")
    actor = EntityRef(
        id=actor_id,
        entity_type=raw_actor.get("entity_type")
        if isinstance(raw_actor.get("entity_type"), str)
        else None,
    )
    action_type = payload.get("action_type")
    if not isinstance(action_type, str):
        raise InvalidResolutionError("action.action_type must be a string")

    targets: list[EntityRef] = []
    raw_targets = payload.get("targets", ())
    if raw_targets is None:
        raw_targets = ()
    if not isinstance(raw_targets, (list, tuple)):
        raise InvalidResolutionError("action.targets must be a list")
    for item in raw_targets:
        if not isinstance(item, Mapping):
            raise InvalidResolutionError("each action target must be an object")
        target_id = item.get("id")
        if not isinstance(target_id, str) or not target_id.strip():
            raise InvalidResolutionError("action target id must be a non-empty string")
        targets.append(
            EntityRef(
                id=target_id,
                entity_type=item.get("entity_type")
                if isinstance(item.get("entity_type"), str)
                else None,
            )
        )

    parameters = payload.get("parameters", {})
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, Mapping):
        raise InvalidResolutionError("action.parameters must be an object")

    return GameAction(
        actor=actor,
        action_type=action_type,
        targets=tuple(targets),
        parameters=dict(parameters),
    )
