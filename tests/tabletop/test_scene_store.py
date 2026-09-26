"""Scene, presence, and campaign-clock invariants in ``SceneStore``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.campaign.models import (
    GameTime,
    PresenceType,
    Scene,
    SceneInvariantError,
    SceneStatus,
)
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate, transaction


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "scenes.db")
    migrate(connection)
    CampaignStore(connection).create_campaign("campaign-1", "First", "freeform")
    CampaignStore(connection).create_campaign("campaign-2", "Second", "freeform")
    yield connection
    connection.close()


@pytest.fixture
def store(conn: sqlite3.Connection) -> SceneStore:
    return SceneStore(conn)


def _entity(conn: sqlite3.Connection, entity_id: str, *, campaign_id: str = "campaign-1") -> None:
    conn.execute(
        "INSERT INTO entities (entity_id, owner_scope, campaign_id, name) "
        "VALUES (?, 'campaign', ?, ?)",
        (entity_id, campaign_id, f"Entity {entity_id}"),
    )



def _open(
    store: SceneStore,
    scene_id: str = "scene-1",
    *,
    campaign_id: str = "campaign-1",
    started_at: str = "2026-09-25T00:00:00Z",
) -> Scene:
    return store.open_scene(campaign_id, scene_id, f"Scene {scene_id}", started_at=started_at)


def test_open_scene_starts_open_with_no_end(
    store: SceneStore, conn: sqlite3.Connection
) -> None:
    scene = _open(store)
    assert scene.status is SceneStatus.OPEN
    assert scene.is_open
    assert scene.ended_at is None
    assert store.get_open_scene("campaign-1") == scene


def test_second_open_scene_is_rejected(store: SceneStore) -> None:
    _open(store)
    with pytest.raises(SceneInvariantError, match="already has open scene"):
        store.open_scene("campaign-1", "scene-2", "Second")
    assert store.get_open_scene("campaign-1").scene_id == "scene-1"


def test_a_closed_campaign_can_open_a_scene(store: SceneStore) -> None:
    _open(store, campaign_id="campaign-2")
    assert store.get_open_scene("campaign-2").scene_id == "scene-1"
    assert store.get_open_scene("campaign-1") is None


def test_reopening_the_same_scene_id_is_rejected(store: SceneStore) -> None:
    _open(store)
    with pytest.raises(SceneInvariantError, match="already exists"):
        store.open_scene("campaign-1", "scene-1", "Again")


def test_close_records_end_and_stops_returning_the_open_scene(
    store: SceneStore,
) -> None:
    _open(store)
    closed = store.close_scene("campaign-1", "scene-1", ended_at="2026-09-25T02:00:00Z")
    assert closed.status is SceneStatus.CLOSED
    assert closed.ended_at == "2026-09-25T02:00:00Z"
    assert store.get_open_scene("campaign-1") is None
    assert [s.scene_id for s in store.list_scenes("campaign-1")] == ["scene-1"]


def test_closing_a_closed_scene_is_rejected(store: SceneStore) -> None:
    _open(store)
    store.close_scene("campaign-1", "scene-1")
    with pytest.raises(SceneInvariantError, match="already closed"):
        store.close_scene("campaign-1", "scene-1")


def test_a_scene_cannot_end_before_it_started(store: SceneStore) -> None:
    store.open_scene(
        "campaign-1", "scene-1", "Crossroads", started_at="2026-09-25T02:00:00Z"
    )
    with pytest.raises(SceneInvariantError, match="cannot end before"):
        store.close_scene("campaign-1", "scene-1", ended_at="2026-09-25T01:00:00Z")
    assert store.get_scene("campaign-1", "scene-1").status is SceneStatus.OPEN


def test_a_new_scene_can_open_after_the_previous_one_closes(
    store: SceneStore,
) -> None:
    _open(store, "scene-a")
    store.close_scene("campaign-1", "scene-a")
    _open(store, "scene-b")
    assert store.get_open_scene("campaign-1").scene_id == "scene-b"
    assert [s.scene_id for s in store.list_scenes("campaign-1")] == ["scene-a", "scene-b"]


def test_closing_a_scene_exits_everyone_still_present(
    store: SceneStore, conn: sqlite3.Connection
) -> None:
    _entity(conn, "pc-ada")
    _open(store)
    store.enter(
        "campaign-1", "scene-1", "pc-ada", PresenceType.PC,
        entered_at="2026-09-25T01:00:00Z",
    )
    store.close_scene("campaign-1", "scene-1", ended_at="2026-09-25T05:00:00Z")
    members = store.get_members("campaign-1", "scene-1")
    assert [m.entity_id for m in members] == ["pc-ada"]
    assert members[0].exited_at == "2026-09-25T05:00:00Z"
    assert not members[0].is_present


def test_enter_and_exit_track_presence_intervals(
    store: SceneStore, conn: sqlite3.Connection
) -> None:
    _entity(conn, "npc-vor")
    _open(store)
    store.enter(
        "campaign-1", "scene-1", "npc-vor", PresenceType.NPC,
        entered_at="2026-09-25T01:00:00Z",
    )
    assert store.get_present_entity_ids("campaign-1", "scene-1") == ("npc-vor",)

    store.exit("campaign-1", "scene-1", "npc-vor", exited_at="2026-09-25T03:00:00Z")
    assert store.get_present_entity_ids("campaign-1", "scene-1") == ()

    store.enter("campaign-1", "scene-1", "npc-vor", PresenceType.NPC, entered_at="2026-09-25T04:00:00Z")
    members = store.get_members("campaign-1", "scene-1")
    assert len(members) == 1
    assert members[0].entered_at == "2026-09-25T04:00:00Z"
    assert members[0].exited_at is None


def test_entering_twice_without_exiting_is_rejected(
    store: SceneStore, conn: sqlite3.Connection
) -> None:
    _entity(conn, "pc-ada")
    _open(store)
    store.enter("campaign-1", "scene-1", "pc-ada", PresenceType.PC)
    with pytest.raises(SceneInvariantError, match="already present"):
        store.enter("campaign-1", "scene-1", "pc-ada", PresenceType.PC)


def test_entering_an_entity_from_another_campaign_is_rejected(
    store: SceneStore, conn: sqlite3.Connection
) -> None:
    _entity(conn, "npc-other", campaign_id="campaign-2")
    _open(store)
    with pytest.raises(SceneInvariantError, match="not a campaign entity"):
        store.enter("campaign-1", "scene-1", "npc-other", PresenceType.NPC)
    assert store.get_members("campaign-1", "scene-1") == ()


def test_entering_a_closed_scene_is_rejected(
    store: SceneStore, conn: sqlite3.Connection
) -> None:
    _entity(conn, "pc-ada")
    _open(store)
    store.close_scene("campaign-1", "scene-1")
    with pytest.raises(SceneInvariantError, match="closed scene"):
        store.enter("campaign-1", "scene-1", "pc-ada", PresenceType.PC)


def test_exiting_an_absent_entity_is_rejected(store: SceneStore) -> None:
    _open(store)
    with pytest.raises(SceneInvariantError, match="no presence record"):
        store.exit("campaign-1", "scene-1", "ghost")


def test_exit_cannot_precede_entry(store: SceneStore, conn: sqlite3.Connection) -> None:
    _entity(conn, "pc-ada")
    _open(store)
    store.enter("campaign-1", "scene-1", "pc-ada", PresenceType.PC, entered_at="2026-09-25T02:00:00Z")
    with pytest.raises(SceneInvariantError, match="cannot exit before"):
        store.exit("campaign-1", "scene-1", "pc-ada", exited_at="2026-09-25T01:00:00Z")


def test_presence_cannot_precede_the_scene(store: SceneStore, conn: sqlite3.Connection) -> None:
    _entity(conn, "pc-ada")
    store.open_scene("campaign-1", "scene-1", "Crossroads", started_at="2026-09-25T02:00:00Z")
    with pytest.raises(SceneInvariantError, match="cannot precede the scene start"):
        store.enter("campaign-1", "scene-1", "pc-ada", PresenceType.PC, entered_at="2026-09-25T01:00:00Z")


def test_presence_type_must_be_declared(store: SceneStore, conn: sqlite3.Connection) -> None:
    _entity(conn, "pc-ada")
    _open(store)
    with pytest.raises(SceneInvariantError, match="must be a PresenceType"):
        store.enter("campaign-1", "scene-1", "pc-ada", "ally")  # type: ignore[arg-type]


def test_setting_a_cross_campaign_location_is_rejected(
    store: SceneStore, conn: sqlite3.Connection
) -> None:
    _entity(conn, "loc-other", campaign_id="campaign-2")
    _open(store)
    with pytest.raises(SceneInvariantError, match="not a campaign entity"):
        store.set_location("campaign-1", "scene-1", "loc-other")
    assert store.get_scene("campaign-1", "scene-1").location_entity_id is None


def test_moving_a_closed_scene_is_rejected(store: SceneStore, conn: sqlite3.Connection) -> None:
    _entity(conn, "loc-hall")
    _open(store)
    store.close_scene("campaign-1", "scene-1")
    with pytest.raises(SceneInvariantError, match="it is closed"):
        store.set_location("campaign-1", "scene-1", "loc-hall")


def test_location_change_is_recorded(store: SceneStore, conn: sqlite3.Connection) -> None:
    _entity(conn, "loc-hall")
    _open(store)
    updated = store.set_location("campaign-1", "scene-1", "loc-hall")
    assert updated.location_entity_id == "loc-hall"
    assert store.set_location("campaign-1", "scene-1", None).location_entity_id is None


def test_game_time_is_absent_until_set_and_then_replaced(
    store: SceneStore, conn: sqlite3.Connection
) -> None:
    assert store.get_game_time("campaign-1") is None
    first = store.set_game_time("campaign-1", in_world_label="Day 1", in_world_minutes=0)
    assert isinstance(first, GameTime)
    second = store.set_game_time(
        "campaign-1", in_world_label="Day 2", in_world_minutes=1440
    )
    assert second.in_world_label == "Day 2"
    assert second.in_world_minutes == 1440
    rows = conn.execute(
        "SELECT COUNT(*) FROM campaign_clock WHERE campaign_id = 'campaign-1'"
    ).fetchone()[0]
    assert rows == 1


def test_game_time_rejects_negative_minutes(store: SceneStore) -> None:
    with pytest.raises(SceneInvariantError, match="cannot be negative"):
        store.set_game_time("campaign-1", in_world_minutes=-5)
    assert store.get_game_time("campaign-1") is None


def test_in_transaction_helpers_share_one_transaction(
    store: SceneStore, conn: sqlite3.Connection
) -> None:
    _entity(conn, "pc-ada")
    with transaction(conn):
        store.open_scene_in_transaction("campaign-1", "scene-1", "Crossroads")
        store.enter_in_transaction(
            "campaign-1", "scene-1", "pc-ada", PresenceType.PC
        )
    assert store.get_present_entity_ids("campaign-1", "scene-1") == ("pc-ada",)


def test_a_failed_transition_leaves_no_partial_scene(
    store: SceneStore, conn: sqlite3.Connection
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(conn):
            store.open_scene_in_transaction("campaign-1", "scene-1", "Crossroads")
            conn.execute(
                "INSERT INTO scenes "
                "(scene_id, campaign_id, name, status, started_at) "
                "VALUES ('scene-2', 'campaign-1', 'Second', 'open', "
                "'2026-09-25T00:00:00Z')",
            )
    assert store.list_scenes("campaign-1") == ()
    assert store.get_open_scene("campaign-1") is None
