"""Invariant checks and state transitions for campaign facts."""

from __future__ import annotations

from dataclasses import replace

from tabletop.api.errors import FactInvariantError
from tabletop.campaign.models import CanonState, Fact, KnowledgeState


def check_fact_invariants(fact: Fact) -> None:
    """Reject the one canon and knowledge combination with no valid meaning."""
    if (
        fact.canon_state is CanonState.PROPOSED
        and fact.knowledge_state is KnowledgeState.KNOWN
    ):
        raise FactInvariantError(
            "invalid fact state: "
            f"canon_state={fact.canon_state.value!r} cannot be combined with "
            f"knowledge_state={fact.knowledge_state.value!r}"
        )


def promote(fact: Fact) -> Fact:
    """Return the fact as confirmed without changing its knowledge state."""
    promoted = replace(fact, canon_state=CanonState.CONFIRMED)
    check_fact_invariants(promoted)
    return promoted


def reveal(fact: Fact) -> Fact:
    """Return the fact as known, rejecting facts that are still proposed."""
    if fact.canon_state is CanonState.PROPOSED:
        raise FactInvariantError(
            "invalid fact state: "
            f"canon_state={fact.canon_state.value!r} cannot be combined with "
            f"knowledge_state={KnowledgeState.KNOWN.value!r}"
        )

    revealed = replace(fact, knowledge_state=KnowledgeState.KNOWN)
    check_fact_invariants(revealed)
    return revealed
