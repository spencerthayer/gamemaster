"""Tests for SQL-level fact filtering by active viewpoint."""

from __future__ import annotations

from pathlib import Path

import pytest

from tabletop.api.visibility import Viewpoint, parse_scope
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate


@pytest.fixture
def store(tmp_path: Path) -> CampaignStore:
    conn = connect(tmp_path / "visibility.db")
    migrate(conn)
    result = CampaignStore(conn)
    result.create_campaign("campaign-1", "First", "test")
    try:
        yield result
    finally:
        conn.close()


def _viewpoint(scope: str, *, party_member: bool = False) -> Viewpoint:
    return Viewpoint(scope=parse_scope(scope), party_member=party_member)


def _fact(
    fact_id: str,
    *,
    visibility: str,
    canon_state: CanonState = CanonState.CONFIRMED,
    knowledge_state: KnowledgeState = KnowledgeState.KNOWN,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        fact_scope=FactScope.CAMPAIGN,
        setting_id=None,
        campaign_id="campaign-1",
        subject_id="subject-1",
        predicate="knows",
        value=fact_id,
        canon_state=canon_state,
        knowledge_state=knowledge_state,
        visibility=visibility,
        created_at=f"2026-09-22T00:00:0{len(fact_id)}Z",
    )


def test_filters_facts_in_sql_for_character_and_gm_viewpoints(
    store: CampaignStore,
) -> None:
    facts = (
        _fact("public", visibility="PUBLIC"),
        _fact("character", visibility="CHARACTER:hero"),
        _fact("gm-only", visibility="GM"),
        _fact(
            "unrevealed",
            visibility="CHARACTER:hero",
            knowledge_state=KnowledgeState.UNREVEALED,
        ),
        _fact(
            "proposed",
            visibility="PARTY",
            canon_state=CanonState.PROPOSED,
            knowledge_state=KnowledgeState.UNREVEALED,
        ),
    )
    for fact in facts:
        store.add_fact(fact)

    statements: list[str] = []
    store.conn.set_trace_callback(statements.append)
    player_facts = store.get_facts(
        "campaign-1",
        viewpoint=_viewpoint("CHARACTER:hero", party_member=True),
    )
    store.conn.set_trace_callback(None)

    assert len(player_facts) == 2
    assert {fact.fact_id for fact in player_facts} == {"public", "character"}
    assert any(
        "canon_state = 'confirmed'" in statement
        and "knowledge_state = 'known'" in statement
        and "visibility IN" in statement
        for statement in statements
    )

    gm_facts = store.get_facts("campaign-1", viewpoint=_viewpoint("GM"))
    assert len(gm_facts) == len(facts)
    assert {fact.fact_id for fact in gm_facts} == {
        "public",
        "character",
        "gm-only",
        "unrevealed",
        "proposed",
    }


def test_get_facts_requires_explicit_viewpoint(store: CampaignStore) -> None:
    with pytest.raises(TypeError, match="viewpoint"):
        store.get_facts("campaign-1")


@pytest.mark.parametrize(
    ("viewpoint", "expected"),
    [
        (
            Viewpoint(
                scope=parse_scope("CHARACTER:hero"),
                party_member=True,
                faction_ids=frozenset({"red"}),
                group_ids=frozenset({"crew"}),
            ),
            {"public", "party", "character", "faction", "group"},
        ),
        (_viewpoint("NPC:guide"), {"public", "npc"}),
        (_viewpoint("FACTION:red"), {"public", "faction"}),
        (_viewpoint("GROUP:crew"), {"public", "group"}),
    ],
)
def test_non_gm_sql_scopes_match_viewpoint_access(
    store: CampaignStore,
    viewpoint: Viewpoint,
    expected: set[str],
) -> None:
    scoped_facts = {
        "public": "PUBLIC",
        "party": "PARTY",
        "gm": "GM",
        "character": "CHARACTER:hero",
        "npc": "NPC:guide",
        "faction": "FACTION:red",
        "group": "GROUP:crew",
    }
    for fact_id, visibility in scoped_facts.items():
        store.add_fact(_fact(fact_id, visibility=visibility))

    facts = store.get_facts("campaign-1", viewpoint=viewpoint)

    assert {fact.fact_id for fact in facts} == expected
