"""Combinations of visibility, canon, and knowledge must not leak.

Fact temporal validity is intentionally not filtered here. Relationship
queries already take an explicit ``as_of``. Fact reads do not, and game
time is not the wall clock, so this matrix does not treat an expired fact
as hidden.
"""

from __future__ import annotations

from pathlib import Path

from tabletop.api.visibility import Viewpoint, parse_scope
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState
from tabletop.campaign.relationships import RelationshipEdge, query_edges, store_edge
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate

_VISIBILITIES = (
    "PUBLIC",
    "PARTY",
    "GM",
    "CHARACTER:c1",
    "NPC:n1",
    "FACTION:f1",
    "GROUP:g1",
)
_VIEWERS = (
    Viewpoint(scope=parse_scope("GM")),
    Viewpoint(scope=parse_scope("PUBLIC"), party_member=True),
    Viewpoint(scope=parse_scope("CHARACTER:c1")),
    Viewpoint(scope=parse_scope("CHARACTER:c2")),
    Viewpoint(scope=parse_scope("NPC:n1")),
    Viewpoint(scope=parse_scope("FACTION:f1")),
    Viewpoint(scope=parse_scope("GROUP:g1")),
    Viewpoint(scope=parse_scope("PUBLIC")),
)


def _allows(viewer: Viewpoint, visibility: str, *, confirmed: bool, known: bool) -> bool:
    if viewer.scope.to_string() == "GM":
        return True
    if not confirmed or not known:
        return False
    if visibility == "PUBLIC":
        return True
    if visibility == "PARTY":
        return viewer.party_member
    if visibility == viewer.scope.to_string():
        return True
    if visibility.startswith("FACTION:") and visibility.split(":", 1)[1] in viewer.faction_ids:
        return True
    if visibility.startswith("GROUP:") and visibility.split(":", 1)[1] in viewer.group_ids:
        return True
    return False


def test_fact_matrix_does_not_disclose(tmp_path: Path) -> None:
    connection = connect(tmp_path / "matrix.db")
    migrate(connection)
    connection.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("setting-1", "Owned", "2026-09-22T00:00:00Z"),
    )
    CampaignStore(connection).create_campaign(
        "campaign-1", "Owned", "freeform", setting_id="setting-1"
    )
    store = CampaignStore(connection)
    index = 0
    for visibility in _VISIBILITIES:
        for canon in (CanonState.PROPOSED, CanonState.CONFIRMED):
            for knowledge in (KnowledgeState.UNREVEALED, KnowledgeState.KNOWN):
                if canon is CanonState.PROPOSED and knowledge is KnowledgeState.KNOWN:
                    continue
                for scope, setting_id, campaign_id in (
                    (FactScope.CAMPAIGN, None, "campaign-1"),
                    (FactScope.SETTING, "setting-1", None),
                ):
                    store.add_fact(
                        Fact(
                            fact_id=f"fact-{index}",
                            fact_scope=scope,
                            setting_id=setting_id,
                            campaign_id=campaign_id,
                            subject_id=f"subject-{index}",
                            predicate="status",
                            value=visibility,
                            canon_state=canon,
                            knowledge_state=knowledge,
                            visibility=visibility,
                            created_at=f"2026-09-22T00:00:{index % 60:02d}Z",
                        )
                    )
                    index += 1
    leaks: list[str] = []
    for viewer in _VIEWERS:
        visible = {
            fact.fact_id
            for fact in store.get_facts("campaign-1", viewpoint=viewer)
        }
        for fact in store.get_facts("campaign-1", viewpoint=gm_safe()):
            allowed = _allows(
                viewer,
                fact.visibility,
                confirmed=fact.canon_state is CanonState.CONFIRMED,
                known=fact.knowledge_state is KnowledgeState.KNOWN,
            )
            if fact.fact_id in visible and not allowed:
                leaks.append(f"{viewer.scope.to_string()} saw {fact.fact_id} {fact.visibility}")
    assert leaks == []
    connection.close()


def gm_safe() -> Viewpoint:
    return Viewpoint(scope=parse_scope("GM"))


def test_expired_relationship_stays_hidden(tmp_path: Path) -> None:
    connection = connect(tmp_path / "edges.db")
    migrate(connection)
    CampaignStore(connection).create_campaign("campaign-1", "Owned", "freeform")
    store_edge(
        connection,
        RelationshipEdge(
            relationship_id="expired",
            owner_scope="campaign",
            setting_id=None,
            campaign_id="campaign-1",
            source_id="hero",
            relationship_type="near",
            target_id="gate",
            visibility="PUBLIC",
            valid_from=None,
            valid_until="2020-01-01",
        ),
    )
    edges = query_edges(
        connection,
        owner_scope="campaign",
        entity_id="hero",
        as_of="2026-09-22",
        viewpoint=gm_safe(),
        campaign_id="campaign-1",
    )
    assert edges == ()
    connection.close()
