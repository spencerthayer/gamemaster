"""Operator CLI entry point."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_module(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    if env is None:
        merged = os.environ.copy()
    else:
        merged = env
    return subprocess.run(
        [sys.executable, "-m", "tabletop.cli", *args],
        cwd=REPO_ROOT,
        env=merged,
        capture_output=True,
        text=True,
        check=False,
    )


def test_help_exits_zero_and_names_campaign_and_system() -> None:
    result = _run_module("--help")
    assert result.returncode == 0
    combined = f"{result.stdout}\n{result.stderr}".lower()
    assert "campaign" in combined
    assert "system" in combined


def test_unknown_command_exits_nonzero() -> None:
    result = _run_module("definitely-not-a-command")
    assert result.returncode != 0


def test_scripts_gamemaster_delegates_to_module() -> None:
    script = REPO_ROOT / "scripts" / "gamemaster"
    result = subprocess.run(
        [str(script), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    combined = f"{result.stdout}\n{result.stderr}".lower()
    assert "campaign" in combined
    assert "system" in combined


def test_help_works_without_database_path() -> None:
    env = os.environ.copy()
    env.pop("TABLETOP_DATABASE_PATH", None)
    result = _run_module("--help", env=env)
    assert result.returncode == 0
