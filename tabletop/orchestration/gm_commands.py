"""One GM command language, shared by the CLI and channel control messages.

The GM used to drive the game by calling workspace skills. That put a
privileged surface inside the model's reach: if the model can call it, the
model can be talked into calling it. ``/gm status`` and
``gamemaster campaign status`` are clients of this router instead, which
composes the same read APIs and issues no SQL of its own.

The command set is closed. An unknown verb is rejected rather than guessed at,
and read commands take no free-form argument that could become a query.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping

from tabletop.api.visibility import Viewpoint, gm_viewpoint
from tabletop.campaign.event_store import EventStore
from tabletop.campaign.resume import resume_snapshot
from tabletop.campaign.store import CampaignStore

#: Bound on a GM-supplied identifier. A control message is untrusted input, so
#: an unbounded id is refused before it reaches a query.
MAX_ARGUMENT_LENGTH = 128
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class GmCommand(str, Enum):
    """The closed set of GM read commands."""

    STATUS = "status"
    ENTITY = "entity"
    FACTS = "facts"
    RELATIONSHIPS = "relationships"
    NPC = "npc"
    RECENT = "recent"


class GmCommandError(ValueError):
    """A GM command was malformed or unknown."""


@dataclass(frozen=True)
class GmRequest:
    """One parsed GM command."""

    command: GmCommand
    argument: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"command": self.command.value, "argument": self.argument}


def parse_gm_command(text: str) -> GmRequest:
    """Parse one GM control line.

    Strict on purpose: an unknown verb or a malformed argument is an error an
    operator can see, not something to route somewhere plausible.
    """
    stripped = text.strip()
    if stripped.startswith("/gm"):
        stripped = stripped[3:].strip()
    if not stripped:
        raise GmCommandError("empty GM command")
    parts = stripped.split(None, 1)
    verb = parts[0].casefold()
    argument = parts[1].strip() if len(parts) > 1 else None
    try:
        command = GmCommand(verb)
    except ValueError as exc:
        raise GmCommandError(
            f"unknown GM command {verb!r}; choose one of "
            f"{', '.join(c.value for c in GmCommand)}"
        ) from exc
    # The argument bound is checked before the whole-line bound so an overlong
    # argument reports the rule it actually violated.
    if argument is not None:
        _require_argument(command, argument)
    return GmRequest(command=command, argument=argument)


def _require_argument(command: GmCommand, argument: str) -> None:
    if len(argument) > MAX_ARGUMENT_LENGTH:
        raise GmCommandError(
            f"{command.value} argument must be at most {MAX_ARGUMENT_LENGTH} characters"
        )
    if not _IDENTIFIER.fullmatch(argument):
        raise GmCommandError(
            f"{command.value} argument must be a plain identifier, got {argument!r}"
        )


class GmRouter:
    """Execute a parsed GM command against the authoritative stores."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        campaign_id: str,
        *,
        viewpoint: Viewpoint | None = None,
    ) -> None:
        self.conn = conn
        self.campaign_id = campaign_id
        self.viewpoint = viewpoint or gm_viewpoint()

    def dispatch(self, text: str) -> dict[str, Any]:
        request = parse_gm_command(text)
        handler: Callable[[GmRequest], dict[str, Any]] = {
            GmCommand.STATUS: self._status,
            GmCommand.ENTITY: self._entity,
            GmCommand.FACTS: self._facts,
            GmCommand.RELATIONSHIPS: self._relationships,
            GmCommand.NPC: self._npc,
            GmCommand.RECENT: self._recent,
        }[request.command]
        return handler(request)

    # -- read commands ----------------------------------------------------

    def _status(self, _request: GmRequest) -> dict[str, Any]:
        snapshot = resume_snapshot(self.conn, self.campaign_id)
        return {
            "command": "status",
            "campaign_id": self.campaign_id,
            "name": snapshot["name"],
            "system_id": snapshot["system_id"],
            "scene": snapshot["scene"],
            "in_world_date": snapshot["in_world_date"],
            "authoritative_contradictions": snapshot["authoritative_contradictions"],
        }

    def _entity(self, request: GmRequest) -> dict[str, Any]:
        if request.argument is None:
            raise GmCommandError("entity requires an entity id")
        entity = CampaignStore(self.conn).get_entity(
            self.campaign_id, request.argument
        )
        if entity is None:
            raise GmCommandError(
                f"entity {request.argument!r} is not in campaign {self.campaign_id!r}"
            )
        return {"command": "entity", "entity": entity}

    def _facts(self, _request: GmRequest) -> dict[str, Any]:
        facts = CampaignStore(self.conn).get_facts(
            self.campaign_id, viewpoint=self.viewpoint
        )
        return {
            "command": "facts",
            "facts": [
                {
                    "fact_id": fact.fact_id,
                    "subject_id": fact.subject_id,
                    "predicate": fact.predicate,
                    "value": fact.value,
                    "visibility": fact.visibility,
                }
                for fact in facts
            ],
        }

    def _relationships(self, request: GmRequest) -> dict[str, Any]:
        if request.argument is None:
            raise GmCommandError("relationships requires an entity id")
        from tabletop.campaign.relationships import resolve_relationship_overlay

        campaign = CampaignStore(self.conn).get_campaign(self.campaign_id)
        if campaign is None:
            raise GmCommandError(f"campaign {self.campaign_id!r} not found")
        edges = resolve_relationship_overlay(
            self.conn,
            campaign_id=self.campaign_id,
            setting_id=campaign.get("setting_id"),
            entity_id=request.argument,
            as_of=datetime.now(timezone.utc).date().isoformat(),
            viewpoint=self.viewpoint,
        )
        return {
            "command": "relationships",
            "entity_id": request.argument,
            "relationships": [
                {
                    "relationship_id": edge.relationship_id,
                    "source_id": edge.source_id,
                    "target_id": edge.target_id,
                    "relationship_type": edge.relationship_type,
                }
                for edge in edges
            ],
        }

    def _npc(self, request: GmRequest) -> dict[str, Any]:
        if request.argument is None:
            raise GmCommandError("npc requires an entity id")
        entity = CampaignStore(self.conn).get_entity(
            self.campaign_id, request.argument
        )
        if entity is None:
            raise GmCommandError(
                f"entity {request.argument!r} is not in campaign {self.campaign_id!r}"
            )
        return {
            "command": "npc",
            "entity_id": request.argument,
            "entity": entity,
        }

    def _recent(self, request: GmRequest) -> dict[str, Any]:
        limit = 10
        if request.argument is not None:
            try:
                limit = int(request.argument)
            except ValueError as exc:
                raise GmCommandError(
                    f"recent takes a count, got {request.argument!r}"
                ) from exc
            if not 1 <= limit <= 100:
                raise GmCommandError("recent count must be between 1 and 100")
        events = EventStore(self.conn).read(self.campaign_id)[-limit:]
        return {
            "command": "recent",
            "events": [
                {
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "scene_id": event.scene_id,
                    "occurred_at": event.occurred_at,
                }
                for event in reversed(events)
            ],
        }


