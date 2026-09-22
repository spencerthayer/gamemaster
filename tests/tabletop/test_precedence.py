"""Retrieval precedence and query-time campaign overlay tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tabletop.retrieval.models import (
    RetrievalNamespace,
    RetrievedChunk,
    SourceReference,
)
from tabletop.retrieval.precedence import (
    LORE_PRECEDENCE,
    MECHANICS_PRECEDENCE,
    PrecedenceResolver,
)


@dataclass(frozen=True)
class TierAnswer:
    campaign_id: str | None
    tier: str
    text: str
    namespace: RetrievalNamespace


class RecordingTierSearch:
    def __init__(self, answers: tuple[TierAnswer, ...]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str, str]] = []

    def search_tier(
        self, query: str, campaign_id: str, tier: str
    ) -> tuple[RetrievedChunk, ...]:
        self.calls.append((query, campaign_id, tier))
        return tuple(
            _chunk(answer)
            for answer in self.answers
            if answer.tier == tier
            and answer.campaign_id in (None, campaign_id)
            and query.lower() in answer.text.lower()
        )


def _chunk(answer: TierAnswer) -> RetrievedChunk:
    slug = answer.tier.replace(" ", "-")
    return RetrievedChunk(
        text=answer.text,
        score=1.0,
        namespace=answer.namespace,
        source=SourceReference(
            chunk_id=f"{answer.campaign_id or 'shared'}-{slug}",
            document_id=f"doc-{slug}",
            document_title=answer.tier,
            section="Answer",
            page=None,
            source_path=f"/{slug}.md",
        ),
    )


def test_precedence_policies_are_separate_and_verbatim() -> None:
    assert MECHANICS_PRECEDENCE == (
        "campaign rulings",
        "campaign house rules",
        "adventure-specific mechanics",
        "enabled supplements",
        "active system rules",
        "GM adjudication",
    )
    assert LORE_PRECEDENCE == (
        "campaign-specific canon",
        "shared setting canon",
        "adventure canon",
        "source material",
        "GM adjudication",
    )


def test_campaign_ruling_outranks_sourcebook_and_reports_conflict() -> None:
    search = RecordingTierSearch(
        (
            TierAnswer(
                campaign_id="campaign-a",
                tier="campaign rulings",
                text="Grappling costs a full action.",
                namespace=RetrievalNamespace.RULINGS,
            ),
            TierAnswer(
                campaign_id=None,
                tier="active system rules",
                text="Grappling costs an attack.",
                namespace=RetrievalNamespace.SYSTEM,
            ),
        )
    )

    result = PrecedenceResolver(search).resolve(
        "Grappling costs", "campaign-a", "mechanics"
    )

    assert result.answer is not None
    assert result.answer.text == "Grappling costs a full action."
    assert result.tier == "campaign rulings"
    assert [conflict.conflicting.text for conflict in result.conflicts] == [
        "Grappling costs an attack."
    ]


def test_campaign_lore_overlay_does_not_mutate_setting_or_other_campaign() -> None:
    setting_answer = TierAnswer(
        campaign_id=None,
        tier="shared setting canon",
        text="The city is ruled by Queen Mara.",
        namespace=RetrievalNamespace.SETTING,
    )
    search = RecordingTierSearch(
        (
            TierAnswer(
                campaign_id="campaign-a",
                tier="campaign-specific canon",
                text="The city is ruled by Regent Sol.",
                namespace=RetrievalNamespace.CAMPAIGN,
            ),
            setting_answer,
        )
    )
    resolver = PrecedenceResolver(search)

    campaign_a = resolver.resolve("city is ruled", "campaign-a", "lore")
    campaign_b = resolver.resolve("city is ruled", "campaign-b", "lore")

    assert campaign_a.answer is not None
    assert campaign_a.answer.text == "The city is ruled by Regent Sol."
    assert [conflict.conflicting.text for conflict in campaign_a.conflicts] == [
        setting_answer.text
    ]
    assert setting_answer.text == "The city is ruled by Queen Mara."
    assert campaign_b.answer is not None
    assert campaign_b.answer.text == setting_answer.text
    assert campaign_b.conflicts == ()


def test_mechanics_and_lore_search_only_their_own_policy_tiers() -> None:
    search = RecordingTierSearch(())
    resolver = PrecedenceResolver(search)

    resolver.resolve("difficulty", "campaign-a", "mechanics")
    mechanics_tiers = tuple(call[2] for call in search.calls)
    search.calls.clear()
    resolver.resolve("who rules", "campaign-a", "lore")
    lore_tiers = tuple(call[2] for call in search.calls)

    assert mechanics_tiers == MECHANICS_PRECEDENCE
    assert lore_tiers == LORE_PRECEDENCE
    assert "active system rules" not in lore_tiers
    assert "shared setting canon" not in mechanics_tiers


def test_unknown_kind_raises_instead_of_defaulting() -> None:
    resolver = PrecedenceResolver(RecordingTierSearch(()))

    with pytest.raises(ValueError, match="unknown precedence kind"):
        resolver.resolve("question", "campaign-a", "rules")
