"""``submit_action`` is the canonical path from a proposal to mechanics.

Every case drives ``TabletopRuntime.submit_action``. The plugin is the only
thing that may produce a roll, an outcome, or a state change, and the
proposal must never reach it before the planner has decided.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tabletop.api.workspace import Workspace
from tabletop.campaign.event_store import EventStore
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate

REPO_ROOT = Path(__file__).resolve().parents[2]
_SYSTEMS = REPO_ROOT / "systems"
_CAMPAIGN = "proposal-demo"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "proposal.db")
    migrate(connection)
    store = CampaignStore(connection)
    store.create_campaign(_CAMPAIGN, "Proposal Demo", "freeform")
    for entity_id in ("pc-ada", "npc-gate"):
        store.upsert_entity(
            _CAMPAIGN,
            entity_id,
            entity_id,
            system_state={"resources": {"lanterns": 5}},
        )
    SceneStore(connection).open_scene(_CAMPAIGN, "scene-1", "Crossroads")
    yield connection
    connection.close()


@pytest.fixture
def runtime(tmp_path: Path, conn: sqlite3.Connection) -> TabletopRuntime:
    return TabletopRuntime(
        tmp_path,
        campaign_roots=[tmp_path / "campaigns"],
        plugin_roots=[_SYSTEMS],
        connection=conn,
        workspace=Workspace.CAMPAIGN,
        active_campaign=_CAMPAIGN,
    )


def _proposal(**overrides: object) -> str:
    payload: dict[str, object] = {
        "actor_id": "pc-ada",
        "intent": "Ada waits and watches the gate",
        "proposed_action_type": None,
        "target_refs": [],
        "parameters": {},
        "uncertainty": None,
        "needs_resolution": True,
    }
    payload.update(overrides)
    return json.dumps(payload)


def _resolved_events(conn: sqlite3.Connection) -> list[str]:
    return [
        event.event_type
        for event in EventStore(conn).read(_CAMPAIGN)
        if event.event_type == "action.resolved"
    ]


# -- narrative --------------------------------------------------------------


def test_a_narrative_proposal_resolves_to_no_mechanics(
    runtime: TabletopRuntime, conn: sqlite3.Connection
) -> None:
    result = runtime.submit_action(_proposal())
    assert result["ok"], result
    assert result["data"]["disposition"] == "narrative"
    assert result["data"]["action"] is None
    assert _resolved_events(conn) == []


# -- player clarification ---------------------------------------------------


def test_an_ambiguous_target_asks_the_player_and_resolves_nothing(
    runtime: TabletopRuntime, conn: sqlite3.Connection
) -> None:
    CampaignStore(conn).upsert_entity(_CAMPAIGN, "npc-hound", "npc-hound")
    SceneStore(conn).enter(_CAMPAIGN, "scene-1", "npc-gate", _pc())
    SceneStore(conn).enter(_CAMPAIGN, "scene-1", "npc-hound", _npc())

    result = runtime.submit_action(
        _proposal(proposed_action_type="check", target_refs=[])
    )
    assert result["ok"], result
    assert result["data"]["disposition"] == "player_clarification"
    assert result["data"]["clarification"]["question"]
    assert _resolved_events(conn) == []


def _pc():
    from tabletop.campaign.models import PresenceType

    return PresenceType.NPC


def _npc():
    from tabletop.campaign.models import PresenceType

    return PresenceType.NPC


# -- rule-filled mechanical action ------------------------------------------


def test_a_rule_filled_check_reaches_the_plugin(
    runtime: TabletopRuntime, conn: sqlite3.Connection
) -> None:
    """A difficulty from campaign state is enough for freeform's `check`."""

    CampaignStore(conn).apply_state_changes(
        _CAMPAIGN,
        _scene_set(("scene", "system", "difficulty"), 12),
        scene_id="scene-1",
    )
    SceneStore(conn).enter(_CAMPAIGN, "scene-1", "npc-gate", _npc())
    result = runtime.submit_action(
        _proposal(
            proposed_action_type="check",
            target_refs=["npc-gate"],
            parameters={"expression": "1d2"},
        )
    )
    assert result["ok"], result
    assert result["data"]["disposition"] == "resolve"
    assert result["data"]["resolution"]["status"] == "resolved"
    assert _resolved_events(conn) == ["action.resolved"]


