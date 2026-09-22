"""Durable campaign rulings and their canon lifecycle."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from tabletop.api.events import GameEvent
from tabletop.api.rules import RuleReference
from tabletop.campaign.event_store import EventStore, EventType
from tabletop.campaign.invariants import check_canon_knowledge_invariants
from tabletop.campaign.models import CanonState, KnowledgeState
from tabletop.orchestration.adjudication import AdjudicationResult
from tabletop.retrieval.models import (
    RetrievalNamespace,
    RetrievedChunk,
    SourceReference,
)
from tabletop.storage.sqlite import transaction


@dataclass(frozen=True, kw_only=True)
class Ruling:
    """A campaign-scoped GM decision with independent canon and knowledge state."""

    ruling_id: str
    campaign_id: str
    system_id: str
    question: str
    decision: str
    scope: str
    source_references: tuple[RuleReference, ...]
    session_id: str | None
    created_at: str
    supersedes: str | None = None
    canon_state: CanonState = CanonState.PROPOSED
    knowledge_state: KnowledgeState = KnowledgeState.UNREVEALED
    originating_action: Mapping[str, Any] | None = None
    originating_context: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for name in (
            "ruling_id",
            "campaign_id",
            "system_id",
            "question",
            "decision",
            "scope",
            "created_at",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        for name in ("session_id", "supersedes"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(f"{name} must be None or a non-empty string")
        if self.supersedes == self.ruling_id:
            raise ValueError("a ruling cannot supersede itself")
        references = tuple(self.source_references)
        if not all(isinstance(reference, RuleReference) for reference in references):
            raise TypeError("source_references must contain only RuleReference values")
        object.__setattr__(self, "source_references", references)
        object.__setattr__(self, "canon_state", CanonState(self.canon_state))
        object.__setattr__(
            self, "knowledge_state", KnowledgeState(self.knowledge_state)
        )
        check_canon_knowledge_invariants(
            self.canon_state,
            self.knowledge_state,
            record_type="ruling",
        )
        for name in ("originating_action", "originating_context"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, MappingProxyType(dict(value)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ruling_id": self.ruling_id,
            "campaign_id": self.campaign_id,
            "system_id": self.system_id,
            "question": self.question,
            "decision": self.decision,
            "scope": self.scope,
            "source_references": [
                reference.to_dict() for reference in self.source_references
            ],
            "session_id": self.session_id,
            "created_at": self.created_at,
            "supersedes": self.supersedes,
            "canon_state": self.canon_state.value,
            "knowledge_state": self.knowledge_state.value,
            "originating_action": (
                None
                if self.originating_action is None
                else dict(self.originating_action)
            ),
            "originating_context": (
                None
                if self.originating_context is None
                else dict(self.originating_context)
            ),
        }


def ruling_from_adjudication(
    result: AdjudicationResult,
    *,
    ruling_id: str,
    scope: str,
    session_id: str | None,
    created_at: str,
    supersedes: str | None = None,
    canon_state: CanonState = CanonState.PROPOSED,
    knowledge_state: KnowledgeState = KnowledgeState.UNREVEALED,
) -> Ruling:
    """Convert an accepted adjudication without dropping its origin."""
    request = result.request
    return Ruling(
        ruling_id=ruling_id,
        campaign_id=request.context.campaign_id,
        system_id=request.context.system_id,
        question=request.detail,
        decision=result.decision,
        scope=scope,
        source_references=request.rule_references,
        session_id=session_id,
        created_at=created_at,
        supersedes=supersedes,
        canon_state=canon_state,
        knowledge_state=knowledge_state,
        originating_action=request.action.to_dict(),
        originating_context=request.context.to_dict(),
    )


class RulingStore:
    """SQLite persistence and lifecycle operations for campaign rulings."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record(self, ruling: Ruling) -> Ruling:
        """Store one ruling and append ``ruling.recorded`` atomically."""
        if ruling.supersedes is not None:
            superseded = self.get(ruling.supersedes)
            if superseded is None:
                raise LookupError(f"superseded ruling not found: {ruling.supersedes}")
            if (
                superseded.campaign_id != ruling.campaign_id
                or superseded.system_id != ruling.system_id
            ):
                raise ValueError(
                    "a ruling may supersede only a ruling from the same campaign "
                    "and system"
                )

        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO rulings "
                "(ruling_id, campaign_id, system_id, question, decision, scope, "
                "source_references, session_id, created_at, supersedes, canon_state, "
                "knowledge_state, originating_action, originating_context) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ruling.ruling_id,
                    ruling.campaign_id,
                    ruling.system_id,
                    ruling.question,
                    ruling.decision,
                    ruling.scope,
                    _encode_json(
                        [
                            reference.to_dict()
                            for reference in ruling.source_references
                        ]
                    ),
                    ruling.session_id,
                    ruling.created_at,
                    ruling.supersedes,
                    ruling.canon_state.value,
                    ruling.knowledge_state.value,
                    _encode_optional_mapping(ruling.originating_action),
                    _encode_optional_mapping(ruling.originating_context),
                ),
            )
            EventStore(self.conn).append_in_transaction(
                self.conn,
                ruling.campaign_id,
                GameEvent(
                    event_type=EventType.RULING_RECORDED.value,
                    payload={
                        "ruling_id": ruling.ruling_id,
                        "canon_state": ruling.canon_state.value,
                    },
                ),
                session_id=ruling.session_id,
                occurred_at=ruling.created_at,
            )
        return ruling

    def get(self, ruling_id: str) -> Ruling | None:
        row = self.conn.execute(
            "SELECT ruling_id, campaign_id, system_id, question, decision, scope, "
            "source_references, session_id, created_at, supersedes, canon_state, "
            "knowledge_state, originating_action, originating_context "
            "FROM rulings WHERE ruling_id = ?",
            (ruling_id,),
        ).fetchone()
        return None if row is None else _ruling_from_row(row)

    def promote(self, ruling_id: str) -> Ruling:
        """Confirm a proposed ruling without changing player knowledge."""
        ruling = self.get(ruling_id)
        if ruling is None:
            raise LookupError(f"ruling not found: {ruling_id}")
        promoted = replace(ruling, canon_state=CanonState.CONFIRMED)
        with transaction(self.conn):
            cursor = self.conn.execute(
                "UPDATE rulings SET canon_state = ? WHERE ruling_id = ?",
                (promoted.canon_state.value, ruling_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"ruling not found: {ruling_id}")
            EventStore(self.conn).append_in_transaction(
                self.conn,
                ruling.campaign_id,
                GameEvent(
                    event_type=EventType.FACT_PROMOTED.value,
                    payload={"ruling_id": ruling_id},
                ),
                session_id=ruling.session_id,
            )
        return promoted

    def search(
        self,
        query: str,
        campaign_id: str,
        *,
        system_id: str | None = None,
        limit: int = 10,
    ) -> tuple[Ruling, ...]:
        """Search confirmed rulings in one campaign without touching documents."""
        if limit <= 0:
            raise ValueError("limit must be positive")
        pattern = f"%{query}%"
        rows = self.conn.execute(
            "SELECT ruling_id, campaign_id, system_id, question, decision, scope, "
            "source_references, session_id, created_at, supersedes, canon_state, "
            "knowledge_state, originating_action, originating_context "
            "FROM rulings "
            "WHERE campaign_id = ? AND canon_state = 'confirmed' "
            "AND (? IS NULL OR system_id = ?) "
            "AND NOT EXISTS ("
            "SELECT 1 FROM rulings AS successor "
            "WHERE successor.supersedes = rulings.ruling_id "
            "AND successor.campaign_id = rulings.campaign_id "
            "AND successor.canon_state = 'confirmed'"
            ") "
            "AND (question LIKE ? OR decision LIKE ? OR scope LIKE ?) "
            "ORDER BY created_at DESC, ruling_id LIMIT ?",
            (
                campaign_id,
                system_id,
                system_id,
                pattern,
                pattern,
                pattern,
                limit,
            ),
        ).fetchall()
        return tuple(_ruling_from_row(row) for row in rows)


class TierSearch(Protocol):
    def search_tier(
        self, query: str, campaign_id: str, tier: str
    ) -> tuple[RetrievedChunk, ...]:
        """Search one mechanics precedence tier."""


class RulingAwareTierSearch:
    """Route the highest mechanics tier to rulings, then use normal retrieval."""

    def __init__(self, rulings: RulingStore, fallback: TierSearch) -> None:
        self._rulings = rulings
        self._fallback = fallback

    def search_tier(
        self, query: str, campaign_id: str, tier: str
    ) -> tuple[RetrievedChunk, ...]:
        if tier != "campaign rulings":
            return self._fallback.search_tier(query, campaign_id, tier)
        return tuple(_retrieved_ruling(ruling) for ruling in self._rulings.search(
            query, campaign_id
        ))


def _retrieved_ruling(ruling: Ruling) -> RetrievedChunk:
    return RetrievedChunk(
        text=ruling.decision,
        score=1.0,
        namespace=RetrievalNamespace.RULINGS,
        source=SourceReference(
            chunk_id=ruling.ruling_id,
            document_id=f"ruling:{ruling.ruling_id}",
            document_title=f"Campaign ruling: {ruling.question}",
            section=ruling.scope,
            page=None,
            source_path="",
            refetch_tool="get_ruling",
        ),
    )


def _ruling_from_row(row: sqlite3.Row) -> Ruling:
    references = json.loads(row["source_references"])
    return Ruling(
        ruling_id=row["ruling_id"],
        campaign_id=row["campaign_id"],
        system_id=row["system_id"],
        question=row["question"],
        decision=row["decision"],
        scope=row["scope"],
        source_references=tuple(RuleReference(**reference) for reference in references),
        session_id=row["session_id"],
        created_at=row["created_at"],
        supersedes=row["supersedes"],
        canon_state=CanonState(row["canon_state"]),
        knowledge_state=KnowledgeState(row["knowledge_state"]),
        originating_action=_decode_optional_mapping(row["originating_action"]),
        originating_context=_decode_optional_mapping(row["originating_context"]),
    )


def _encode_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _encode_optional_mapping(value: Mapping[str, Any] | None) -> str | None:
    return None if value is None else _encode_json(dict(value))


def _decode_optional_mapping(value: str | None) -> Mapping[str, Any] | None:
    return None if value is None else json.loads(value)
