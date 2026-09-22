"""Invariant checks and state transitions for campaign facts."""

from __future__ import annotations

from dataclasses import replace

from tabletop.api.errors import FactInvariantError
from tabletop.campaign.models import CanonState, Fact, KnowledgeState


def check_canon_knowledge_invariants(
    canon_state: CanonState,
    knowledge_state: KnowledgeState,
    *,
    record_type: str = "fact",
) -> None:
    """Reject canon and player-knowledge combinations with no valid meaning."""
    if (
        canon_state is CanonState.PROPOSED
        and knowledge_state is KnowledgeState.KNOWN
    ):
        raise FactInvariantError(
            f"invalid {record_type} state: "
            f"canon_state={canon_state.value!r} cannot be combined with "
            f"knowledge_state={knowledge_state.value!r}"
        )


def check_fact_invariants(fact: Fact) -> None:
    """Reject the one canon and knowledge combination with no valid meaning."""
    check_canon_knowledge_invariants(fact.canon_state, fact.knowledge_state)


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


def detach(fact: Fact) -> Fact:
    """Return the fact with purge ownership transferred away from its source."""
    return replace(fact, source_ownership="detached")
