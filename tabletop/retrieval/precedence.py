"""Resolve mechanics and lore retrieval using separate precedence policies."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Protocol

from tabletop.retrieval.models import RetrievalNamespace, RetrievedChunk

MECHANICS_PRECEDENCE = (
    "campaign rulings",
    "campaign house rules",
    "adventure-specific mechanics",
    "enabled supplements",
    "active system rules",
    "GM adjudication",
)

LORE_PRECEDENCE = (
    "campaign-specific canon",
    "shared setting canon",
    "adventure canon",
    "source material",
    "GM adjudication",
)

_POLICIES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "mechanics": MECHANICS_PRECEDENCE,
        "lore": LORE_PRECEDENCE,
    }
)


class TierSearch(Protocol):
    """Search one logical precedence tier within one campaign context."""

    def search_tier(
        self, query: str, campaign_id: str, tier: str
    ) -> tuple[RetrievedChunk, ...]:
        """Return ranked answers from exactly one logical tier."""


@dataclass(frozen=True)
class AdvisoryConflict:
    """A lower-precedence answer that differs from the selected answer."""

    winning_tier: str
    winner: RetrievedChunk
    conflicting_tier: str
    conflicting: RetrievedChunk


@dataclass(frozen=True)
class PrecedenceResult:
    """The selected answer and lower-tier contradictions visible to the GM."""

    answer: RetrievedChunk | None
    tier: str | None
    conflicts: tuple[AdvisoryConflict, ...] = ()


class PrecedenceResolver:
    """Apply query-time precedence without mutating any source record."""

    def __init__(self, search: TierSearch) -> None:
        self._search = search

    def resolve(
        self, query: str, campaign_id: str, kind: str
    ) -> PrecedenceResult:
        """Resolve a mechanics or lore query under its dedicated policy."""
        try:
            policy = _POLICIES[kind]
        except KeyError as exc:
            raise ValueError(f"unknown precedence kind: {kind!r}") from exc

        winner: RetrievedChunk | None = None
        winning_tier: str | None = None
        lower_answers: list[tuple[str, RetrievedChunk]] = []

        for tier in policy:
            answers = self._search.search_tier(query, campaign_id, tier)
            if not answers:
                continue
            if winner is None:
                winner = answers[0]
                winning_tier = tier
                continue
            lower_answers.extend((tier, answer) for answer in answers)

        if winner is None or winning_tier is None:
            return PrecedenceResult(answer=None, tier=None)

        winner_text = _normalized_answer(winner)
        conflicts = tuple(
            AdvisoryConflict(
                winning_tier=winning_tier,
                winner=winner,
                conflicting_tier=tier,
                conflicting=answer,
            )
            for tier, answer in lower_answers
            if _normalized_answer(answer) != winner_text
        )
        return PrecedenceResult(
            answer=winner,
            tier=winning_tier,
            conflicts=conflicts,
        )


def _normalized_answer(answer: RetrievedChunk) -> str:
    return " ".join(answer.text.casefold().split())
