"""CLI tests for session start/inspect/end."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tabletop.storage.sqlite import connect, migrate

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(database: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["TABLETOP_DATABASE_PATH"] = str(database)
    env.pop("TABLETOP_CAMPAIGN", None)
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


def _prepare(database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
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
        ).returncode
        == 0
    )
    assert _run("campaign", "select", "night", database=database).returncode == 0


def test_session_start_inspect_end(tmp_path: Path) -> None:
    database = tmp_path / "state" / "campaign.db"
    _prepare(database)
    start = _run(
        "campaign", "session", "start", "--session-id", "s1", database=database
    )
    assert start.returncode == 0, start.stderr
    second = _run(
        "campaign", "session", "start", "--session-id", "s2", database=database
    )
    assert second.returncode != 0
    inspect = _run("campaign", "session", "inspect", database=database)
    assert inspect.returncode == 0
    assert "s1" in inspect.stdout
    end = _run("campaign", "session", "end", database=database)
    assert end.returncode == 0, end.stderr

    conn = connect(database)
    try:
        migrate(conn)
        row = conn.execute(
            "SELECT ended_at FROM sessions WHERE session_id = ?", ("s1",)
        ).fetchone()
        assert row is not None
        assert row["ended_at"] is not None
    finally:
        conn.close()
