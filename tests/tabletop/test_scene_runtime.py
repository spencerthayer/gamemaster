"""Authoritative scene runtime operations on ``TabletopRuntime``."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tabletop.api.workspace import Workspace
from tabletop.campaign.event_store import EventStore
from tabletop.campaign.models import SceneInvariantError
from tabletop.campaign.store import CampaignStore
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate


@pytest.fixture
def connection(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "scenes.db")
    migrate(conn)
    CampaignStore(conn).create_campaign("campaign-1", "First", "freeform")
    yield conn
    conn.close()


@pytest.fixture
def runtime(tmp_path: Path, connection: sqlite3.Connection) -> TabletopRuntime:
    return TabletopRuntime(
        tmp_path,
        campaign_roots=[],
        plugin_roots=[Path(__file__).resolve().parents[2] / "systems"],
        connection=connection,
        workspace=Workspace.CAMPAIGN,
        active_campaign="campaign-1",
    )


def _entity(conn: sqlite3.Connection, entity_id: str) -> None:
    conn.execute(
        "INSERT INTO entities (entity_id, owner_scope, campaign_id, name) "
        "VALUES (?, 'campaign', 'campaign-1', ?)",
        (entity_id, entity_id),
    )


def _open_session(runtime: TabletopRuntime) -> str:
    result = runtime.start_session(json.dumps({"session_id": "session-1"}))
    assert result["ok"], result
    return "session-1"


def test_open_scene_returns_the_new_scene(runtime: TabletopRuntime) -> None:
    result = runtime.open_scene(json.dumps({"scene_id": "scene-1", "name": "Crossroads"}))
    assert result["ok"], result
    assert result["data"]["scene"]["name"] == "Crossroads"
    assert result["data"]["scene"]["status"] == "open"
    assert result["data"]["scene"]["scene_id"] == "scene-1"


def test_current_scene_reports_the_open_scene(
    runtime: TabletopRuntime, connection: sqlite3.Connection
) -> None:
    runtime.open_scene(json.dumps({"scene_id": "scene-1", "name": "Crossroads"}))
    result = runtime.get_current_scene(json.dumps({}))
    assert result["ok"], result
    assert result["data"]["scene"]["scene_id"] == "scene-1"


def test_current_scene_is_absent_before_any_scene_opens(runtime: TabletopRuntime) -> None:
    result = runtime.get_current_scene(json.dumps({}))
    assert result["ok"]
    assert result["data"]["scene"] is None


def test_opening_a_second_scene_is_rejected(
    runtime: TabletopRuntime, connection: sqlite3.Connection
) -> None:
    runtime.open_scene(json.dumps({"scene_id": "scene-1", "name": "Crossroads"}))
    result = runtime.open_scene(json.dumps({"scene_id": "scene-2", "name": "Second"}))
    assert result["ok"] is False
    assert result["error"]["code"] == "scene_invitation_failed" or "scene" in result["error"]["code"]


def test_open_scene_on_an_archived_campaign_is_rejected(
    runtime: TabletopRuntime, connection: sqlite3.Connection
) -> None:
    CampaignStore(connection).archive_campaign("campaign-1")
    result = runtime.open_scene(json.dumps({"scene_id": "scene-1", "name": "Crossroads"}))
    assert result["ok"] is False
    assert result["error"]["code"] == "campaign_archived"


def test_close_scene_records_the_end(runtime: TabletopRuntime) -> None:
    runtime.open_scene(json.dumps({"scene_id": "scene-1", "name": "Crossroads"}))
    result = runtime.close_scene(json.dumps({"scene_id": "scene-1"}))
    assert result["ok"], result
    assert result["data"]["scene"]["status"] == "closed"
    assert runtime.get_current_scene(json.dumps({}))["data"]["scene"] is None


def test_transition_closes_the_old_scene_and_opens_the_new_one(
    runtime: TabletopRuntime, connection: sqlite3.Connection
) -> None:
    runtime.open_scene(json.dumps({"scene_id": "scene-a", "name": "Crossroads"}))
    result = runtime.transition_scene(
        json.dumps({"from_scene_id": "scene-a", "scene_id": "scene-b", "name": "Archive"})
    )
    assert result["ok"], result

    rows = {
        row["scene_id"]: row
        for row in connection.execute(
            "SELECT scene_id, status FROM scenes ORDER BY scene_id"
        ).fetchall()
    }
    assert rows["scene-a"]["status"] == "closed"
    assert rows["scene-b"]["status"] == "open"
    assert runtime.get_current_scene(json.dumps({}))["data"]["scene"]["scene_id"] == "scene-b"


def test_transition_emits_close_and_open_events_in_order(
    runtime: TabletopRuntime, connection: sqlite3.Connection
) -> None:
    runtime.open_scene(json.dumps({"scene_id": "scene-a", "name": "Crossroads"}))
    runtime.transition_scene(
        json.dumps({"from_scene_id": "scene-a", "scene_id": "scene-b", "name": "Archive"})
    )
    types = [e.event_type for e in EventStore(connection).read("campaign-1")]
    assert types == ["scene.opened", "scene.closed", "scene.opened"]


def test_a_failed_transition_leaves_no_rows_or_events(
    runtime: TabletopRuntime, connection: sqlite3.Connection
) -> None:
    runtime.open_scene(json.dumps({"scene_id": "scene-a", "name": "Crossroads"}))
    before = [e.event_type for e in EventStore(connection).read("campaign-1")]
    result = runtime.transition_scene(
        json.dumps({"from_scene_id": "scene-b", "scene_id": "scene-c", "name": "Nowhere"})
    )
    assert result["ok"] is False
    assert [e.event_type for e in EventStore(connection).read("campaign-1")] == before
    assert connection.execute(
        "SELECT COUNT(*) FROM scenes WHERE scene_id = 'scene-c'"
    ).fetchone()[0] == 0


def test_enter_and_exit_track_presence(
    runtime: TabletopRuntime, connection: sqlite3.Connection
) -> None:
    _entity(connection, "pc-ada")
    runtime.open_scene(json.dumps({"scene_id": "scene-1", "name": "Crossroads"}))
    entered = runtime.enter_scene(
        json.dumps({"scene_id": "scene-1", "entity_id": "pc-ada", "presence_type": "pc"})
    )
    assert entered["ok"], entered
    assert runtime.get_current_scene(json.dumps({}))["data"]["present_entity_ids"] == [
        "pc-ada"
    ]

    exited = runtime.exit_scene(
        json.dumps({"scene_id": "scene-1", "entity_id": "pc-ada"})
    )
    assert exited["ok"], exited
    assert runtime.get_current_scene(json.dumps({}))["data"]["present_entity_ids"] == []


def test_entering_an_entity_from_another_campaign_is_rejected(
    runtime: TabletopRuntime, connection: sqlite3.Connection
) -> None:
    CampaignStore(connection).create_campaign("campaign-2", "Second", "freeform")
    connection.execute(
        "INSERT INTO entities (entity_id, owner_scope, campaign_id, name) "
        "VALUES ('npc-other', 'campaign', 'campaign-2', 'Other')",
    )
    runtime.open_scene(json.dumps({"scene_id": "scene-1", "name": "Crossroads"}))
    result = runtime.enter_scene(
        json.dumps({"scene_id": "scene-1", "entity_id": "npc-other", "presence_type": "npc"})
    )
    assert result["ok"] is False


def test_game_time_round_trips(runtime: TabletopRuntime) -> None:
    assert runtime.get_game_time(json.dumps({}))["data"]["game_time"] is None
    set_result = runtime.set_game_time(
        json.dumps({"in_world_label": "Day 3, morning", "in_world_minutes": 2880})
    )
    assert set_result["ok"], set_result
    assert runtime.get_game_time(json.dumps({}))["data"]["game_time"]["in_world_label"] == (
        "Day 3, morning"
    )


def test_setting_game_time_emits_one_event(runtime: TabletopRuntime, connection: sqlite3.Connection) -> None:
    runtime.set_game_time(json.dumps({"in_world_label": "Day 1", "in_world_minutes": 0}))
    types = [e.event_type for e in EventStore(connection).read("campaign-1")]
    assert types == ["scene.time_changed"]


def test_scene_operations_require_storage(tmp_path: Path) -> None:
    runtime = TabletopRuntime(
        tmp_path,
        campaign_roots=[],
        plugin_roots=[],
        connection=None,
        workspace=Workspace.CAMPAIGN,
        active_campaign="campaign-1",
    )
    for operation, payload in (
        (runtime.open_scene, {"scene_id": "s", "name": "n"}),
        (runtime.close_scene, {"scene_id": "s"}),
        (runtime.get_current_scene, {}),
        (runtime.enter_scene, {"scene_id": "s", "entity_id": "e", "presence_type": "pc"}),
        (runtime.exit_scene, {"scene_id": "s", "entity_id": "e"}),
        (runtime.get_game_time, {}),
        (runtime.set_game_time, {"in_world_label": "Day 1"}),
    ):
        result = operation(json.dumps(payload))
        assert result["ok"] is False, operation
        assert result["error"]["code"] == "storage_not_configured"


def test_ending_a_session_closes_its_open_scene_first(
    tmp_path: Path, connection: sqlite3.Connection
) -> None:
    """No scene may outlive the session it was playing in."""
    _entity(connection, "pc-ada")
    runtime = TabletopRuntime(
        tmp_path,
        campaign_roots=[tmp_path / "campaigns"],
        plugin_roots=[Path(__file__).resolve().parents[2] / "systems"],
        connection=connection,
        workspace=Workspace.CAMPAIGN,
        active_campaign="campaign-1",
    )
    runtime.start_session(json.dumps({"session_id": "session-1"}))
    runtime.open_scene(
        json.dumps({"scene_id": "scene-1", "name": "Crossroads", "session_id": "session-1"})
    )
    runtime.enter_scene(
        json.dumps({"scene_id": "scene-1", "entity_id": "pc-ada", "presence_type": "pc"})
    )

    assert runtime.end_session()["ok"] is True

    types = [e.event_type for e in EventStore(connection).read("campaign-1")]
    assert types.index("scene.closed") < types.index("session.ended")
    row = connection.execute(
        "SELECT status, ended_at FROM scenes WHERE scene_id = 'scene-1'"
    ).fetchone()
    assert row["status"] == "closed"
    assert runtime.get_current_scene(json.dumps({}))["data"]["scene"] is None


def test_ending_a_session_with_no_open_scene_still_ends(
    tmp_path: Path, connection: sqlite3.Connection
) -> None:
    runtime = TabletopRuntime(
        tmp_path,
        campaign_roots=[tmp_path / "campaigns"],
        plugin_roots=[Path(__file__).resolve().parents[2] / "systems"],
        connection=connection,
        workspace=Workspace.CAMPAIGN,
        active_campaign="campaign-1",
    )
    runtime.start_session(json.dumps({"session_id": "session-1"}))
    assert runtime.end_session()["ok"] is True
    assert "session.ended" in [
        e.event_type for e in EventStore(connection).read("campaign-1")
    ]


def test_turn_effects_default_to_the_current_open_scene(
    tmp_path: Path, connection: sqlite3.Connection
) -> None:
    """A turn with no explicit scene writes into the campaign's open scene."""
    runtime = TabletopRuntime(
        tmp_path,
        campaign_roots=[],
        plugin_roots=[Path(__file__).resolve().parents[2] / "systems"],
        connection=connection,
        workspace=Workspace.CAMPAIGN,
        active_campaign="campaign-1",
    )
    runtime.open_scene(json.dumps({"scene_id": "scene-1", "name": "Crossroads"}))
    _seed_scene_state(connection, "scene-1", 5)

    result = runtime.resolve_action(
        json.dumps(
            {
                "actor": {"id": "pc-ada"},
                "action_type": "decrement_resource",
                "parameters": {"path": ["scene", "system", "lanterns"], "amount": 2},
            }
        )
    )
    assert result["ok"], result

    assert _scene_lanterns(connection, "scene-1") == 3
    event_scene_ids = [
        e.scene_id
        for e in EventStore(connection).read("campaign-1")
        if e.event_type == "action.resolved"
    ]
    assert event_scene_ids == ["scene-1"]