class GmMutatingCommand(str, Enum):
    """GM commands that change authoritative state.

    Every one of these dispatches to an existing service and emits that
    service's event. None of them writes SQL directly, so an authority change
    is one edit rather than one per command.
    """

    SCENE_OPEN = "scene open"
    SCENE_CLOSE = "scene close"
    SCENE_TRANSITION = "scene transition"
    SCENE_ENTER = "scene enter"
    SCENE_EXIT = "scene exit"
    SCENE_LOCATION = "scene location"
    TIME_SET = "time set"
    RULING_RECORD = "ruling record"
    RULING_PROMOTE = "ruling promote"
    FACT_REVEAL = "fact reveal"
    FACT_REJECT = "fact reject"


_MUTATING_ARGUMENT_COUNT: Mapping[GmMutatingCommand, tuple[int, ...]] = {
    GmMutatingCommand.SCENE_OPEN: (2,),
    GmMutatingCommand.SCENE_CLOSE: (1,),
    GmMutatingCommand.SCENE_TRANSITION: (2,),
    GmMutatingCommand.SCENE_ENTER: (3,),
    GmMutatingCommand.SCENE_EXIT: (2,),
    GmMutatingCommand.SCENE_LOCATION: (2,),
    GmMutatingCommand.TIME_SET: (1,),
    GmMutatingCommand.RULING_RECORD: (2,),
    GmMutatingCommand.RULING_PROMOTE: (1,),
    GmMutatingCommand.FACT_REVEAL: (1,),
    GmMutatingCommand.FACT_REJECT: (1,),
}


