"""Computed context budget, collection policy, and allocation tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import pytest

from tabletop.api.visibility import Viewpoint, parse_scope
from tabletop.api.workspace import Workspace
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState
from tabletop.orchestration.context import (
    ContextEntry,
    ContextRequest,
    ContextSource,
    allocate_entries,
    build_context,
    compute_budget,
    estimate_tokens,
)
from tabletop.retrieval.models import (
    RetrievalNamespace,
    RetrievedChunk,
    SourceReference,
)


@dataclass
class RecordingSource:
    records: dict[ContextSource, Sequence[object]]
    reads: list[ContextSource] = field(default_factory=list)

    def read(
        self,
        source: ContextSource,
        request: ContextRequest,
    ) -> Sequence[object]:
        self.reads.append(source)
        return self.records.get(source, ())


def _entry(
    content: str,
    *,
    priority: int,
    recency: int,
    token_cost: int = 1,
) -> ContextEntry:
    return ContextEntry(
        content=content,
        token_cost=token_cost,
        refetch_tool="fetch",
        refetch_args={"id": content},
        priority=priority,
        compacted=False,
        recency=recency,
    )


def _request(
    source: RecordingSource,
    *,
    workspace: Workspace = Workspace.CAMPAIGN,
    viewpoint: str = "GM",
    model_context_size: int = 100,
    prompt_reserve: int = 10,
    response_reserve: int = 20,
    session_reserve: int = 5,
    per_entry_ceiling: int = 30,
) -> ContextRequest:
    return ContextRequest(
        source=source,
        workspace=workspace,
        viewpoint=Viewpoint(scope=parse_scope(viewpoint)),
        campaign_id="campaign-1",
        query="dragon",
        model_context_size=model_context_size,
        prompt_reserve=prompt_reserve,
        response_reserve=response_reserve,
        session_reserve=session_reserve,
        per_entry_ceiling=per_entry_ceiling,
    )


def _fact(
    fact_id: str,
    *,
    scope: FactScope,
    value: str,
    visibility: str,
    canon: CanonState = CanonState.CONFIRMED,
    knowledge: KnowledgeState = KnowledgeState.KNOWN,
    created_at: str = "2026-09-22T01:00:00Z",
) -> Fact:
    return Fact(
        fact_id=fact_id,
        fact_scope=scope,
        setting_id="setting-1" if scope is FactScope.SETTING else None,
        campaign_id="campaign-1" if scope is FactScope.CAMPAIGN else None,
        subject_id="city",
        predicate="ruler",
        value=value,
        canon_state=canon,
        knowledge_state=knowledge,
        visibility=visibility,
        created_at=created_at,
    )


def _chunk(text: str, namespace: RetrievalNamespace) -> RetrievedChunk:
    return RetrievedChunk(
        text=text,
        score=1.0,
        namespace=namespace,
        source=SourceReference(
            chunk_id=f"{namespace.value}-chunk",
            document_id=f"{namespace.value}-document",
            document_title=namespace.value,
            section="section",
            page=None,
            source_path=f"/{namespace.value}.md",
        ),
    )


def test_context_entry_carries_required_fields_and_copies_refetch_args() -> None:
    args = {"entity_id": "npc-1"}
    entry = ContextEntry(
        content="The guide waits.",
        token_cost=4,
        refetch_tool="get-entity",
        refetch_args=args,
        priority=80,
        compacted=False,
    )
    args["entity_id"] = "changed"

    assert entry.content == "The guide waits."
    assert entry.token_cost == 4
    assert entry.refetch_tool == "get-entity"
    assert dict(entry.refetch_args) == {"entity_id": "npc-1"}
    assert entry.priority == 80
    assert entry.compacted is False


def test_budget_is_derived_from_model_context_and_all_reserves() -> None:
    assert compute_budget(128_000, 4_000, 8_000, 16_000) == 100_000
    assert compute_budget(32_000, 4_000, 8_000, 16_000) == 4_000
    with pytest.raises(ValueError, match="reserves exceed"):
        compute_budget(10, 4, 4, 4)


def test_allocation_orders_by_priority_then_newest_and_skips_oversized() -> None:
    entries = (
        _entry("old-high", priority=10, recency=1, token_cost=2),
        _entry("low", priority=1, recency=99, token_cost=1),
        _entry("new-high", priority=10, recency=2, token_cost=2),
        _entry("oversized", priority=100, recency=100, token_cost=20),
    )

    selected = allocate_entries(entries, budget=5, per_entry_ceiling=5)

    assert [entry.content for entry in selected] == [
        "new-high",
        "old-high",
        "low",
    ]


def test_one_entry_cannot_consume_the_budget_or_bypass_file_dump_ceiling() -> None:
    giant = _entry("full file dump", priority=100, recency=10, token_cost=10)
    useful = _entry("current scene", priority=90, recency=9, token_cost=3)

    selected = allocate_entries(
        (giant, useful),
        budget=10,
        per_entry_ceiling=4,
    )

    assert selected == (useful,)


def test_build_context_filters_every_fact_and_applies_campaign_precedence() -> None:
    source = RecordingSource(
        {
            ContextSource.SETTING_FACTS: (
                _fact(
                    "setting",
                    scope=FactScope.SETTING,
                    value="Queen Mara",
                    visibility="PUBLIC",
                ),
            ),
            ContextSource.FACTS: (
                _fact(
                    "campaign",
                    scope=FactScope.CAMPAIGN,
                    value="Regent Sol",
                    visibility="PUBLIC",
                    created_at="2026-09-22T02:00:00Z",
                ),
                _fact(
                    "secret",
                    scope=FactScope.CAMPAIGN,
                    value="Hidden Regent",
                    visibility="GM",
                ),
                _fact(
                    "unknown",
                    scope=FactScope.CAMPAIGN,
                    value="Rumor",
                    visibility="PUBLIC",
                    knowledge=KnowledgeState.UNREVEALED,
                ),
            )
        }
    )

    context = build_context(_request(source, viewpoint="CHARACTER:hero"))
    content = "\n".join(entry.content for entry in context.entries)

    assert "Regent Sol" in content
    assert "Queen Mara" not in content
    assert "Hidden Regent" not in content
    assert "Rumor" not in content


def test_player_context_never_reads_or_contains_gm_npc_agendas() -> None:
    source = RecordingSource(
        {ContextSource.NPC_AGENDAS: ("Betray the party at dawn.",)}
    )

    context = build_context(_request(source, viewpoint="CHARACTER:hero"))

    assert ContextSource.NPC_AGENDAS not in source.reads
    assert all("Betray" not in entry.content for entry in context.entries)


def test_workspace_bounds_sources_before_the_provider_is_read() -> None:
    source = RecordingSource(
        {
            ContextSource.SETTING_FACTS: ("The old empire fell.",),
            ContextSource.CURRENT_SCENE: ("Forbidden campaign scene",),
            ContextSource.CAMPAIGN_RULINGS: ("Forbidden campaign ruling",),
        }
    )

    context = build_context(_request(source, workspace=Workspace.SETTING))

    assert set(source.reads) == {
        ContextSource.SETTING_FACTS,
        ContextSource.SYSTEM_INFO,
        ContextSource.RETRIEVED_RULES,
    }
    assert all("Forbidden" not in entry.content for entry in context.entries)


def test_build_context_gathers_all_campaign_categories_as_entries() -> None:
    records: dict[ContextSource, Sequence[object]] = {
        source: (f"{source.value} record",)
        for source in ContextSource
    }
    records[ContextSource.RETRIEVED_RULES] = (
        _chunk("dragon rule", RetrievalNamespace.SYSTEM),
    )
    records[ContextSource.CAMPAIGN_RULINGS] = (
        _chunk("campaign dragon ruling", RetrievalNamespace.RULINGS),
    )
    source = RecordingSource(records)

    context = build_context(_request(source))

    expected = set(ContextSource)
    assert set(source.reads) == expected
    assert {entry.source for entry in context.entries} == expected
    assert context.budget == 65
    assert context.used_tokens == sum(entry.token_cost for entry in context.entries)
    assert all(entry.token_cost == estimate_tokens(entry.content) for entry in context.entries)
