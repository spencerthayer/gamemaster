"""Canon and campaign boundary coverage for runtime skill surfaces."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tabletop.api.workspace import Workspace
from tabletop.campaign.event_store import EventStore, EventType
from tabletop.campaign.models import CanonState, FactScope, KnowledgeState
from tabletop.campaign.relationships import RelationshipEdge, store_edge
from tabletop.campaign.store import CampaignStore
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADAPTER_PATH = _REPO_ROOT / "plugins" / "tabletop" / "omega_tabletop_adapter.py"


def _load_adapter():
    spec = importlib.util.spec_from_file_location(
        "omega_tabletop_adapter_boundaries_test", _ADAPTER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "boundaries.db")
    migrate(connection)
    connection.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("setting-1", "Owned Setting", "2026-09-22T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("setting-2", "Other Setting", "2026-09-22T00:00:01Z"),
    )
    CampaignStore(connection).create_campaign(
        "campaign-1",
        "Owned",
        "freeform",
        setting_id="setting-1",
        system_state={},
    )
    CampaignStore(connection).create_campaign(
        "campaign-2",
        "Other",
        "freeform",
        setting_id="setting-2",
        system_state={},
    )
    try:
        yield connection
    finally:
        connection.close()


def _runtime(
    tmp_path: Path,
    conn,
    *,
    workspace: Workspace = Workspace.CAMPAIGN,
    active_campaign: str | None = "campaign-1",
) -> TabletopRuntime:
    return TabletopRuntime(
        tmp_path,
        campaign_roots=[],
        plugin_roots=[],
        connection=conn,
        workspace=workspace,
        active_campaign=active_campaign,
    )


def test_record_world_history_defaults_to_proposed_unrevealed_and_emits_event(
    tmp_path: Path,
    conn,
) -> None:
    runtime = _runtime(tmp_path, conn, workspace=Workspace.SETTING)
    payload = runtime.record_world_history(
        json.dumps(
            {
                "fact_id": "history-1",
                "setting_id": "setting-1",
                "subject_id": "city",
                "predicate": "ruler",
                "value": "Queen Mara",
                "canon_state": "confirmed",
                "knowledge_state": "known",
                "visibility": "PUBLIC",
            }
        )
    )

    assert payload["ok"] is True
    assert payload["data"]["canon_state"] == CanonState.PROPOSED.value
    assert payload["data"]["knowledge_state"] == KnowledgeState.UNREVEALED.value
    row = conn.execute(
        "SELECT canon_state, knowledge_state, visibility, fact_scope "
        "FROM facts WHERE fact_id = ?",
        ("history-1",),
    ).fetchone()
    assert dict(row) == {
        "canon_state": "proposed",
        "knowledge_state": "unrevealed",
        "visibility": "PUBLIC",
        "fact_scope": FactScope.SETTING.value,
    }
    events = EventStore(conn).read("campaign-1")
    assert [event.event_type for event in events] == [EventType.FACT_PROPOSED.value]
    assert events[0].payload["fact_id"] == "history-1"


def test_adapter_record_ruling_accepts_json_string(
    tmp_path: Path,
    conn,
) -> None:
    runtime = _runtime(tmp_path, conn)
    adapter = _load_adapter()
    adapter.reset_runtime_for_tests()
    adapter.reset_skill_registration_for_tests()
    adapter._RUNTIME = runtime
    adapter._SKILLS_REGISTERED = True

    encoded = adapter.record_ruling(
        json.dumps(
            {
                "ruling_id": "ruling-json-1",
                "campaign_id": "campaign-1",
                "system_id": "freeform",
                "question": "Does grappling consume the full action?",
                "decision": "Yes.",
                "scope": "grappling",
                "source_references": [{"source_id": "core-rules"}],
                "session_id": None,
                "created_at": "2026-09-22T01:00:00+00:00",
            }
        )
    )
    payload = json.loads(encoded)

    assert payload["ok"] is True
    assert payload["operation"] == "record-ruling"
    assert payload["data"]["ruling_id"] == "ruling-json-1"
    assert payload.get("error") is None


def test_reads_are_scoped_to_active_campaign(tmp_path: Path, conn) -> None:
    runtime = _runtime(tmp_path, conn)
    conn.execute(
        "INSERT INTO sessions (session_id, campaign_id, started_at) VALUES (?, ?, ?)",
        ("session-other", "campaign-2", "2026-09-22T01:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO facts "
        "(fact_id, fact_scope, setting_id, campaign_id, subject_id, predicate, value, "
        "canon_state, knowledge_state, visibility, created_at) "
        "VALUES (?, 'campaign', NULL, ?, ?, ?, ?, 'confirmed', 'unrevealed', 'GM', ?)",
        (
            "secret-other",
            "campaign-2",
            "npc-1",
            "secret",
            "hidden",
            "2026-09-22T01:00:00+00:00",
        ),
    )
    conn.execute(
        "INSERT INTO entities "
        "(entity_id, owner_scope, setting_id, campaign_id, entity_type, name, "
        "system_state, metadata) "
        "VALUES (?, 'setting', ?, NULL, 'place', ?, '{}', '{}')",
        ("entity-other", "setting-2", "Foreign Landmark"),
    )
    conn.commit()

    assert runtime.read_session("session-other")["ok"] is False
    assert runtime.read_campaign_secret("secret-other")["ok"] is False
    assert runtime.get_world_entity("entity-other")["ok"] is False


def test_query_and_entity_skills_use_store_not_unavailable(
    tmp_path: Path,
    conn,
) -> None:
    runtime = _runtime(tmp_path, conn)
    CampaignStore(conn).upsert_entity(
        "campaign-1",
        "hero",
        "Hero",
        entity_type="pc",
        system_state={"hp": 10},
    )
    store_edge(
        conn,
        RelationshipEdge(
            relationship_id="rel-1",
            owner_scope="campaign",
            setting_id=None,
            campaign_id="campaign-1",
            source_id="hero",
            relationship_type="allied_with",
            target_id="guide",
            visibility="PUBLIC",
            valid_from="2026-01-01",
        ),
    )

    entity = runtime.get_entity("hero")
    relationships = runtime.get_relationships("hero")
    campaign_query = runtime.query_campaign("Hero")
    rules_query = runtime.query_rules("attack")

    assert entity["ok"] is True
    assert entity["data"]["entity"]["entity_id"] == "hero"
    assert relationships["ok"] is True
    assert relationships["data"]["relationships"][0]["relationship_id"] == "rel-1"
    assert campaign_query["ok"] is True
    assert campaign_query["data"]["entities"][0]["entity_id"] == "hero"
    assert rules_query["ok"] is True
    assert all(
        result.get("error", {}).get("code") != "capability_unavailable"
        for result in (entity, relationships, campaign_query, rules_query)
    )


def test_mutate_quest_uses_campaign_store_state_changes(
    tmp_path: Path,
    conn,
) -> None:
    runtime = _runtime(tmp_path, conn)
    payload = runtime.mutate_quest(
        json.dumps({"quest_id": "q1", "title": "Find the gate", "status": "open"})
    )

    assert payload["ok"] is True
    campaign = CampaignStore(conn).get_campaign("campaign-1")
    assert campaign is not None
    assert campaign["system_state"]["quests"]["q1"]["title"] == "Find the gate"
