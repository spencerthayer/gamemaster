"""README quick-start smoke test using a temporary database."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = [sys.executable, "-m", "tabletop.cli"]


def _run(
    args: list[str], *, env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*CLI, *args],
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _assert_under_tmp(path: Path, tmp_path: Path) -> None:
    assert path.resolve().is_relative_to(tmp_path.resolve())


def test_readme_quick_start_smoke(tmp_path: Path) -> None:
    db_path = tmp_path / "smoke.sqlite3"
    _assert_under_tmp(db_path, tmp_path)
    env = os.environ.copy()
    env["TABLETOP_DATABASE_PATH"] = str(db_path)
    env.pop("TABLETOP_ACTIVE_CAMPAIGN", None)

    steps = [
        ["system", "list"],
        [
            "campaign",
            "create",
            "--id",
            "night",
            "--name",
            "Night Watch",
            "--system",
            "freeform",
        ],
        ["campaign", "select", "night"],
        [
            "campaign",
            "entity",
            "create",
            "--id",
            "ada",
            "--kind",
            "character",
            "--name",
            "Ada",
        ],
        [
            "campaign",
            "participant",
            "add",
            "--id",
            "gm1",
            "--name",
            "GM",
            "--role",
            "gm",
        ],
        ["campaign", "validate", "--id", "night"],
        ["campaign", "export", "night", "--out", str(tmp_path / "night-package")],
    ]
    for args in steps:
        result = _run(args, env=env, cwd=REPO_ROOT)
        assert result.returncode == 0, (
            f"command failed: {args}\nstdout={result.stdout}\nstderr={result.stderr}"
        )

    restore_db = tmp_path / "restore.sqlite3"
    _assert_under_tmp(restore_db, tmp_path)
    restore_env = env.copy()
    restore_env["TABLETOP_DATABASE_PATH"] = str(restore_db)
    restored = _run(
        ["campaign", "restore-package", str(tmp_path / "night-package")],
        env=restore_env,
        cwd=REPO_ROOT,
    )
    assert restored.returncode == 0, restored.stderr
    assert "night" in restored.stdout.lower() or restored.returncode == 0
    listed = _run(["campaign", "list"], env=restore_env, cwd=REPO_ROOT)
    assert listed.returncode == 0
    assert "night" in listed.stdout

    fork_db = tmp_path / "fork.sqlite3"
    _assert_under_tmp(fork_db, tmp_path)
    fork_env = env.copy()
    fork_env["TABLETOP_DATABASE_PATH"] = str(fork_db)
    fork = _run(
        ["campaign", "fork", str(tmp_path / "night-package"), "--id", "dawn"],
        env=fork_env,
        cwd=REPO_ROOT,
    )
    assert fork.returncode == 0, fork.stderr
    forked_list = _run(["campaign", "list"], env=fork_env, cwd=REPO_ROOT)
    assert "dawn" in forked_list.stdout
    assert "night" not in forked_list.stdout.split()

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    quick_start = readme.split("## Quick start", 1)[1].split("## ", 1)[0]
    fenced = re.findall(r"```bash\n(.*?)```", quick_start, flags=re.S)
    assert fenced, "quick start must include a bash fence"
    block = fenced[0]
    required = [
        "system list",
        "campaign create",
        "campaign entity create",
        "campaign participant add",
        "campaign validate",
    ]
    for needle in required:
        assert needle in block, f"missing {needle!r} in quick start"
    assert "campaign start night --gm" in block
    assert "configured channel" in block

    native_section = readme.split("## Import an existing campaign", 1)[1].split(
        "## ", 1
    )[0]
    assert "campaign export" in native_section
    assert "campaign restore-package" in native_section
    assert "campaign fork" in native_section
    native_fences = re.findall(r"```bash\n(.*?)```", native_section, flags=re.S)
    assert len(native_fences) >= 2
    assert "campaign import" not in native_fences[0]
    assert "campaign import" in native_fences[1]


def test_readme_sections_present() -> None:
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    headings = [
        "# Gamemaster",
        "## What you can do",
        "## Requirements",
        "## Quick start",
        "## Choose or install a game system",
        "## Create a campaign",
        "## Import an existing campaign",
        "## Add players and characters",
        "## Start the GM",
        "## Start the player surface",
        "## Connect a channel",
        "## Add sourcebooks and documents",
        "## Resume a campaign",
        "## Archive, restore, export",
        "## Write a game-system plugin",
        "## Write a content pack",
        "## Security model",
        "## Architecture",
        "## Current limitations",
    ]
    for heading in headings:
        assert heading in text, f"missing heading {heading}"