def _scene_set(path: tuple[str, ...], value: object):
    from tabletop.api.resolution import StateChange, StateOperation

    return (StateChange(operation=StateOperation.SET, path=path, value=value),)


# -- model-only parameter is refused ----------------------------------------


def test_a_model_only_difficulty_never_reaches_the_plugin(
    runtime: TabletopRuntime, conn: sqlite3.Connection
) -> None:
    result = runtime.submit_action(
        _proposal(
            proposed_action_type="check",
            target_refs=["npc-gate"],
            # The model supplies the number itself.
            parameters={"expression": "1d2", "difficulty": 15},
        )
    )
    assert result["ok"], result
    assert result["data"]["disposition"] != "resolve"
    assert _resolved_events(conn) == []


# -- unsupported ------------------------------------------------------------


def test_an_unsupported_mechanic_is_reported_not_rolled(
    runtime: TabletopRuntime, conn: sqlite3.Connection
) -> None:
    result = runtime.submit_action(
        _proposal(proposed_action_type="phase_moon", target_refs=[])
    )
    assert result["ok"], result
    assert result["data"]["disposition"] == "unsupported"
    assert _resolved_events(conn) == []


# -- GM ruling --------------------------------------------------------------


def test_an_unjournable_mechanic_reaches_the_gm(
    runtime: TabletopRuntime, conn: sqlite3.Connection
) -> None:
    # The target is present, so this gets past clarification and stops only
    # because no source can supply the difficulty.
    SceneStore(conn).enter(_CAMPAIGN, "scene-1", "npc-gate", _npc())
    result = runtime.submit_action(
        _proposal(
            proposed_action_type="check",
            target_refs=["npc-gate"],
            parameters={"expression": "1d2"},
        )
    )
    assert result["ok"], result
    assert result["data"]["disposition"] == "gm_ruling"
    assert result["data"]["missing_parameters"] == ["difficulty"]
    assert _resolved_events(conn) == []


# -- player control ---------------------------------------------------------


def test_a_player_cannot_act_as_a_character_they_do_not_control(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    player = TabletopRuntime(
        tmp_path,
        campaign_roots=[tmp_path / "campaigns"],
        plugin_roots=[_SYSTEMS],
        connection=conn,
        workspace=Workspace.PLAYER,
        active_campaign=_CAMPAIGN,
        participant_id="player-1",
    )
    result = player.submit_action(
        _proposal(
            actor_id="npc-gate",
            proposed_action_type="check",
            target_refs=[],
        )
    )
    assert result["ok"], result
    assert result["data"]["disposition"] in {
        "player_clarification",
        "unsupported",
        "narrative",
    }
    assert _resolved_events(conn) == []


# -- the plugin guard is unchanged ------------------------------------------


def test_only_the_plugin_produces_state_changes(
    runtime: TabletopRuntime, conn: sqlite3.Connection
) -> None:
    """A narrative proposal must leave plugin state exactly as it was."""

    before = conn.execute(
        "SELECT system_state FROM entities WHERE entity_id = 'pc-ada'"
    ).fetchone()["system_state"]
    runtime.submit_action(_proposal())
    after = conn.execute(
        "SELECT system_state FROM entities WHERE entity_id = 'pc-ada'"
    ).fetchone()["system_state"]
    assert before == after


def test_submit_action_requires_storage(tmp_path: Path) -> None:
    bare = TabletopRuntime(
        tmp_path,
        campaign_roots=[],
        plugin_roots=[],
        connection=None,
        workspace=Workspace.CAMPAIGN,
        active_campaign=_CAMPAIGN,
    )
    result = bare.submit_action(_proposal())
    assert result["ok"] is False
    assert result["error"]["code"] == "storage_not_configured"


def test_submit_action_rejects_a_malformed_proposal(
    runtime: TabletopRuntime,
) -> None:
    result = runtime.submit_action(json.dumps({"intent": "no actor here"}))
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_proposal"
