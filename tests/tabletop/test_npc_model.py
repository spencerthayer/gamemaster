"""System-agnostic NPC model tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

from tabletop.campaign.models import NpcRecord
from tabletop.campaign.npc import (
    ClockAdvanceEvent,
    PlayerNpcProjection,
    advance_clock,
    fetch_npc,
    list_npc_index,
    project_for_player,
)


def _npc(entity_id: str = "npc-1", name: str = "Mara Venn") -> NpcRecord:
    return NpcRecord(
        entity_id=entity_id,
        identity={"name": name, "pronouns": "she/her"},
        public={"summary": "A guarded harbor pilot.", "demeanor": "reserved"},
        private={"true_allegiance": "The Tide Court"},
        knowledge={"smuggler_route": "beneath the east pier"},
        agenda={"goal": "Protect her missing brother"},
        relationships=("faction-tide-court",),
        clocks={"trust": 1},
        system_state={"custom_rules": {"rank": "veteran"}},
    )


def test_npc_record_is_frozen_and_system_agnostic() -> None:
    npc = _npc()

    assert {field.name for field in fields(npc)} == {
        "entity_id",
        "identity",
        "public",
        "private",
        "knowledge",
        "agenda",
        "relationships",
        "clocks",
        "system_state",
    }
    assert not {"health", "level", "class"} & set(npc.system_state)
    with pytest.raises(FrozenInstanceError):
        npc.entity_id = "npc-2"  # type: ignore[misc]
    with pytest.raises(TypeError):
        npc.clocks["trust"] = 2  # type: ignore[index]


def test_index_lists_only_names_and_one_line_summaries() -> None:
    records = (
        _npc(),
        _npc(entity_id="npc-2", name="Old Fen"),
    )

    entries = list_npc_index(records)

    assert [(entry.entity_id, entry.name, entry.summary) for entry in entries] == [
        ("npc-1", "Mara Venn", "A guarded harbor pilot."),
        ("npc-2", "Old Fen", "A guarded harbor pilot."),
    ]
    assert {field.name for field in fields(entries[0])} == {
        "entity_id",
        "name",
        "summary",
    }
    assert fetch_npc(records, "npc-1") is records[0]
    with pytest.raises(LookupError, match="missing"):
        fetch_npc(records, "missing")


def test_player_projection_is_a_whitelisted_type_without_private_fields() -> None:
    npc = _npc()

    projection = project_for_player(npc)

    assert isinstance(projection, PlayerNpcProjection)
    assert projection.entity_id == npc.entity_id
    assert projection.identity == npc.identity
    assert projection.public == npc.public
    assert projection.relationships == npc.relationships
    assert projection.clocks == npc.clocks
    assert projection.system_state == npc.system_state
    assert not {"private", "knowledge", "agenda"} & {
        field.name for field in fields(projection)
    }


def test_clock_advances_only_by_applying_an_event_to_a_new_record() -> None:
    npc = _npc()
    event = ClockAdvanceEvent(clock="trust", amount=2)

    advanced = advance_clock(npc, event)

    assert advanced is not npc
    assert npc.clocks == {"trust": 1}
    assert advanced.clocks == {"trust": 3}
    assert advanced.identity == npc.identity
    with pytest.raises(ValueError, match="positive"):
        advance_clock(npc, ClockAdvanceEvent(clock="trust", amount=0))
    with pytest.raises(LookupError, match="missing"):
        advance_clock(npc, ClockAdvanceEvent(clock="missing", amount=1))
