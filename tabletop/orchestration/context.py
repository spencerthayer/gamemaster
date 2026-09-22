"""Budgeted, viewpoint-aware prompt context assembly.

Token counts use one deliberately simple approximation: four UTF-8 bytes per
token, rounded up. Keeping that estimate in :func:`estimate_tokens` lets a
model tokenizer replace it later without changing collection or allocation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence

from tabletop.api.errors import VisibilityScopeError
from tabletop.api.visibility import Viewpoint, VisibilityKind, can_see, parse_scope
from tabletop.api.workspace import Workspace
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState
from tabletop.retrieval.models import RetrievedChunk


class ContextSource(str, Enum):
    """Logical source categories available to context assembly."""

    CURRENT_SCENE = "current_scene"
    ACTIVE_ENTITIES = "active_entities"
    FACTS = "facts"
    RECENT_EVENTS = "recent_events"
    RELEVANT_HISTORY = "relevant_history"
    NPC_AGENDAS = "npc_agendas"
    RELATIONSHIPS = "relationships"
    SYSTEM_INFO = "system_info"
    RETRIEVED_RULES = "retrieved_rules"
    CAMPAIGN_RULINGS = "campaign_rulings"
    SETTING_FACTS = "setting_facts"


_SETTING_SOURCES = (
    ContextSource.SETTING_FACTS,
    ContextSource.SYSTEM_INFO,
    ContextSource.RETRIEVED_RULES,
)

_CAMPAIGN_SOURCES = tuple(ContextSource)

_SOURCE_PRIORITIES: Mapping[ContextSource, int] = MappingProxyType(
    {
        ContextSource.CURRENT_SCENE: 100,
        ContextSource.ACTIVE_ENTITIES: 95,
        ContextSource.FACTS: 90,
        ContextSource.NPC_AGENDAS: 88,
        ContextSource.CAMPAIGN_RULINGS: 85,
        ContextSource.SYSTEM_INFO: 80,
        ContextSource.RELATIONSHIPS: 75,
        ContextSource.RECENT_EVENTS: 70,
        ContextSource.RETRIEVED_RULES: 65,
        ContextSource.RELEVANT_HISTORY: 60,
        ContextSource.SETTING_FACTS: 55,
    }
)


class ContextProvider(Protocol):
    """Read records from one explicitly selected logical source."""

    def read(
        self,
        source: ContextSource,
        request: ContextRequest,
    ) -> Sequence[object]:
        """Return records from ``source`` for this request."""


@dataclass(frozen=True, slots=True)
class ContextEntry:
    """One independently budgeted prompt-context record."""

    content: str
    token_cost: int
    refetch_tool: str | None
    refetch_args: Mapping[str, object]
    priority: int
    compacted: bool
    recency: int | float = 0
    source: ContextSource | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("context entry content must be non-empty")
        if self.token_cost <= 0:
            raise ValueError("context entry token_cost must be positive")
        if self.priority < 0:
            raise ValueError("context entry priority cannot be negative")
        object.__setattr__(
            self,
            "refetch_args",
            MappingProxyType(dict(self.refetch_args)),
        )


@dataclass(frozen=True, slots=True)
class ContextRequest:
    """Inputs and hard limits for one context build."""

    source: ContextProvider
    workspace: Workspace
    viewpoint: Viewpoint
    campaign_id: str | None
    query: str
    model_context_size: int
    prompt_reserve: int
    response_reserve: int
    session_reserve: int
    per_entry_ceiling: int

    def __post_init__(self) -> None:
        if not isinstance(self.workspace, Workspace):
            raise TypeError("workspace must be a Workspace")
        if not isinstance(self.viewpoint, Viewpoint):
            raise TypeError("viewpoint must be a Viewpoint")
        if self.per_entry_ceiling <= 0:
            raise ValueError("per_entry_ceiling must be positive")
        if self.workspace is Workspace.CAMPAIGN and not self.campaign_id:
            raise ValueError("campaign workspace requires campaign_id")


@dataclass(frozen=True, slots=True)
class Context:
    """Selected entries and the computed token accounting."""

    entries: tuple[ContextEntry, ...]
    budget: int
    used_tokens: int


def estimate_tokens(content: str) -> int:
    """Estimate tokens as four UTF-8 bytes each, rounded up."""

    if not isinstance(content, str):
        raise TypeError("content must be a string")
    byte_count = len(content.encode("utf-8"))
    return (byte_count + 3) // 4


def compute_budget(
    model_context_size: int,
    prompt_reserve: int,
    response_reserve: int,
    session_reserve: int,
) -> int:
    """Return model capacity left after all reserved token regions."""

    values = {
        "model_context_size": model_context_size,
        "prompt_reserve": prompt_reserve,
        "response_reserve": response_reserve,
        "session_reserve": session_reserve,
    }
    if any(not isinstance(value, int) for value in values.values()):
        raise TypeError("context size and reserves must be integers")
    if model_context_size <= 0:
        raise ValueError("model_context_size must be positive")
    if any(value < 0 for name, value in values.items() if name != "model_context_size"):
        raise ValueError("context reserves cannot be negative")
    budget = (
        model_context_size
        - prompt_reserve
        - response_reserve
        - session_reserve
    )
    if budget < 0:
        raise ValueError("context reserves exceed the model context size")
    return budget


def allocate_entries(
    entries: Sequence[ContextEntry],
    *,
    budget: int,
    per_entry_ceiling: int,
) -> tuple[ContextEntry, ...]:
    """Select whole entries by priority, then recency, within hard limits."""

    if budget < 0:
        raise ValueError("budget cannot be negative")
    if per_entry_ceiling <= 0:
        raise ValueError("per_entry_ceiling must be positive")

    selected: list[ContextEntry] = []
    remaining = budget
    ordered = sorted(
        entries,
        key=lambda entry: (entry.priority, entry.recency),
        reverse=True,
    )
    for entry in ordered:
        if entry.token_cost > per_entry_ceiling:
            continue
        if entry.token_cost > remaining:
            continue
        selected.append(entry)
        remaining -= entry.token_cost
    return tuple(selected)


def compact(
    entries: Sequence[ContextEntry],
    budget: int,
) -> tuple[ContextEntry, ...]:
    """Replace aged entries with deterministic calls that can reload them."""

    if budget < 0:
        raise ValueError("budget cannot be negative")

    result: list[ContextEntry | None] = list(entries)
    used_tokens = sum(entry.token_cost for entry in entries)
    candidates = sorted(
        enumerate(entries),
        key=lambda indexed: (
            indexed[1].priority,
            indexed[1].recency,
            indexed[0],
        ),
    )
    for index, entry in candidates:
        if used_tokens <= budget:
            break
        if entry.compacted:
            continue
        if not entry.refetch_tool or not entry.refetch_tool.strip():
            result[index] = None
            used_tokens -= entry.token_cost
            continue

        arguments = ",".join(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            for _, value in sorted(entry.refetch_args.items())
        )
        content = (
            f"[Compacted {entry.content}. "
            f"Refetch with {entry.refetch_tool}({arguments}).]"
        )
        compacted = replace(
            entry,
            content=content,
            token_cost=estimate_tokens(content),
            compacted=True,
        )
        result[index] = compacted
        used_tokens += compacted.token_cost - entry.token_cost

    return tuple(entry for entry in result if entry is not None)


def build_context(request: ContextRequest) -> Context:
    """Collect allowed records, enforce visibility and precedence, then allocate."""

    budget = compute_budget(
        request.model_context_size,
        request.prompt_reserve,
        request.response_reserve,
        request.session_reserve,
    )
    gathered: list[tuple[ContextSource, object]] = []
    for source in _sources_for(request):
        gathered.extend(
            (source, record) for record in request.source.read(source, request)
        )
    gathered = list(_visible_precedence_records(gathered, request.viewpoint))

    records: list[ContextEntry] = []
    collection_index = 0
    for source, raw_record in gathered:
        collection_index += 1
        recency = _record_recency(raw_record, collection_index)
        entry = _entry_from_record(raw_record, source, recency)
        if entry is not None:
            records.append(entry)

    entries = allocate_entries(
        records,
        budget=budget,
        per_entry_ceiling=request.per_entry_ceiling,
    )
    return Context(
        entries=entries,
        budget=budget,
        used_tokens=sum(entry.token_cost for entry in entries),
    )


def _sources_for(request: ContextRequest) -> tuple[ContextSource, ...]:
    if request.workspace is Workspace.SETTING:
        return _SETTING_SOURCES
    if request.viewpoint.scope.kind is VisibilityKind.GM:
        return _CAMPAIGN_SOURCES
    return tuple(
        source
        for source in _CAMPAIGN_SOURCES
        if source is not ContextSource.NPC_AGENDAS
    )


def _visible_precedence_records(
    records: Sequence[tuple[ContextSource, object]],
    viewpoint: Viewpoint,
) -> tuple[tuple[ContextSource, object], ...]:
    passthrough: list[tuple[ContextSource, object]] = []
    winners: dict[tuple[str | None, str], tuple[ContextSource, Fact]] = {}
    for source, record in records:
        if not isinstance(record, Fact):
            if source in {ContextSource.FACTS, ContextSource.SETTING_FACTS}:
                continue
            passthrough.append((source, record))
            continue
        if not _fact_is_visible(record, viewpoint):
            continue
        key = (record.subject_id, record.predicate)
        current = winners.get(key)
        if current is None or _fact_precedence(record) > _fact_precedence(current[1]):
            winners[key] = (source, record)
    return (*passthrough, *winners.values())


def _fact_is_visible(fact: Fact, viewpoint: Viewpoint) -> bool:
    try:
        visible = can_see(viewpoint, parse_scope(fact.visibility))
    except VisibilityScopeError:
        return False
    if not visible:
        return False
    if viewpoint.scope.kind is VisibilityKind.GM:
        return True
    return (
        fact.canon_state is CanonState.CONFIRMED
        and fact.knowledge_state is KnowledgeState.KNOWN
    )


def _fact_precedence(fact: Fact) -> tuple[int, str]:
    scope_rank = 1 if fact.fact_scope is FactScope.CAMPAIGN else 0
    return scope_rank, fact.created_at


def _record_recency(record: object, fallback: int) -> int | float:
    sequence = _record_field(record, "sequence")
    if isinstance(sequence, (int, float)) and not isinstance(sequence, bool):
        return sequence

    for field_name in ("occurred_at", "created_at", "updated_at", "timestamp"):
        timestamp = _record_field(record, field_name)
        if isinstance(timestamp, datetime):
            return timestamp.timestamp()
        if not isinstance(timestamp, str):
            continue
        try:
            return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return fallback


def _record_field(record: object, field_name: str) -> object | None:
    if isinstance(record, Mapping):
        return record.get(field_name)
    return getattr(record, field_name, None)


def _entry_from_record(
    record: object,
    source: ContextSource,
    recency: int | float,
) -> ContextEntry | None:
    content: str
    refetch_tool: str | None = None
    refetch_args: Mapping[str, object] = {}

    if isinstance(record, Fact):
        subject = f"{record.subject_id} " if record.subject_id else ""
        content = f"{subject}{record.predicate}: {record.value}"
        refetch_tool = "get-fact"
        refetch_args = {"fact_id": record.fact_id}
    elif isinstance(record, RetrievedChunk):
        content = record.text
        refetch_tool = record.source.refetch_tool
        refetch_args = record.source.refetch_args
    elif isinstance(record, str):
        content = record
    elif isinstance(record, Mapping):
        content = json.dumps(
            dict(record),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    else:
        content = str(record)

    content = content.strip()
    if not content:
        return None
    token_cost = estimate_tokens(content)
    if token_cost <= 0:
        return None
    return ContextEntry(
        content=content,
        token_cost=token_cost,
        refetch_tool=refetch_tool,
        refetch_args=refetch_args,
        priority=_SOURCE_PRIORITIES[source],
        compacted=False,
        recency=recency,
        source=source,
    )
