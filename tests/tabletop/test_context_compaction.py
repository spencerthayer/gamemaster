"""Refetch-based context compaction tests."""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from typing import Mapping

from tabletop.api.workspace import Workspace
from tabletop.orchestration.context import ContextEntry, compact


def _entry(
    content: str,
    *,
    token_cost: int,
    priority: int,
    recency: int,
    refetch_tool: str | None = "get-entity",
    refetch_args: Mapping[str, object] | None = None,
) -> ContextEntry:
    return ContextEntry(
        content=content,
        token_cost=token_cost,
        refetch_tool=refetch_tool,
        refetch_args=refetch_args or {"entity_id": content.lower()},
        priority=priority,
        compacted=False,
        recency=recency,
    )


@dataclass
class SkillRuntime:
    entities: Mapping[str, object]

    def get_entity(self, entity_id: str) -> object:
        return self.entities[entity_id]


def _execute_campaign_skill(
    runtime: SkillRuntime,
    tool_name: str,
    arguments: Mapping[str, object],
) -> object:
    skill = next(skill for skill in Workspace.CAMPAIGN.skills if skill.name == tool_name)
    return getattr(runtime, skill.runtime_method)(**arguments)


def test_compaction_creates_exact_refetchable_stub_and_round_trips() -> None:
    original = {"entity_id": "ithrazel", "name": "Ithrazel", "kind": "dragon"}
    entry = _entry(
        "entity record: Ithrazel",
        token_cost=100,
        priority=10,
        recency=1,
        refetch_args={"entity_id": "ithrazel"},
    )

    result = compact((entry,), budget=30)

    assert len(result) == 1
    stub = result[0]
    assert stub.content == (
        '[Compacted entity record: Ithrazel. Refetch with get-entity("ithrazel").]'
    )
    assert stub.compacted is True
    runtime = SkillRuntime(entities={"ithrazel": original})
    refetched = _execute_campaign_skill(
        runtime,
        stub.refetch_tool or "",
        stub.refetch_args,
    )
    assert refetched == original


def test_compaction_drops_an_entry_that_cannot_be_refetched() -> None:
    unrefetchable = _entry(
        "transient narration",
        token_cost=80,
        priority=1,
        recency=1,
        refetch_tool=None,
        refetch_args={},
    )
    durable = _entry(
        "Ithrazel",
        token_cost=80,
        priority=10,
        recency=2,
        refetch_args={"entity_id": "ithrazel"},
    )

    assert compact((durable, unrefetchable), budget=80) == (durable,)


def test_compaction_selects_lowest_priority_then_oldest() -> None:
    low_old = _entry("low old", token_cost=80, priority=1, recency=1)
    low_new = _entry("low new", token_cost=80, priority=1, recency=2)
    high_old = _entry("high old", token_cost=80, priority=2, recency=1)

    result = compact((high_old, low_new, low_old), budget=180)

    by_original_content = {
        entry.content.replace("[Compacted ", "").split(". Refetch", 1)[0]: entry
        for entry in result
    }
    assert by_original_content["low old"].compacted is True
    assert by_original_content["low new"].compacted is False
    assert by_original_content["high old"].compacted is False


def test_compaction_never_imports_a_provider_or_model_module(monkeypatch) -> None:
    imported: list[str] = []
    real_import = builtins.__import__

    def recording_import(name, *args, **kwargs):
        imported.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", recording_import)
    compact(
        (
            _entry(
                "entity record: Ithrazel",
                token_cost=100,
                priority=1,
                recency=1,
                refetch_args={"entity_id": "ithrazel"},
            ),
        ),
        budget=30,
    )

    assert all("provider" not in name and "model" not in name for name in imported)
