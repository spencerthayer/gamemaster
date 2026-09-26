"""Setup materializes the configured starting state through real services.

Applying a manifest must not hand-write rows. It composes the same campaign,
membership, plugin, scene, and clock services play uses, so a configured
campaign is indistinguishable from one built by hand.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.campaign.models import PresenceType, SceneStatus
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.setup import (
    CampaignSetupManifest,
    SetupContent,
    SetupManifestError,
    SetupParticipant,
    SetupScene,
    apply_setup,
    plan_setup,
)
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "demo-campaign"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "starting.db")
    migrate(connection)
    yield connection
    connection.close()


def _manifest(tmp_path: Path, **overrides: object) -> CampaignSetupManifest:
    (tmp_path / "rules.md").write_text("Gate DC 15", encoding="utf-8")
    payload: dict[str, object] = {
        "campaign_id": _CAMPAIGN,
        "name": "Demo Campaign",
        "system_id": "freeform",
        "participants": (
            SetupParticipant(
                participant_id="gm-1", display_name="The GM", role="gm",
                character_ids=("npc-vor",),
            ),
            SetupParticipant(
                participant_id="player-1", display_name="Ada",
                character_ids=("pc-ada",),
            ),
        ),
        "characters": (
            ("pc-ada", "Ada", "player-1"),
            ("npc-vor", "Vor", "gm-1"),
            ("loc-hall", "Flooded Hall", None),
        ),
        "content": (SetupContent(path=tmp_path / "rules.md", role="rules"),),
        "starting_state": {"scene": {"flood_level": "waist"}},
        "starting_scene": SetupScene(
            scene_id="scene-1",
            name="Flooded Hall",
            location_entity_id="loc-hall",
            present=("pc-ada", "npc-vor"),
        ),
        "game_time": {"in_world_label": "Day 3, dusk", "in_world_minutes": 2880},
    }
    payload.update(overrides)
    return CampaignSetupManifest(**payload)  # type: ignore[arg-type]


def test_setup_creates_every_configured_entity(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    apply_setup(conn, _manifest(tmp_path))
    store = CampaignStore(conn)
    for entity_id in ("pc-ada", "npc-vor", "loc-hall"):
        assert store.get_entity(_CAMPAIGN, entity_id) is not None


def test_setup_grants_character_ownership(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    apply_setup(conn, _manifest(tmp_path))
    from tabletop.campaign.membership import MembershipStore

    membership = MembershipStore(conn)
    assert membership.controlled_entity_ids(_CAMPAIGN, "player-1") == frozenset({"pc-ada"})
    assert membership.controlled_entity_ids(_CAMPAIGN, "gm-1") == frozenset({"npc-vor"})


def test_setup_opens_exactly_one_scene_with_its_location(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    apply_setup(conn, _manifest(tmp_path))
    scenes = SceneStore(conn)
    open_scenes = [s for s in scenes.list_scenes(_CAMPAIGN) if s.is_open]
    assert len(open_scenes) == 1
    assert open_scenes[0].scene_id == "scene-1"
    assert open_scenes[0].status is SceneStatus.OPEN
    assert open_scenes[0].location_entity_id == "loc-hall"
    assert open_scenes[0].system_state == {"flood_level": "waist"}


def test_setup_records_presence_for_the_starting_scene(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    apply_setup(conn, _manifest(tmp_path))
    members = SceneStore(conn).get_members(_CAMPAIGN, "scene-1")
    assert {m.entity_id for m in members} == {"pc-ada", "npc-vor"}
    assert all(m.presence_type is PresenceType.NPC for m in members)
    assert all(m.is_present for m in members)

def test_setup_sets_the_campaign_clock(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    apply_setup(conn, _manifest(tmp_path))
    clock = SceneStore(conn).get_game_time(_CAMPAIGN)
    assert clock is not None
    assert clock.in_world_label == "Day 3, dusk"
    assert clock.in_world_minutes == 2880


def test_setup_writes_no_campaign_events_for_the_scene(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Setup configures; play emits. It must not forge play history."""

    apply_setup(conn, _manifest(tmp_path))
    types = [
        row[0]
        for row in conn.execute(
            "SELECT event_type FROM events WHERE campaign_id = ?", (_CAMPAIGN,)
        ).fetchall()
    ]
    assert "action.resolved" not in types
    assert "scene.opened" not in types


def test_setup_on_an_active_campaign_creates_no_second_scene(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    manifest = _manifest(tmp_path)
    apply_setup(conn, manifest)
    scenes = SceneStore(conn)
    # Play moved on to a different scene before setup was rerun.
    scenes.close_scene(_CAMPAIGN, "scene-1")
    scenes.open_scene(_CAMPAIGN, "scene-2", "Somewhere Else")

    rerun = apply_setup(conn, manifest)
    assert [s.scene_id for s in SceneStore(conn).list_scenes(_CAMPAIGN)] == [
        "scene-1",
        "scene-2",
    ]
    assert not any(
        action.kind == "OPEN scene" and not action.already_satisfied
        for action in rerun.actions
    )


def test_setup_on_an_active_campaign_does_not_reopen_the_starting_scene(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    manifest = _manifest(tmp_path)
    apply_setup(conn, manifest)
    scenes = SceneStore(conn)
    scenes.close_scene(_CAMPAIGN, "scene-1")
    assert scenes.get_scene(_CAMPAIGN, "scene-1").status is SceneStatus.CLOSED

    # A rerun must not resurrect a scene the operator already closed.
    apply_setup(conn, manifest)
    assert scenes.get_scene(_CAMPAIGN, "scene-1").status is SceneStatus.CLOSED


def test_a_manifest_naming_an_unknown_system_still_parses(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Parsing and system availability are separate concerns."""

    manifest = _manifest(tmp_path, system_id="shadowrun")
    assert manifest.system_id == "shadowrun"
    # The campaign record carries what the manifest declared; plugin loading
    # is the runtime's concern, not the manifest's.
    apply_setup(conn, manifest)
    assert CampaignStore(conn).get_campaign(_CAMPAIGN)["system_id"] == "shadowrun"
