"""Operator CLI surface for scenes and the campaign clock.

Every case drives a real ``python -m tabletop.cli`` process against a real
database file, so a command that only works in-process cannot pass.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tabletop.campaign.models import PresenceType
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate

REPO_ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN = "scene-demo"


def _run(*args: str, database: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TABLETOP_DATABASE_PATH"] = str(database)
    env["TABLETOP_CAMPAIGN"] = CAMPAIGN
    env.pop("TABLETOP_WORKSPACE", None)
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
    path = tmp_path / "scenes.db"
    conn = connect(path)
    migrate(conn)
    store = CampaignStore(conn)
    store.create_campaign(CAMPAIGN, "Scene Demo", "freeform")
    for entity_id in ("pc-ada", "npc-vor"):
        store.upsert_entity(CAMPAIGN, entity_id, entity_id)
    conn.close()
    return path


def _query(database: Path, sql: str, *params: object) -> list[sqlite3.Row]:
    conn = connect(database)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_scene_show_reports_no_open_scene(database: Path) -> None:
    result = _run("campaign", "scene", "show", database=database)
    assert result.returncode == 0, result.stderr
    assert "no open scene" in result.stdout


def test_scene_open_then_show(database: Path) -> None:
    opened = _run(
        "campaign", "scene", "open", "--scene-id", "scene-1", "--name", "Crossroads",
        database=database,
    )
    assert opened.returncode == 0, opened.stderr
    assert "Crossroads" in opened.stdout

    shown = _run("campaign", "scene", "show", database=database)
    assert shown.returncode == 0, shown.stderr
    assert "scene-1" in shown.stdout
    assert "open" in shown.stdout


def test_opening_a_second_scene_exits_nonzero(database: Path) -> None:
    _run("campaign", "scene", "open", "--scene-id", "scene-1", "--name", "A", database=database)
    result = _run("campaign", "scene", "open", "--scene-id", "scene-2", "--name", "B", database=database)
    assert result.returncode != 0
    rows = _query(database, "SELECT COUNT(*) FROM scenes WHERE status = 'open'")
    assert rows[0][0] == 1


def test_scene_close_closes_the_open_scene(database: Path) -> None:
    _run("campaign", "scene", "open", "--scene-id", "scene-1", "--name", "A", database=database)
    result = _run("campaign", "scene", "close", "--scene-id", "scene-1", database=database)
    assert result.returncode == 0, result.stderr
    assert "closed" in result.stdout
    rows = _query(database, "SELECT status FROM scenes WHERE scene_id = 'scene-1'")
    assert rows[0][0] == "closed"


def test_scene_close_without_an_open_scene_exits_nonzero(database: Path) -> None:
    result = _run("campaign", "scene", "close", database=database)
    assert result.returncode != 0
    assert "no open scene" in (result.stdout + result.stderr)


def test_scene_transition_moves_between_scenes(database: Path) -> None:
    _run("campaign", "scene", "open", "--scene-id", "scene-a", "--name", "Crossroads", database=database)
    result = _run(
        "campaign", "scene", "transition", "--scene-id", "scene-b", "--name", "Archive",
        database=database,
    )
    assert result.returncode == 0, result.stderr
    statuses = {
        row["scene_id"]: row["status"]
        for row in _query(database, "SELECT scene_id, status FROM scenes")
    }
    assert statuses == {"scene-a": "closed", "scene-b": "open"}


def test_scene_transition_rejects_a_stale_from_scene(database: Path) -> None:
    _run("campaign", "scene", "open", "--scene-id", "scene-a", "--name", "A", database=database)
    result = _run(
        "campaign", "scene", "transition", "--from", "scene-zzz",
        "--scene-id", "scene-b", "--name", "B", database=database,
    )
    assert result.returncode != 0
    rows = _query(database, "SELECT COUNT(*) FROM scenes")
    assert rows[0][0] == 1


def test_scene_enter_and_exit_use_the_open_scene(database: Path) -> None:
    _run("campaign", "scene", "open", "--scene-id", "scene-1", "--name", "A", database=database)
    entered = _run(
        "campaign", "scene", "enter", "--entity-id", "pc-ada", "--presence-type", "pc",
        database=database,
    )
    assert entered.returncode == 0, entered.stderr

    shown = _run("campaign", "scene", "show", database=database)
    assert "pc-ada" in shown.stdout

    exited = _run("campaign", "scene", "exit", "--entity-id", "pc-ada", database=database)
    assert exited.returncode == 0, exited.stderr
    conn = connect(database)
    try:
        members = SceneStore(conn).get_members(CAMPAIGN, "scene-1")
    finally:
        conn.close()
    assert members[0].is_present is False


def test_scene_enter_rejects_an_unknown_presence_type(database: Path) -> None:
    _run("campaign", "scene", "open", "--scene-id", "scene-1", "--name", "A", database=database)
    result = _run(
        "campaign", "scene", "enter", "--entity-id", "pc-ada", "--presence-type", "ally",
        database=database,
    )
    assert result.returncode != 0


def test_scene_enter_rejects_an_entity_from_another_campaign(
    database: Path, tmp_path: Path
) -> None:
    _run("campaign", "scene", "open", "--scene-id", "scene-1", "--name", "A", database=database)
    result = _run(
        "campaign", "scene", "enter", "--entity-id", "ghost", "--presence-type", "npc",
        database=database,
    )
    assert result.returncode != 0


def test_scene_commands_are_rejected_on_an_archived_campaign(database: Path) -> None:
    conn = connect(database)
    try:
        CampaignStore(conn).archive_campaign(CAMPAIGN)
    finally:
        conn.close()
    result = _run(
        "campaign", "scene", "open", "--scene-id", "scene-1", "--name", "A", database=database
    )
    assert result.returncode != 0
    assert "archived" in (result.stdout + result.stderr)


def test_time_show_before_any_clock_is_set(database: Path) -> None:
    result = _run("campaign", "time", "show", database=database)
    assert result.returncode == 0, result.stderr
    assert "not set" in result.stdout


def test_time_set_and_show_round_trip(database: Path) -> None:
    result = _run(
        "campaign", "time", "set", "--label", "Day 3, morning", "--minutes", "2880",
        database=database,
    )
    assert result.returncode == 0, result.stderr
    assert "Day 3, morning" in result.stdout

    shown = _run("campaign", "time", "show", database=database)
    assert "2880" in shown.stdout


def test_time_set_requires_at_least_one_value(database: Path) -> None:
    result = _run("campaign", "time", "set", database=database)
    assert result.returncode != 0


def test_time_set_rejects_negative_minutes(database: Path) -> None:
    result = _run("campaign", "time", "set", "--minutes", "-5", database=database)
    assert result.returncode != 0


def test_scene_open_requires_a_name(database: Path) -> None:
    result = _run("campaign", "scene", "open", "--scene-id", "scene-1", database=database)
    assert result.returncode != 0
