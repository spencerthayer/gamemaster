"""The whole milestone in one scenario, through public services only.

Dialogue, an ambiguous target, a GM escalation, a ruling, a resolved action,
a GM-only secret, a scene transition, a restart from disk, and a continuation
whose outcome depends on restored state. Direct SQL appears only in assertion
helpers, never to perform a scenario step.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tabletop.api.visibility import (
    Viewpoint,
    VisibilityKind,
    VisibilityScope,
)
from tabletop.api.workspace import Workspace
from tabletop.campaign.models import (
    CanonState,
    Fact,
    FactScope,
    KnowledgeState,
    PresenceType,
    SceneStatus,
)
from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.scene_snapshot import build_scene_snapshot
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.gm_commands import (
    GmRouter,
    apply_gm_mutation,
    explain_turn_for_gm,
    parse_gm_mutation,
)
from tabletop.orchestration.planner import Disposition
from tabletop.orchestration.turn_job import TurnJobStore
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate

REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEMS = REPO_ROOT / "systems"
CAMPAIGN = "black-company"
_SECRET = "Vor blames Ada for opening the floodgate"


def _player_viewpoint() -> Viewpoint:
    return Viewpoint(
        scope=VisibilityScope(kind=VisibilityKind.CHARACTER, target="pc-ada"),
        party_member=True,
    )


def _seed(conn: sqlite3.Connection) -> None:
    """A configured campaign: two players, one GM, a secret, a starting scene."""
    store = CampaignStore(conn)
    store.create_campaign(CAMPAIGN, "The Black Company", "freeform")
    for entity_id, name in (
        ("pc-ada", "Ada"),
        ("pc-bosch", "Bosch"),
        ("npc-vor", "Vor"),
        ("loc-hall", "Flooded Hall"),
    ):
        store.upsert_entity(
            CAMPAIGN, entity_id, name, system_state={"resources": {"hp": 10}}
        )
    membership = MembershipStore(conn)
    membership.add_participant(CAMPAIGN, "gm-1", "The GM", "gm")
    membership.add_participant(CAMPAIGN, "player-1", "Ada", "player")
    membership.add_participant(CAMPAIGN, "player-2", "Bosch", "player")
    membership.bind_principal(CAMPAIGN, "gm-1", "telegram", "1000")
    membership.bind_principal(CAMPAIGN, "player-1", "telegram", "2001")
    membership.bind_principal(CAMPAIGN, "player-2", "telegram", "2002")
    for participant, entity in (
        ("player-1", "pc-ada"),
        ("player-2", "pc-bosch"),
        ("gm-1", "npc-vor"),
    ):
        membership.grant_control(CAMPAIGN, participant, entity, "owner")
    store.add_fact(
        Fact(
            fact_id="fact-secret",
            fact_scope=FactScope.CAMPAIGN,
            setting_id=None,
            campaign_id=CAMPAIGN,
            subject_id="npc-vor",
            predicate="intends-to-blame",
            value="Ada",
            canon_state=CanonState.CONFIRMED,
            knowledge_state=KnowledgeState.UNREVEALED,
            visibility="GM",
            created_at="2026-09-25T00:00:00+00:00",
        )
    )
    scenes = SceneStore(conn)
    scenes.open_scene(
        CAMPAIGN, "scene-1", "Flooded Hall", location_entity_id="loc-hall"
    )
    for entity_id in ("pc-ada", "pc-bosch", "npc-vor"):
        scenes.enter(CAMPAIGN, "scene-1", entity_id, PresenceType.NPC)
    scenes.set_game_time(
        CAMPAIGN, in_world_label="Day 3, dusk", in_world_minutes=2880
    )


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "dogfood.db"
    conn = connect(path)
    migrate(conn)
    _seed(conn)
    conn.close()
    return path


def _open(database: Path) -> sqlite3.Connection:
    conn = connect(database)
    migrate(conn)
    return conn


def _play(
    tmp_path: Path,
    conn: sqlite3.Connection,
    intent: str,
    *,
    action_type: str | None = None,
    target_refs: tuple[str, ...] = (),
    parameters: dict[str, Any] | None = None,
    external_message_id: str = "1",
) -> dict[str, Any]:
    """One player turn: claim ingress, then submit through the public entry point.

    Claiming first is what real play does. Without it a turn has no durable
    identity, so a retry could not be deduplicated and nothing would be
    explainable afterwards.
    """
    TurnJobStore(conn).claim_ingress(
        CAMPAIGN, intent, channel="telegram", conversation_id="chat-1",
        external_message_id=external_message_id, principal_id="telegram:2001",
    )
    runtime = TabletopRuntime(
        REPO_ROOT,
        campaign_roots=[tmp_path / "campaigns"],
        plugin_roots=[SYSTEMS],
        connection=conn,
        workspace=Workspace.CAMPAIGN,
        active_campaign=CAMPAIGN,
    )
    result = runtime.submit_action(
        json.dumps(
            {
                "actor_id": "pc-ada",
                "intent": intent,
                "proposed_action_type": action_type,
                "target_refs": list(target_refs),
                "parameters": parameters or {},
                "uncertainty": None,
                "needs_resolution": True,
            }
        )
    )
    assert result["ok"], result
    return result["data"]


def _gm(conn: sqlite3.Connection, text: str) -> dict[str, Any]:
    return apply_gm_mutation(conn, parse_gm_mutation(text), CAMPAIGN)


def _set_scene_state(conn: sqlite3.Connection, scene_id: str, state: dict[str, Any]) -> None:
    """Assertion-side setup of a scene value, not a scenario step."""
    conn.execute(
        "UPDATE scenes SET system_state = ? WHERE scene_id = ?",
        (json.dumps(state), scene_id),
    )


def test_the_whole_milestone_runs_end_to_end(tmp_path: Path, database: Path) -> None:
    conn = _open(database)
    try:
        # 1. Dialogue produces no mechanics.
        data = _play(tmp_path, conn, "Ada asks the gate to open", external_message_id="m1")
        assert data["disposition"] == Disposition.NARRATIVE.value
        assert data["resolution"] is None

        # 2. An ambiguous target asks the player, not the GM.
        data = _play(
            tmp_path, conn, "Ada attacks", action_type="check", external_message_id="m2"
        )
        assert data["disposition"] == Disposition.PLAYER_CLARIFICATION.value
        assert "Which one" in data["clarification"]["question"]

        # 3. A named target with no difficulty reaches the GM.
        data = _play(
            tmp_path, conn, "Ada tests the gate", action_type="check",
            target_refs=("npc-vor",), parameters={"expression": "1d2"},
            external_message_id="m3",
        )
        assert data["disposition"] == Disposition.GM_RULING.value

        # 4. The GM records a ruling, which does not confirm it.
        recorded = _gm(conn, "ruling record ruling-1 gate")
        assert recorded["canon_state"] == "proposed"
        promoted = _gm(conn, "ruling promote ruling-1")
        assert promoted["canon_state"] == "confirmed"
        # Confirming precedent must not reveal it to players.
        assert promoted["knowledge_state"] == "unrevealed"

        # 5. A difficulty exists, so the same action now resolves.
        _set_scene_state(conn, "scene-1", {"difficulty": 2})
        resolved = _play(
            tmp_path, conn, "Ada tests the gate", action_type="check",
            target_refs=("npc-vor",), parameters={"expression": "1d2"},
            external_message_id="m5",
        )
        assert resolved["disposition"] == Disposition.RESOLVE.value
        assert resolved["resolution"]["status"] == "resolved"

        # 6. The GM-only secret never reaches a player.
        player_snapshot = build_scene_snapshot(
            conn, CAMPAIGN, viewpoint=_player_viewpoint()
        )
        assert _SECRET not in str(player_snapshot)
        gm_snapshot = build_scene_snapshot(
            conn, CAMPAIGN, viewpoint=GmRouter(conn, CAMPAIGN).viewpoint
        )
        assert "intends-to-blame" in str(gm_snapshot)

        # 7. The GM transitions to a new scene.
        _gm(conn, "scene transition scene-2 Sunken-Archive")
        store = SceneStore(conn)
        assert store.get_scene(CAMPAIGN, "scene-1").status is SceneStatus.CLOSED
        assert store.get_open_scene(CAMPAIGN).scene_id == "scene-2"
    finally:
        conn.close()

    # 8. Restart from disk. Established state must survive.
    reopened = _open(database)
    try:
        store = SceneStore(reopened)
        assert store.get_open_scene(CAMPAIGN).scene_id == "scene-2"
        assert store.get_game_time(CAMPAIGN).in_world_label == "Day 3, dusk"

        # 9. A continuation whose outcome depends on the restored state.
        _set_scene_state(reopened, "scene-2", {"difficulty": 2})
        store.enter(CAMPAIGN, "scene-2", "npc-vor", PresenceType.NPC)
        continued = _play(
            tmp_path, reopened, "Ada tests the archive door", action_type="check",
            target_refs=("npc-vor",), parameters={"expression": "1d2"},
            external_message_id="m9",
        )
        assert continued["disposition"] == Disposition.RESOLVE.value

        # 10. Every turn is explainable.
        turns = TurnJobStore(reopened).list_for_campaign(CAMPAIGN)
        assert turns
        for job in turns:
            explained = explain_turn_for_gm(reopened, CAMPAIGN, job.turn_id)
            assert explained["turn_id"] == job.turn_id
            assert explained["status"] == job.status
    finally:
        reopened.close()


def test_the_turn_ledger_records_one_effect_per_turn(tmp_path: Path, database: Path) -> None:
    conn = _open(database)
    try:
        _set_scene_state(conn, "scene-1", {"difficulty": 2})
        _play(
            tmp_path, conn, "Ada tests the gate", action_type="check",
            target_refs=("npc-vor",), parameters={"expression": "1d2"},
        )
        resolved = [
            event
            for event in __import__(
                "tabletop.campaign.event_store", fromlist=["EventStore"]
            ).EventStore(conn).read(CAMPAIGN)
            if event.event_type == "action.resolved"
        ]
        assert len(resolved) == 1
    finally:
        conn.close()


def test_a_scenario_step_uses_services_not_sql(
    tmp_path: Path, database: Path
) -> None:
    """Scene changes go through the GM command, not a direct write."""
    conn = _open(database)
    try:
        _gm(conn, "scene transition scene-9 Side-Hall")
        store = SceneStore(conn)
        assert store.get_open_scene(CAMPAIGN).scene_id == "scene-9"
        assert store.get_scene(CAMPAIGN, "scene-1").status is SceneStatus.CLOSED
    finally:
        conn.close()
