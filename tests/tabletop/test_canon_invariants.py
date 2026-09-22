from dataclasses import FrozenInstanceError

import pytest

from tabletop.api.errors import FactInvariantError
from tabletop.campaign.invariants import check_fact_invariants, promote, reveal
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState


def make_fact(
    *,
    canon_state: CanonState,
    knowledge_state: KnowledgeState,
) -> Fact:
    return Fact(
        fact_id="fact-1",
        fact_scope=FactScope.CAMPAIGN,
        setting_id=None,
        campaign_id="campaign-1",
        subject_id="entity-1",
        predicate="is_allied_with",
        value="entity-2",
        canon_state=canon_state,
        knowledge_state=knowledge_state,
        created_at="2026-09-22T00:00:00Z",
    )


@pytest.mark.parametrize(
    ("canon_state", "knowledge_state"),
    [
        (CanonState.CONFIRMED, KnowledgeState.UNREVEALED),
        (CanonState.CONFIRMED, KnowledgeState.KNOWN),
        (CanonState.PROPOSED, KnowledgeState.UNREVEALED),
    ],
)
def test_check_fact_invariants_accepts_valid_pairs(
    canon_state: CanonState,
    knowledge_state: KnowledgeState,
) -> None:
    fact = make_fact(
        canon_state=canon_state,
        knowledge_state=knowledge_state,
    )

    assert check_fact_invariants(fact) is None


def test_check_fact_invariants_rejects_proposed_known_and_names_both_axes() -> None:
    fact = make_fact(
        canon_state=CanonState.PROPOSED,
        knowledge_state=KnowledgeState.KNOWN,
    )

    with pytest.raises(FactInvariantError) as exc_info:
        check_fact_invariants(fact)

    message = str(exc_info.value)
    assert "canon_state" in message
    assert "knowledge_state" in message
    assert CanonState.PROPOSED.value in message
    assert KnowledgeState.KNOWN.value in message


def test_promote_changes_only_canon_state_and_returns_frozen_fact() -> None:
    fact = make_fact(
        canon_state=CanonState.PROPOSED,
        knowledge_state=KnowledgeState.UNREVEALED,
    )

    promoted = promote(fact)

    assert promoted is not fact
    assert promoted.canon_state is CanonState.CONFIRMED
    assert promoted.knowledge_state is fact.knowledge_state
    with pytest.raises(FrozenInstanceError):
        promoted.value = "changed"


def test_reveal_changes_only_knowledge_state() -> None:
    fact = make_fact(
        canon_state=CanonState.CONFIRMED,
        knowledge_state=KnowledgeState.UNREVEALED,
    )

    revealed = reveal(fact)

    assert revealed is not fact
    assert revealed.canon_state is fact.canon_state
    assert revealed.knowledge_state is KnowledgeState.KNOWN


def test_reveal_rejects_proposed_fact() -> None:
    fact = make_fact(
        canon_state=CanonState.PROPOSED,
        knowledge_state=KnowledgeState.UNREVEALED,
    )

    with pytest.raises(FactInvariantError):
        reveal(fact)
