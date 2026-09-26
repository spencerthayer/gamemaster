"""P3 acceptance: setup is a reproducible operator interface.

A dry run, an apply, and a rerun are driven as real CLI processes, and the
authoritative row and event counts are compared before and after. A dry run
must leave the database digest byte-identical.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tabletop.campaign.resume import resume_snapshot
from tabletop.campaign.store import CampaignStore
from tabletop.importing.store import authoritative_state_digest
from tabletop.storage.sqlite import connect, migrate

REPO_ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN = "acceptance-campaign"

_MANIFEST = """
campaign_id: acceptance-campaign
name: Acceptance Campaign
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
  - entity_id: loc-hall
    name: Flooded Hall
starting_state:
  scene:
    flood_level: waist
starting_scene:
  scene_id: scene-1
  name: Flooded Hall
  location_entity_id: loc-hall
  present: [pc-ada]
game_time:
  in_world_label: Day 3, dusk
  in_world_minutes: 2880
"""


def _run(*args: str, database: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TABLETOP_DATABASE_PATH"] = str(database)
    env.pop("TABLETOP_CAMPAIGN", None)
    return subprocess.run(
        [sys.executable, "-m", "tabletop.cli", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "acceptance.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


@pytest.fixture
def manifest_file(tmp_path: Path) -> Path:
    path = tmp_path / "campaign.setup.yaml"
    path.write_text(_MANIFEST, encoding="utf-8")
    return path


def _counts(database: Path) -> dict[str, int]:
    conn = connect(database)
    try:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "campaigns",
                "entities",
                "participants",
                "scenes",
                "scene_members",
                "campaign_clock",
                "events",
            )
        }
    finally:
        conn.close()


def test_dry_run_apply_and_rerun_are_reproducible(
    database: Path, manifest_file: Path
) -> None:
    dry = _run(
        "campaign", "setup", "--from", str(manifest_file), "--dry-run",
        database=database,
    )
    assert dry.returncode == 0, dry.stderr
    assert "plan for acceptance-campaign" in dry.stdout

    apply_result = _run(
        "campaign", "setup", "--from", str(manifest_file), "--yes", database=database
    )
    assert apply_result.returncode == 0, apply_result.stderr
    after_apply = _counts(database)

    rerun = _run(
        "campaign", "setup", "--from", str(manifest_file), "--yes", database=database
    )
    assert rerun.returncode == 0, rerun.stderr
    assert _counts(database) == after_apply


def test_a_dry_run_leaves_the_database_digest_unchanged(
    database: Path, manifest_file: Path
) -> None:
    conn = connect(database)
    try:
        before = authoritative_state_digest(conn, CAMPAIGN)
    finally:
        conn.close()

    dry = _run(
        "campaign", "setup", "--from", str(manifest_file), "--dry-run",
        database=database,
    )
    assert dry.returncode == 0, dry.stderr

    conn = connect(database)
    try:
        assert authoritative_state_digest(conn, CAMPAIGN) == before
    finally:
        conn.close()


def test_a_configured_campaign_is_ready_to_play(
    database: Path, manifest_file: Path
) -> None:
    _run("campaign", "setup", "--from", str(manifest_file), "--yes", database=database)
    conn = connect(database)
    try:
        campaign = CampaignStore(conn).get_campaign(CAMPAIGN)
        assert campaign is not None
        assert campaign["system_id"] == "freeform"
        snapshot = resume_snapshot(conn, CAMPAIGN)
        assert snapshot["scene"]["scene_id"] == "scene-1"
        assert snapshot["scene"]["status"] == "open"
        assert snapshot["in_world_date"] == "Day 3, dusk"
        assert [item["entity_id"] for item in snapshot["present_entities"]] == ["pc-ada"]
    finally:
        conn.close()


def test_setup_prints_a_reproducible_launch_sequence(
    database: Path, manifest_file: Path
) -> None:
    result = _run(
        "campaign", "setup", "--from", str(manifest_file), "--yes", database=database
    )
    assert result.returncode == 0, result.stderr
    assert f"gamemaster campaign start {CAMPAIGN}" in result.stdout
    assert f"gamemaster campaign validate {CAMPAIGN}" in result.stdout


def test_the_shipped_example_manifest_loads() -> None:
    """The documented example must actually parse."""

    from tabletop.campaign.setup import load_setup_manifest

    example = REPO_ROOT / "examples" / "campaigns" / "setup" / "campaign.setup.yaml"
    manifest = load_setup_manifest(example)
    assert manifest.campaign_id == "black-company"
    assert len(manifest.participants) == 3
    assert manifest.starting_scene is not None
    # It is loadable and dry-runnable, and names nothing executable.
    assert all(item.path.suffix == ".md" for item in manifest.content)
