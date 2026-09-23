"""CLI tests for campaign list and inspect."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(database: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["TABLETOP_DATABASE_PATH"] = str(database)
    return env


def _run(*args: str, database: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tabletop.cli", *args],
        cwd=REPO_ROOT,
        env=_env(database),
        capture_output=True,
        text=True,
        check=False,
    )


def _create(database: Path, campaign_id: str, name: str) -> None:
    result = _run(
        "campaign",
        "create",
        "--id",
        campaign_id,
        "--name",
        name,
        "--system",
        "freeform",
        database=database,
    )
    assert result.returncode == 0, result.stderr


def test_list_contains_created_ids(tmp_path: Path) -> None:
    database = tmp_path / "campaign.db"
    _create(database, "alpha", "Alpha")
    _create(database, "beta", "Beta")
    result = _run("campaign", "list", database=database)
    assert result.returncode == 0
    assert "alpha" in result.stdout
    assert "beta" in result.stdout


def test_inspect_missing_exits_nonzero(tmp_path: Path) -> None:
    database = tmp_path / "campaign.db"
    _create(database, "alpha", "Alpha")
    result = _run("campaign", "inspect", "missing", database=database)
    assert result.returncode != 0


def test_inspect_includes_system_and_name(tmp_path: Path) -> None:
    database = tmp_path / "campaign.db"
    _create(database, "alpha", "Alpha Quest")
    result = _run("campaign", "inspect", "alpha", database=database)
    assert result.returncode == 0
    assert "alpha" in result.stdout
    assert "Alpha Quest" in result.stdout
    assert "freeform" in result.stdout
