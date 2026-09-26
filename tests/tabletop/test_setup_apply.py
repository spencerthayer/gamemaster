"""Setup plans are reviewable, apply is idempotent, and conflicts fail loudly."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.models import PresenceType
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.setup import (
    CampaignSetupManifest,
    SetupConflictError,
    SetupContent,
    SetupManifestError,
    SetupParticipant,
    SetupScene,
    apply_setup,
    load_setup_manifest,
    plan_setup,
)
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "demo-campaign"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "setup.db")
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
                participant_id="gm-1",
                display_name="The GM",
                role="gm",
                character_ids=("npc-vor",),
                principals=(("telegram", "12345"),),
            ),
            SetupParticipant(
                participant_id="player-1",
                display_name="Ada",
                character_ids=("pc-ada",),
            ),
        ),
        "characters": (
            ("pc-ada", "Ada", "player-1"),
            ("npc-vor", "Vor", "gm-1"),
        ),
        "content": (SetupContent(path=tmp_path / "rules.md", role="rules"),),
        "starting_state": {"scene": {"lanterns": 3}},
        "starting_scene": SetupScene(
            scene_id="scene-1", name="Flooded Hall", present=("pc-ada",)
        ),
        "game_time": {"in_world_label": "Day 3, dusk", "in_world_minutes": 2880},
    }
    payload.update(overrides)
    return CampaignSetupManifest(**payload)  # type: ignore[arg-type]




# -- planning ---------------------------------------------------------------


def test_a_dry_run_plan_writes_nothing(conn: sqlite3.Connection, tmp_path: Path) -> None:
    before = _row_counts(conn)
    plan_setup(conn, _manifest(tmp_path))
    assert _row_counts(conn) == before


def test_the_plan_names_every_required_step(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    plan = plan_setup(conn, _manifest(tmp_path))
    kinds = {action.kind for action in plan.actions}
    assert kinds == {
        "CREATE campaign",
        "ADD participant",
        "BIND principal",
        "GRANT character",
        "GRANT control",
        "INSTALL content",
        "APPLY starting state",
        "OPEN scene",
        "SET game time",
    }


def test_the_plan_is_json_safe(conn: sqlite3.Connection, tmp_path: Path) -> None:
    import json

    plan = plan_setup(conn, _manifest(tmp_path))
    assert json.loads(json.dumps(plan.to_dict()))


def test_a_fresh_plan_has_work(conn: sqlite3.Connection, tmp_path: Path) -> None:
    assert plan_setup(conn, _manifest(tmp_path)).has_work is True


# -- applying ---------------------------------------------------------------


def test_applying_creates_the_campaign(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    apply_setup(conn, _manifest(tmp_path))
    campaign = CampaignStore(conn).get_campaign(_CAMPAIGN)
    assert campaign is not None
    assert campaign["name"] == "Demo Campaign"
    assert campaign["system_id"] == "freeform"


def test_applying_creates_participants_principals_and_controls(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    apply_setup(conn, _manifest(tmp_path))
    membership = MembershipStore(conn)
    assert {row["participant_id"] for row in membership.list_participants(_CAMPAIGN)} == {
        "gm-1",
        "player-1",
    }
    assert membership.list_principals(_CAMPAIGN)[0]["channel"] == "telegram"
    assert membership.controlled_entity_ids(_CAMPAIGN, "player-1") == frozenset({"pc-ada"})


def test_applying_creates_the_starting_scene_and_clock(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    apply_setup(conn, _manifest(tmp_path))
    scenes = SceneStore(conn)
    scene = scenes.get_scene(_CAMPAIGN, "scene-1")
    assert scene is not None
    assert scene.status.value == "open"
    assert scene.system_state == {"lanterns": 3}
    assert scenes.get_present_entity_ids(_CAMPAIGN, "scene-1") == ("pc-ada",)
    assert scenes.get_game_time(_CAMPAIGN).in_world_label == "Day 3, dusk"


# -- idempotency ------------------------------------------------------------


def test_a_rerun_creates_nothing_new(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    manifest = _manifest(tmp_path)
    apply_setup(conn, manifest)
    first = _row_counts(conn)
    apply_setup(conn, manifest)
    assert _row_counts(conn) == first


def test_a_rerun_reports_no_database_work(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    manifest = _manifest(tmp_path)
    apply_setup(conn, manifest)
    rerun = plan_setup(conn, manifest)
    database_steps = [
        action
        for action in rerun.actions
        if action.kind != "INSTALL content"
    ]
    assert all(action.already_satisfied for action in database_steps), database_steps


def test_a_rerun_does_not_duplicate_presence(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    manifest = _manifest(tmp_path)
    apply_setup(conn, manifest)
    apply_setup(conn, manifest)
    assert len(SceneStore(conn).get_members(_CAMPAIGN, "scene-1")) == 1


# -- conflicts --------------------------------------------------------------


def test_a_conflicting_campaign_name_fails_rather_than_overwriting(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    CampaignStore(conn).create_campaign(_CAMPAIGN, "A Different Name", "freeform")
    with pytest.raises(SetupConflictError, match="named"):
        plan_setup(conn, _manifest(tmp_path))
    assert CampaignStore(conn).get_campaign(_CAMPAIGN)["name"] == "A Different Name"


def test_a_conflicting_system_fails_rather_than_overwriting(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    CampaignStore(conn).create_campaign(_CAMPAIGN, "Demo Campaign", "dnd5e")
    with pytest.raises(SetupConflictError, match="uses system"):
        plan_setup(conn, _manifest(tmp_path))
    assert CampaignStore(conn).get_campaign(_CAMPAIGN)["system_id"] == "dnd5e"


def test_a_conflicting_character_name_fails(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    CampaignStore(conn).create_campaign(_CAMPAIGN, "Demo Campaign", "freeform")
    CampaignStore(conn).upsert_entity(_CAMPAIGN, "pc-ada", "Someone Else")
    with pytest.raises(SetupConflictError, match="is named"):
        plan_setup(conn, _manifest(tmp_path))


def test_a_failed_apply_leaves_nothing_half_configured(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    CampaignStore(conn).create_campaign(_CAMPAIGN, "Demo Campaign", "freeform")
    CampaignStore(conn).upsert_entity(_CAMPAIGN, "pc-ada", "Someone Else")
    before = _row_counts(conn)
    with pytest.raises(SetupConflictError):
        apply_setup(conn, _manifest(tmp_path))
    assert _row_counts(conn) == before


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in (
        "campaigns",
        "entities",
        "participants",
        "participant_principals",
        "character_controls",
        "scenes",
        "scene_members",
        "campaign_clock",
    ):
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return counts
