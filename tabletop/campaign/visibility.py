"""Visibility filtering for campaign facts.

Filters facts per active viewpoint (viewer role and identity) using the
scopes defined in ``tabletop.api.visibility``. Tests for this module land
early (Phase 14 of the execution plan).
"""

from __future__ import annotations

from tabletop.api.visibility import Viewpoint, VisibilityKind
from tabletop.campaign.models import CanonState, KnowledgeState


def visible_facts_clause(viewpoint: Viewpoint) -> tuple[str, tuple[str, ...]]:
    """Return the SQL predicate and parameters for facts visible to a viewpoint."""

    if viewpoint.scope.kind is VisibilityKind.GM:
        return "1 = 1", ()

    visible_scopes = {"PUBLIC"}
    if viewpoint.party_member:
        visible_scopes.add("PARTY")
    if viewpoint.scope.kind in {VisibilityKind.CHARACTER, VisibilityKind.NPC}:
        visible_scopes.add(viewpoint.scope.to_string())

    if viewpoint.scope.kind is VisibilityKind.FACTION:
        visible_scopes.add(viewpoint.scope.to_string())
    visible_scopes.update(
        f"FACTION:{faction_id}" for faction_id in viewpoint.faction_ids
    )

    if viewpoint.scope.kind is VisibilityKind.GROUP:
        visible_scopes.add(viewpoint.scope.to_string())
    visible_scopes.update(f"GROUP:{group_id}" for group_id in viewpoint.group_ids)

    ordered_scopes = tuple(sorted(visible_scopes))
    placeholders = ", ".join("?" for _ in ordered_scopes)
    clause = (
        "canon_state = ? AND knowledge_state = ? "
        f"AND visibility IN ({placeholders})"
    )
    return clause, (
        CanonState.CONFIRMED.value,
        KnowledgeState.KNOWN.value,
        *ordered_scopes,
    )
