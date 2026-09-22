"""Explicit promote and reveal skills ignore lifecycle fields in the payload."""

from __future__ import annotations

import json
from pathlib import Path

from tabletop.api.workspace import Workspace
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState
from tabletop.campaign.store import CampaignStore
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate


def _runtime(tmp_path: Path, connection, *, workspace: Workspace, campaign: str | None):
    return TabletopRuntime(
        tmp_path,
        campaign_roots=[],
        plugin_roots=[],
        connection=connection,
        workspace=workspace,
        active_campaign=campaign,
    )


def test_campaign_and_setting_fact_skills_are_split(tmp_path: Path) -> None:
    connection = connect(tmp_path / "facts.db")
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
    CampaignStore(connection).add_fact(
        Fact(
            fact_id="campaign-fact",
            fact_scope=FactScope.CAMPAIGN,
            setting_id=None,
            campaign_id="campaign-1",
            subject_id="hero",
            predicate="has-title",
            value="Warden",
            canon_state=CanonState.PROPOSED,
            knowledge_state=KnowledgeState.UNREVEALED,
            created_at="2026-09-22T00:00:00Z",
        )
    )
    CampaignStore(connection).add_fact(
        Fact(
            fact_id="world-fact",
            fact_scope=FactScope.SETTING,
            setting_id="setting-1",
            campaign_id=None,
            subject_id="harbor",
            predicate="founded",
            value="by keepers",
            canon_state=CanonState.PROPOSED,
            knowledge_state=KnowledgeState.UNREVEALED,
            created_at="2026-09-22T00:00:00Z",
        )
    )
    campaign = _runtime(
        tmp_path, connection, workspace=Workspace.CAMPAIGN, campaign="campaign-1"
    )
    setting = _runtime(
        tmp_path, connection, workspace=Workspace.SETTING, campaign="campaign-1"
    )
    promoted = campaign.promote_fact(
        json.dumps({"fact_id": "campaign-fact", "canon_state": "proposed"})
    )
    assert promoted["data"]["canon_state"] == "confirmed"
    assert promoted["data"]["knowledge_state"] == "unrevealed"
    revealed = campaign.reveal_fact_skill("campaign-fact")
    assert revealed["data"]["knowledge_state"] == "known"
    assert revealed["data"]["canon_state"] == "confirmed"
    world = setting.promote_world_fact(
        json.dumps({"fact_id": "world-fact", "knowledge_state": "known"})
    )
    assert world["data"]["canon_state"] == "confirmed"
    assert world["data"]["knowledge_state"] == "unrevealed"
    shown = setting.reveal_world_fact("world-fact")
    assert shown["data"]["knowledge_state"] == "known"
    foreign = _runtime(
        tmp_path, connection, workspace=Workspace.CAMPAIGN, campaign="campaign-2"
    )
    CampaignStore(connection).create_campaign("campaign-2", "Other", "freeform")
    assert foreign.promote_fact("campaign-fact")["ok"] is False
    names = {skill.name for skill in Workspace.CAMPAIGN.skills}
    setting_names = {skill.name for skill in Workspace.SETTING.skills}
    assert "promote-world-fact" not in names
    assert "update-fact" not in names
    assert "detach-provenance" not in names
    assert "promote-fact" not in setting_names
    assert "promote-ruling" not in setting_names
    history = campaign.query_world_history("")
    assert {row["fact_id"] for row in history["data"]["facts"]} == {"world-fact"}
    connection.close()
