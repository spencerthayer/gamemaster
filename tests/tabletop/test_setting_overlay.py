"""Campaign reads overlay owned setting rows without concatenating them."""

from __future__ import annotations

from pathlib import Path

from tabletop.api.visibility import Viewpoint, gm_viewpoint, parse_scope
from tabletop.api.workspace import Workspace
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState
from tabletop.campaign.relationships import RelationshipEdge, store_edge
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.turn import build_resolution_context
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate


def test_query_campaign_prefers_campaign_facts(tmp_path: Path) -> None:
    connection = connect(tmp_path / "overlay.db")
    migrate(connection)
    connection.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("setting-1", "Owned", "2026-09-22T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("setting-2", "Other", "2026-09-22T00:00:01Z"),
    )
    CampaignStore(connection).create_campaign(
        "campaign-1", "Owned", "freeform", setting_id="setting-1"
    )
    store = CampaignStore(connection)
    store.add_fact(
        Fact(
            fact_id="setting-title",
            fact_scope=FactScope.SETTING,
            setting_id="setting-1",
            campaign_id=None,
            subject_id="harbor",
            predicate="title",
            value="Old Harbor",
            canon_state=CanonState.CONFIRMED,
            knowledge_state=KnowledgeState.KNOWN,
            visibility="PUBLIC",
            created_at="2026-09-22T00:00:00Z",
        )
    )
    store.add_fact(
        Fact(
            fact_id="campaign-title",
            fact_scope=FactScope.CAMPAIGN,
            setting_id=None,
            campaign_id="campaign-1",
            subject_id="harbor",
            predicate="title",
            value="New Harbor",
            canon_state=CanonState.CONFIRMED,
            knowledge_state=KnowledgeState.KNOWN,
            visibility="PUBLIC",
            created_at="2026-09-22T01:00:00Z",
        )
    )
    store.add_fact(
        Fact(
            fact_id="setting-only",
            fact_scope=FactScope.SETTING,
            setting_id="setting-1",
            campaign_id=None,
            subject_id="harbor",
            predicate="founded",
            value="by keepers",
            canon_state=CanonState.CONFIRMED,
            knowledge_state=KnowledgeState.KNOWN,
            visibility="PUBLIC",
            created_at="2026-09-22T00:00:00Z",
        )
    )
    store.add_fact(
        Fact(
            fact_id="other-setting",
            fact_scope=FactScope.SETTING,
            setting_id="setting-2",
            campaign_id=None,
            subject_id="harbor",
            predicate="secret",
            value="elsewhere",
            canon_state=CanonState.CONFIRMED,
            knowledge_state=KnowledgeState.KNOWN,
            visibility="PUBLIC",
            created_at="2026-09-22T00:00:00Z",
        )
    )
    connection.execute(
        "INSERT INTO entities "
        "(entity_id, owner_scope, setting_id, campaign_id, name, system_state, metadata) "
        "VALUES ('harbor', 'setting', 'setting-1', NULL, 'Harbor', '{\"tide\":1}', '{}')"
    )
    connection.execute(
        "INSERT INTO entities "
        "(entity_id, owner_scope, setting_id, campaign_id, overrides_id, name, "
        "system_state, metadata) "
        "VALUES ('harbor', 'campaign', NULL, 'campaign-1', 'harbor', 'Harbor', "
        "'{\"tide\":9}', '{}')"
    )
    store_edge(
        connection,
        RelationshipEdge(
            relationship_id="setting-edge",
            owner_scope="setting",
            setting_id="setting-1",
            campaign_id=None,
            source_id="harbor",
            relationship_type="near",
            target_id="gate",
            visibility="PUBLIC",
            valid_from=None,
            valid_until=None,
        ),
    )
    store_edge(
        connection,
        RelationshipEdge(
            relationship_id="campaign-edge",
            owner_scope="campaign",
            setting_id=None,
            campaign_id="campaign-1",
            source_id="harbor",
            relationship_type="near",
            target_id="gate",
            visibility="PUBLIC",
            valid_from=None,
            valid_until=None,
        ),
    )
    runtime = TabletopRuntime(
        tmp_path,
        campaign_roots=[],
        plugin_roots=[],
        connection=connection,
        workspace=Workspace.CAMPAIGN,
        active_campaign="campaign-1",
    )
    facts = runtime.query_campaign("")["data"]["facts"]
    titles = [fact for fact in facts if fact["predicate"] == "title"]
    assert [fact["value"] for fact in titles] == ["New Harbor"]
    assert any(fact["fact_id"] == "setting-only" for fact in facts)
    assert all(fact["fact_id"] != "other-setting" for fact in facts)
    context = build_resolution_context(
        connection, campaign_id="campaign-1", system_id="freeform"
    )
    assert context.state["entities"]["harbor"]["system"]["tide"] == 9
    edges = runtime.get_relationships("harbor")["data"]["relationships"]
    assert [edge["relationship_id"] for edge in edges] == ["campaign-edge"]
    assert gm_viewpoint() == Viewpoint(scope=parse_scope("GM"))
    connection.close()