@dataclass(frozen=True)
class GmMutation:
    """One parsed mutating GM command."""

    command: GmMutatingCommand
    arguments: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"command": self.command.value, "arguments": list(self.arguments)}


def parse_gm_mutation(text: str) -> GmMutation:
    """Parse one mutating GM control line, validating arity and shape."""
    stripped = text.strip()
    if stripped.startswith("/gm"):
        stripped = stripped[3:].strip()
    parts = stripped.split()
    if len(parts) < 2:
        raise GmCommandError("expected a mutating GM command and its arguments")
    verb = " ".join(parts[:2]).casefold()
    arguments = parts[2:]
    try:
        command = GmMutatingCommand(verb)
    except ValueError as exc:
        raise GmCommandError(
            f"unknown GM command {verb!r}; choose one of "
            f"{', '.join(c.value for c in GmMutatingCommand)}"
        ) from exc
    expected = _MUTATING_ARGUMENT_COUNT[command]
    if len(arguments) not in expected:
        raise GmCommandError(
            f"{command.value} takes {' or '.join(map(str, expected))} arguments, "
            f"got {len(arguments)}"
        )
    for argument in arguments:
        if len(argument) > MAX_ARGUMENT_LENGTH:
            raise GmCommandError("GM argument is too long")
        if not _IDENTIFIER.fullmatch(argument):
            raise GmCommandError(
                f"arguments must be plain identifiers, got {argument!r}"
            )
    return GmMutation(command=command, arguments=tuple(arguments))


def _scene_service(conn: sqlite3.Connection):
    from tabletop.campaign.scenes import SceneStore

    return SceneStore(conn)


