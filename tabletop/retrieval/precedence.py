"""Resolve mechanics and lore retrieval using separate precedence policies."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence

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


#: Tier for each semantic role. Authority comes from the role, never from a
#: score: a perfectly matching campaign note must not outrank attached rules.
#:
#: ``MECHANICS_PRECEDENCE`` is in descending authority order, so an earlier
#: tier wins. A character sheet is not campaign house rules, so `character`
#: falls through to source material with the other non-authoritative roles.
ROLE_TIERS: Mapping[str, str] = MappingProxyType(
    {
        "adventure": "adventure-specific mechanics",
        "setting": "enabled supplements",
        "rules": "active system rules",
        "character": "source material",
        "notes": "source material",
        "reference": "source material",
    }
)


def select_by_authority(
    answers: Sequence["RetrievedChunk"],
    *,
    roles: Mapping[str, str],
    kind: str = "mechanics",
) -> PrecedenceResult | None:
    """Pick the highest-tier answer, breaking ties only inside one tier.

    ``roles`` maps a chunk's source id to its semantic role. A chunk with no
    known role is treated as ``source material``: unclassified text cannot
    claim authority it was never granted.
    """
    try:
        policy = _POLICIES[kind]
    except KeyError as exc:
        raise ValueError(f"unknown precedence kind: {kind!r}") from exc

    def _document_id(answer: RetrievedChunk) -> str:
        return answer.source.document_id

    by_tier: dict[str, list[RetrievedChunk]] = {}
    for answer in answers:
        # A chunk whose role is unknown is treated as source material:
        # unclassified text cannot claim authority it was never granted.
        role = roles.get(_document_id(answer), "")
        tier = ROLE_TIERS.get(role, "source material")
        by_tier.setdefault(tier, []).append(answer)

    ordered = [tier for tier in policy if tier in by_tier]
    if not ordered:
        return None
    winning_tier = ordered[0]
    # Score decides the winner inside one tier and nothing else.
    winner = max(by_tier[winning_tier], key=lambda a: a.score)

    conflicts = tuple(
        AdvisoryConflict(
            winning_tier=winning_tier,
            winner=winner,
            conflicting_tier=tier,
            conflicting=answer,
        )
        for tier in ordered[1:]
        for answer in by_tier[tier]
        if _normalized_answer(answer) != _normalized_answer(winner)
    )
    return PrecedenceResult(answer=winner, tier=winning_tier, conflicts=conflicts)
