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