def apply_gm_mutation(
    conn: sqlite3.Connection, mutation: GmMutation, campaign_id: str
) -> dict[str, Any]:
    """Execute one mutating GM command through the existing services.

    Every branch ends in a service call that also appends the matching event.
    Recording a ruling does not confirm it: a new ruling starts proposed and
    unrevealed unless the GM explicitly promotes it.
    """
    from tabletop.campaign.event_store import (
        EventStore,
        close_scene_event,
        entity_entered_event,
        entity_exited_event,
        open_scene_event,
        scene_location_changed_event,
        scene_time_changed_event,
    )
    from tabletop.campaign.models import PresenceType
    from tabletop.campaign.rulings import Ruling, RulingStore
    from tabletop.campaign.store import CampaignStore
    from tabletop.storage.sqlite import transaction

    scenes = _scene_service(conn)
    command, args = mutation.command, mutation.arguments

    if command is GmMutatingCommand.SCENE_OPEN:
        with transaction(conn):
            scene = scenes.open_scene_in_transaction(campaign_id, args[0], args[1])
            EventStore(conn).append_in_transaction(
                conn, campaign_id,
                open_scene_event(
                    scene_id=scene.scene_id, name=scene.name, started_at=scene.started_at
                ),
                scene_id=scene.scene_id, occurred_at=scene.started_at,
            )
        return {"command": command.value, "scene_id": scene.scene_id}

    if command is GmMutatingCommand.SCENE_CLOSE:
        with transaction(conn):
            scene = scenes.close_scene_in_transaction(campaign_id, args[0])
            EventStore(conn).append_in_transaction(
                conn, campaign_id,
                close_scene_event(
                    scene_id=scene.scene_id, ended_at=scene.ended_at or ""
                ),
                scene_id=scene.scene_id, occurred_at=scene.ended_at,
            )
        return {"command": command.value, "scene_id": scene.scene_id}

    if command is GmMutatingCommand.SCENE_TRANSITION:
        with transaction(conn):
            current = scenes.get_open_scene(campaign_id)
            if current is not None:
                closed = scenes.close_scene_in_transaction(campaign_id, current.scene_id)
                EventStore(conn).append_in_transaction(
                    conn, campaign_id,
                    close_scene_event(
                        scene_id=closed.scene_id, ended_at=closed.ended_at or ""
                    ),
                    scene_id=closed.scene_id, occurred_at=closed.ended_at,
                )
            opened = scenes.open_scene_in_transaction(campaign_id, args[0], args[1])
            EventStore(conn).append_in_transaction(
                conn, campaign_id,
                open_scene_event(
                    scene_id=opened.scene_id, name=opened.name,
                    started_at=opened.started_at,
                ),
                scene_id=opened.scene_id, occurred_at=opened.started_at,
            )
        return {"command": command.value, "scene_id": opened.scene_id}

    if command is GmMutatingCommand.SCENE_ENTER:
        scene_id, entity_id, raw_presence = args
        try:
            presence = PresenceType(raw_presence.casefold())
        except ValueError as exc:
            raise GmCommandError(
                f"presence type must be one of "
                f"{', '.join(p.value for p in PresenceType)}"
            ) from exc
        with transaction(conn):
            member = scenes.enter_in_transaction(
                campaign_id, scene_id, entity_id, presence
            )
            EventStore(conn).append_in_transaction(
                conn, campaign_id,
                entity_entered_event(
                    scene_id=scene_id, entity_id=entity_id,
                    presence_type=presence.value, entered_at=member.entered_at,
                ),
                scene_id=scene_id, occurred_at=member.entered_at,
            )
        return {"command": command.value, "entity_id": entity_id}

    if command is GmMutatingCommand.SCENE_EXIT:
        with transaction(conn):
            scene_id, entity_id = args
            member = scenes.exit_in_transaction(campaign_id, scene_id, entity_id)
            EventStore(conn).append_in_transaction(
                conn, campaign_id,
                entity_exited_event(
                    scene_id=scene_id, entity_id=entity_id,
                    exited_at=member.exited_at or "",
                ),
                scene_id=scene_id, occurred_at=member.exited_at,
            )
        return {"command": command.value, "entity_id": entity_id}

    if command is GmMutatingCommand.SCENE_LOCATION:
        scene_id, raw_location = args
        location = None if raw_location.casefold() == "none" else raw_location
        with transaction(conn):
            scene = scenes.set_location_in_transaction(campaign_id, scene_id, location)
            EventStore(conn).append_in_transaction(
                conn, campaign_id,
                scene_location_changed_event(
                    scene_id=scene.scene_id, location_entity_id=location,
                    changed_at=scene.started_at,
                ),
                scene_id=scene.scene_id,
            )
        return {"command": command.value, "location_entity_id": location}

    if command is GmMutatingCommand.TIME_SET:
        with transaction(conn):
            clock = scenes.set_game_time_in_transaction(campaign_id, in_world_label=args[0])
            EventStore(conn).append_in_transaction(
                conn, campaign_id,
                scene_time_changed_event(
                    in_world_label=clock.in_world_label,
                    in_world_minutes=clock.in_world_minutes,
                    changed_at=clock.updated_at,
                ),
                occurred_at=clock.updated_at,
            )
        return {"command": command.value, "in_world_label": clock.in_world_label}

    if command is GmMutatingCommand.RULING_RECORD:
        ruling_id, scope = args
        # Recording does not confirm. A ruling starts proposed and unrevealed.
        ruling = RulingStore(conn).record(
            Ruling(
                ruling_id=ruling_id,
                campaign_id=campaign_id,
                system_id=str(
                    CampaignStore(conn).get_campaign(campaign_id)["system_id"]
                ),
                question=f"GM ruling recorded for {scope}",
                decision="recorded",
                scope=scope,
                source_references=(),
                session_id=None,
                created_at=datetime.now(timezone.utc).replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
            )
        )
        return {"command": command.value, "ruling_id": ruling.ruling_id,
                "canon_state": ruling.canon_state.value,
                "knowledge_state": ruling.knowledge_state.value}

    if command is GmMutatingCommand.RULING_PROMOTE:
        ruling = RulingStore(conn).promote(args[0])
        # Promoting confirms precedent. It does not reveal it to players.
        return {"command": command.value, "ruling_id": ruling.ruling_id,
                "canon_state": ruling.canon_state.value,
                "knowledge_state": ruling.knowledge_state.value}

    raise GmCommandError(f"{command.value} is not implemented")