def test_a_turn_with_no_open_scene_keeps_writing_campaign_scope(
    tmp_path: Path, connection: sqlite3.Connection
) -> None:
    runtime = TabletopRuntime(
        tmp_path,
        campaign_roots=[],
        plugin_roots=[Path(__file__).resolve().parents[2] / "systems"],
        connection=connection,
        workspace=Workspace.CAMPAIGN,
        active_campaign="campaign-1",
    )
    connection.execute(
        "UPDATE campaigns SET system_state = '{\"lanterns\": 5}' WHERE campaign_id = 'campaign-1'"
    )
    result = runtime.resolve_action(
        json.dumps(
            {
                "actor": {"id": "pc-ada"},
                "action_type": "decrement_resource",
                "parameters": {
                    "path": ["campaign", "system", "lanterns"],
                    "amount": 2,
                },
            }
        )
    )
    assert result["ok"], result
    row = connection.execute(
        "SELECT system_state FROM campaigns WHERE campaign_id = 'campaign-1'"
    ).fetchone()
    assert json.loads(row["system_state"])["lanterns"] == 3


def _seed_scene_state(connection: sqlite3.Connection, scene_id: str, lanterns: int) -> None:
    connection.execute(
        "UPDATE scenes SET system_state = ? WHERE scene_id = ?",
        (json.dumps({"lanterns": lanterns}), scene_id),
    )


def _scene_lanterns(connection: sqlite3.Connection, scene_id: str) -> int:
    row = connection.execute(
        "SELECT system_state FROM scenes WHERE scene_id = ?", (scene_id,)
    ).fetchone()
    return json.loads(row["system_state"])["lanterns"]
