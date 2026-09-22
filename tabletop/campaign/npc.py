"""Safe reads and event-driven updates for NPC records."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from tabletop.campaign.models import NpcRecord


@dataclass(frozen=True, kw_only=True)
class NpcIndexEntry:
    """The small NPC representation used by index listings."""

    entity_id: str
    name: str
    summary: str


@dataclass(frozen=True, kw_only=True)
class PlayerNpcProjection:
    """NPC fields explicitly approved for player-facing reads."""

    entity_id: str
    identity: Mapping[str, Any]
    public: Mapping[str, Any]
    relationships: tuple[Any, ...]
    clocks: Mapping[str, int]
    system_state: Mapping[str, Any]


@dataclass(frozen=True, kw_only=True)
class ClockAdvanceEvent:
    """A request to advance one named NPC clock."""

    clock: str
    amount: int = 1


def list_npc_index(records: Iterable[NpcRecord]) -> tuple[NpcIndexEntry, ...]:
    """Return only NPC names and one-line public summaries."""

    return tuple(
        NpcIndexEntry(
            entity_id=record.entity_id,
            name=str(record.identity["name"]),
            summary=" ".join(str(record.public["summary"]).splitlines()),
        )
        for record in records
    )


def fetch_npc(records: Iterable[NpcRecord], entity_id: str) -> NpcRecord:
    """Explicitly fetch one full NPC record."""

    for record in records:
        if record.entity_id == entity_id:
            return record
    raise LookupError(f"NPC {entity_id!r} is missing")


def project_for_player(record: NpcRecord) -> PlayerNpcProjection:
    """Build a player projection from an explicit field whitelist."""

    return PlayerNpcProjection(
        entity_id=record.entity_id,
        identity=record.identity,
        public=record.public,
        relationships=record.relationships,
        clocks=record.clocks,
        system_state=record.system_state,
    )


def advance_clock(record: NpcRecord, event: ClockAdvanceEvent) -> NpcRecord:
    """Apply a clock event and return a new immutable NPC record."""

    if event.amount <= 0:
        raise ValueError("clock advance amount must be positive")
    if event.clock not in record.clocks:
        raise LookupError(f"NPC clock {event.clock!r} is missing")

    clocks = dict(record.clocks)
    clocks[event.clock] += event.amount
    return replace(record, clocks=clocks)
