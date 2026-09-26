"""`campaign setup` from a manifest and through the wizard.

Every case drives a real ``python -m tabletop.cli`` process or an injected
prompt sequence, so a command that only works in-process cannot pass.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tabletop.cli.setup_wizard import SetupCancelled, WizardPrompts, run_wizard
from tabletop.campaign.setup import SetupManifestError
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate

REPO_ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN = "demo-campaign"

_MANIFEST = """
campaign_id: demo-campaign
name: Demo Campaign
system_id: freeform
participants:
  - participant_id: gm-1
    display_name: The GM
    role: gm
  - participant_id: player-1
    display_name: Ada
    role: player
characters:
  - entity_id: pc-ada
    name: Ada
starting_scene:
  scene_id: scene-1
  name: Flooded Hall
game_time:
  in_world_label: Day 1
"""


def _run(*args: str, database: Path, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TABLETOP_DATABASE_PATH"] = str(database)
    env.pop("TABLETOP_CAMPAIGN", None)
    return subprocess.run(
        [sys.executable, "-m", "tabletop.cli", *args],
        cwd=REPO_ROOT,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "setup-cli.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


@pytest.fixture
def manifest_file(tmp_path: Path) -> Path:
    path = tmp_path / "campaign.setup.yaml"
    path.write_text(_MANIFEST, encoding="utf-8")
    return path


# -- from a manifest --------------------------------------------------------


def test_dry_run_prints_a_plan_and_writes_nothing(
    database: Path, manifest_file: Path
) -> None:
    result = _run(
        "campaign", "setup", "--from", str(manifest_file), "--dry-run", database=database
    )
    assert result.returncode == 0, result.stderr
    assert "plan for demo-campaign" in result.stdout
    assert "CREATE campaign" in result.stdout
    assert "nothing was written" in result.stdout
    conn = connect(database)
    try:
        assert CampaignStore(conn).get_campaign(CAMPAIGN) is None
    finally:
        conn.close()


def test_apply_creates_the_campaign(database: Path, manifest_file: Path) -> None:
    result = _run(
        "campaign", "setup", "--from", str(manifest_file), "--yes", database=database
    )
    assert result.returncode == 0, result.stderr
    conn = connect(database)
    try:
        campaign = CampaignStore(conn).get_campaign(CAMPAIGN)
        assert campaign is not None
        assert campaign["name"] == "Demo Campaign"
    finally:
        conn.close()


def test_setup_prints_the_commands_to_start_next(
    database: Path, manifest_file: Path
) -> None:
    result = _run(
        "campaign", "setup", "--from", str(manifest_file), "--yes", database=database
    )
    assert result.returncode == 0, result.stderr
    assert "gamemaster campaign start demo-campaign" in result.stdout
    assert "gamemaster campaign validate demo-campaign" in result.stdout


def test_setup_never_launches_a_process(
    database: Path, manifest_file: Path
) -> None:
    """Setup configures; launching is a separate, explicit command."""
    result = _run(
        "campaign", "setup", "--from", str(manifest_file), "--yes", database=database
    )
    assert result.returncode == 0, result.stderr
    for forbidden in ("docker compose", "docker-compose up", "Starting services"):
        assert forbidden not in result.stdout


def test_a_rerun_reports_everything_already_satisfied(
    database: Path, manifest_file: Path
) -> None:
    _run("campaign", "setup", "--from", str(manifest_file), "--yes", database=database)
    again = _run(
        "campaign", "setup", "--from", str(manifest_file), "--dry-run", database=database
    )
    assert again.returncode == 0, again.stderr
    assert "already satisfied" in again.stdout


def test_a_declining_operator_writes_nothing(
    database: Path, manifest_file: Path
) -> None:
    result = _run(
        "campaign", "setup", "--from", str(manifest_file), database=database, stdin="n\n"
    )
    assert result.returncode != 0
    assert "cancelled" in result.stdout
    conn = connect(database)
    try:
        assert CampaignStore(conn).get_campaign(CAMPAIGN) is None
    finally:
        conn.close()


def test_a_missing_manifest_exits_nonzero(database: Path, tmp_path: Path) -> None:
    result = _run(
        "campaign", "setup", "--from", str(tmp_path / "absent.yaml"), database=database
    )
    assert result.returncode != 0
    assert "not found" in (result.stdout + result.stderr)


def test_an_invalid_manifest_exits_nonzero(
    database: Path, tmp_path: Path
) -> None:
    bad = tmp_path / "campaign.setup.yaml"
    bad.write_text("campaign_id: demo\nname: Demo\nsystem_id: not-a-system\n",
                   encoding="utf-8")
    result = _run("campaign", "setup", "--from", str(bad), database=database)
    assert result.returncode != 0


def test_a_conflicting_manifest_exits_nonzero(
    database: Path, manifest_file: Path
) -> None:
    conn = connect(database)
    try:
        CampaignStore(conn).create_campaign(CAMPAIGN, "A Different Name", "freeform")
    finally:
        conn.close()
    result = _run(
        "campaign", "setup", "--from", str(manifest_file), "--yes", database=database
    )
    assert result.returncode != 0
    conn = connect(database)
    try:
        assert CampaignStore(conn).get_campaign(CAMPAIGN)["name"] == "A Different Name"
    finally:
        conn.close()


# -- the wizard -------------------------------------------------------------


def _prompts(answers: list[str], said: list[str] | None = None) -> WizardPrompts:
    queue = list(answers)

    def ask(_prompt: str) -> str:
        if not queue:
            raise SetupCancelled("no more input")
        return queue.pop(0)

    return WizardPrompts(ask=ask, say=(said.append if said is not None else lambda _: None))


def test_the_wizard_builds_the_same_manifest_object() -> None:
    manifest = run_wizard(
        _prompts(
            [
                "demo-campaign",  # campaign id
                "Demo Campaign",  # name
                "freeform",  # system
                "gm-1",  # participant id
                "The GM",  # display name
                "gm",  # role
                "n",  # bind a principal?
                "player-1",  # participant id
                "Ada",  # display name
                "player",  # role
                "n",  # bind a principal?
                "",  # finish participants
                "pc-ada",  # character id
                "Ada",  # character name
                "",  # finish characters
                "y",  # define a starting scene
                "scene-1",  # scene id
                "Flooded Hall",  # scene name
            ]
        )
    )
    assert manifest.campaign_id == "demo-campaign"
    assert manifest.system_id == "freeform"
    assert [p.participant_id for p in manifest.participants] == ["gm-1", "player-1"]
    assert manifest.participants[0].role == "gm"
    assert manifest.characters == (("pc-ada", "Ada", None),)
    assert manifest.starting_scene is not None
    assert manifest.starting_scene.scene_id == "scene-1"


def test_the_wizard_rejects_an_unknown_system() -> None:
    with pytest.raises(SetupManifestError, match="unknown system"):
        run_wizard(_prompts(["demo-campaign", "Demo", "shadowrun"]))


def test_the_wizard_rejects_an_invalid_slug_and_retries() -> None:
    said: list[str] = []
    manifest = run_wizard(
        _prompts(
            ["Not A Slug", "demo-campaign", "Demo", "freeform", "", "n", "y", "scene-1"],
            said,
        )
    )
    assert manifest.campaign_id == "demo-campaign"
    assert any("must match" in line for line in said)


def test_the_wizard_cancels_on_end_of_input() -> None:
    with pytest.raises(SetupCancelled):
        run_wizard(_prompts([]))


def test_the_wizard_ends_with_commands_rather_than_starting_anything(
    database: Path, manifest_file: Path
) -> None:
    """The wizard path and the manifest path share one apply service."""
    from tabletop.campaign.setup import apply_setup, load_setup_manifest, plan_setup

    conn = connect(database)
    try:
        manifest = load_setup_manifest(manifest_file)
        plan = plan_setup(conn, manifest)
        assert plan.has_work
        apply_setup(conn, manifest)
        assert CampaignStore(conn).get_campaign(CAMPAIGN) is not None
    finally:
        conn.close()
