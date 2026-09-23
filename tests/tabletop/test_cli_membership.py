"""CLI tests for membership commands."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(database: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["TABLETOP_DATABASE_PATH"] = str(database)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("TABLETOP_CAMPAIGN", None)
    return env


def _run(*args: str, database: Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tabletop.cli", *args],
        cwd=cwd or REPO_ROOT,
        env=_env(database),
        capture_output=True,
        text=True,
        check=False,
    )


def test_membership_cli_flow(tmp_path: Path) -> None:
    database = tmp_path / "campaign.db"
    cwd = tmp_path
    assert (
        _run(
            "campaign",
            "create",
            "--id",
            "night",
            "--name",
            "Night",
            "--system",
            "freeform",
            database=database,
            cwd=cwd,
        ).returncode
        == 0
    )
    assert _run("campaign", "select", "night", database=database, cwd=cwd).returncode == 0
    assert (
        _run(
            "campaign",
            "entity",
            "create",
            "--id",
            "e1",
            "--kind",
            "character",
            "--name",
            "Hero",
            database=database,
            cwd=cwd,
        ).returncode
        == 0
    )
    assert (
        _run(
            "campaign",
            "participant",
            "add",
            "--id",
            "p1",
            "--name",
            "Ada",
            "--role",
            "player",
            database=database,
            cwd=cwd,
        ).returncode
        == 0
    )
    assert (
        _run(
            "campaign",
            "participant",
            "bind",
            "--participant",
            "p1",
            "--channel",
            "telegram",
            "--external-id",
            "42",
            database=database,
            cwd=cwd,
        ).returncode
        == 0
    )
    dup = _run(
        "campaign",
        "participant",
        "bind",
        "--participant",
        "p1",
        "--channel",
        "telegram",
        "--external-id",
        "43",
        database=database,
        cwd=cwd,
    )
    assert dup.returncode != 0
    grant = _run(
        "campaign",
        "character",
        "grant",
        "--participant",
        "p1",
        "--entity",
        "e1",
        "--control",
        "owner",
        database=database,
        cwd=cwd,
    )
    assert grant.returncode == 0, grant.stderr
    inspect = _run("campaign", "inspect", "night", database=database, cwd=cwd)
    assert inspect.returncode == 0
    assert "p1" in inspect.stdout
    assert "telegram" in inspect.stdout
    assert "42" in inspect.stdout
    assert "e1" in inspect.stdout

    unbind = _run(
        "campaign",
        "participant",
        "unbind",
        "--participant",
        "p1",
        "--channel",
        "telegram",
        database=database,
        cwd=cwd,
    )
    assert unbind.returncode == 0
    inspect2 = _run("campaign", "inspect", "night", database=database, cwd=cwd)
    assert "principals: 0" in inspect2.stdout
    assert (
        _run(
            "campaign",
            "participant",
            "bind",
            "--participant",
            "p1",
            "--channel",
            "telegram",
            "--external-id",
            "42",
            database=database,
            cwd=cwd,
        ).returncode
        == 0
    )

    bad_slug = _run(
        "campaign",
        "participant",
        "add",
        "--id",
        "P 1",
        "--name",
        "X",
        "--role",
        "player",
        database=database,
        cwd=cwd,
    )
    assert bad_slug.returncode != 0
    assert (
        _run(
            "campaign",
            "participant",
            "add",
            "--id",
            "gm1",
            "--name",
            "GM",
            "--role",
            "gm",
            database=database,
            cwd=cwd,
        ).returncode
        == 0
    )
    second_gm = _run(
        "campaign",
        "participant",
        "add",
        "--id",
        "p2",
        "--name",
        "Other",
        "--role",
        "gm",
        database=database,
        cwd=cwd,
    )
    assert second_gm.returncode != 0
