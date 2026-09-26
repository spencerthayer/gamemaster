"""`campaign validate` has a stable JSON shape and stable exit classes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate

REPO_ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN = "validate-demo"


def _run(*args: str, database: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TABLETOP_DATABASE_PATH"] = str(database)
    env["TABLETOP_CAMPAIGN"] = CAMPAIGN
    return subprocess.run(
        [sys.executable, "-m", "tabletop.cli", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _make(database: Path, *, with_gm: bool = True, with_scene: bool = True) -> Path:
    conn = connect(database)
    migrate(conn)
    store = CampaignStore(conn)
    store.create_campaign(CAMPAIGN, "Validate Demo", "freeform")
    store.upsert_entity(CAMPAIGN, "pc-ada", "Ada")
    if with_gm:
        MembershipStore(conn).add_participant(CAMPAIGN, "gm-1", "The GM", "gm")
    if with_scene:
        SceneStore(conn).open_scene(CAMPAIGN, "scene-1", "Crossroads")
    conn.close()
    return database


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return _make(tmp_path / "validate.db")


# -- json shape -------------------------------------------------------------


def test_json_output_contains_ready_and_checks(database: Path) -> None:
    result = _run("campaign", "validate", "--format", "json", database=database)
    payload = json.loads(result.stdout)
    assert payload["campaign_id"] == CAMPAIGN
    assert "ready" in payload
    assert isinstance(payload["checks"], list)
    assert all("id" in check and "status" in check for check in payload["checks"])


def test_json_output_is_stable_across_runs(database: Path) -> None:
    first = json.loads(
        _run("campaign", "validate", "--format", "json", database=database).stdout
    )
    second = json.loads(
        _run("campaign", "validate", "--format", "json", database=database).stdout
    )
    assert first == second


def test_text_output_names_the_failed_check(tmp_path: Path) -> None:
    database = _make(tmp_path / "no-gm.db", with_gm=False)
    result = _run("campaign", "validate", database=database)
    assert "participant.gm.count" in result.stdout
    assert "[fail]" in result.stdout


# -- exit classes -----------------------------------------------------------


def test_a_ready_campaign_exits_zero(database: Path) -> None:
    result = _run("campaign", "validate", database=database)
    assert result.returncode == 0, result.stdout


def test_a_validation_failure_exits_one(tmp_path: Path) -> None:
    database = _make(tmp_path / "no-gm.db", with_gm=False)
    result = _run("campaign", "validate", database=database)
    assert result.returncode == 1


def test_a_live_failure_exits_two(database: Path) -> None:
    """A broken environment is a runtime class, not a validation class."""
    result = _run("campaign", "validate", "--live", database=database)
    # Docker is unavailable in CI, so at least one live probe fails.
    if result.returncode != 0:
        assert result.returncode == 2, result.stdout


def test_an_invalid_invocation_exits_three(database: Path) -> None:
    result = _run("campaign", "validate", "--not-a-flag", database=database)
    assert result.returncode == 3


def test_an_unknown_command_exits_three(database: Path) -> None:
    result = _run("campaign", "not-a-subcommand", database=database)
    assert result.returncode == 3


def test_help_still_exits_zero(database: Path) -> None:
    result = _run("--help", database=database)
    assert result.returncode == 0


def test_a_missing_campaign_selection_exits_three(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["TABLETOP_DATABASE_PATH"] = str(tmp_path / "none.db")
    env.pop("TABLETOP_CAMPAIGN", None)
    result = subprocess.run(
        [sys.executable, "-m", "tabletop.cli", "campaign", "validate"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 3


# -- live and channel probe flags -------------------------------------------


def test_live_adds_live_checks(database: Path) -> None:
    result = _run("campaign", "validate", "--live", "--format", "json", database=database)
    payload = json.loads(result.stdout)
    assert payload["live"] is True
    assert any(check["id"].startswith("live.") for check in payload["checks"])


def test_no_live_flag_means_no_live_checks(database: Path) -> None:
    result = _run("campaign", "validate", "--format", "json", database=database)
    payload = json.loads(result.stdout)
    assert payload["live"] is False
    assert not any(check["id"].startswith("live.") for check in payload["checks"])


def test_the_channel_probe_is_separate_from_live(database: Path) -> None:
    live = json.loads(
        _run("campaign", "validate", "--live", "--format", "json", database=database).stdout
    )
    assert not any(check["id"] == "channel.probe.delivery" for check in live["checks"])

    probed = json.loads(
        _run(
            "campaign", "validate", "--channel-probe", "--format", "json",
            database=database,
        ).stdout
    )
    assert any(check["id"] == "channel.probe.delivery" for check in probed["checks"])


def test_validation_json_never_contains_a_secret(database: Path) -> None:
    env = os.environ.copy()
    env["TABLETOP_DATABASE_PATH"] = str(database)
    env["TABLETOP_CAMPAIGN"] = CAMPAIGN
    env["OMEGA_MODEL_API_KEY"] = "super-secret-value"
    result = subprocess.run(
        [sys.executable, "-m", "tabletop.cli", "campaign", "validate", "--live", "--format", "json"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "super-secret-value" not in result.stdout
    assert "super-secret-value" not in result.stderr
