"""P1 acceptance: scene A to scene B survives a process and database reopen.

Every step runs as a real ``python -m tabletop.cli`` process against a real
database file. The final resume runs in a fresh process with the database
reopened from disk, so nothing here can pass on in-process state.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tabletop.campaign.resume import resume_snapshot
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate

REPO_ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN = "restart-demo"


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
    path = tmp_path / "restart.db"
    conn = connect(path)
    migrate(conn)
    store = CampaignStore(conn)
    store.create_campaign(CAMPAIGN, "Restart Demo", "freeform")
    for entity_id in ("pc-ada", "npc-vor"):
        store.upsert_entity(CAMPAIGN, entity_id, entity_id)
    conn.close()
    return path


def _play_a_full_session(database: Path) -> None:
    """Create, start, play scene A, transition to B, and end the session."""

    created = _run(
        "campaign", "entity", "create", "--id", "hall", "--kind", "location",
        "--name", "Flooded Hall", database=database,
    )
    assert created.returncode == 0, created.stderr

    started = _run("campaign", "session", "start", "--session-id", "session-1", database=database)
    assert started.returncode == 0, started.stderr

    opened = _run(
        "campaign", "scene", "open", "--scene-id", "scene-a", "--name", "Flooded Hall",
        "--session-id", "session-1", "--location", "hall", database=database,
    )
    assert opened.returncode == 0, opened.stderr

    for entity_id, presence in (("pc-ada", "pc"), ("npc-vor", "npc")):
        entered = _run(
            "campaign", "scene", "enter", "--entity-id", entity_id,
            "--presence-type", presence, database=database,
        )
        assert entered.returncode == 0, entered.stderr

    clock = _run(
        "campaign", "time", "set", "--label", "Day 2, dusk", "--minutes", "2880",
        database=database,
    )
    assert clock.returncode == 0, clock.stderr

    moved = _run(
        "campaign", "scene", "transition", "--from", "scene-a",
        "--scene-id", "scene-b", "--name", "Sunken Archive", database=database,
    )
    assert moved.returncode == 0, moved.stderr

    entered_b = _run(
        "campaign", "scene", "enter", "--entity-id", "pc-ada", "--presence-type", "pc",
        database=database,
    )
    assert entered_b.returncode == 0, entered_b.stderr

    ended = _run("campaign", "session", "end", database=database)
    assert ended.returncode == 0, ended.stderr


def test_scene_b_survives_a_process_and_database_reopen(database: Path) -> None:
    _play_a_full_session(database)

    # Reopen from disk in a brand new process, with no inherited state.
    resumed = _run("campaign", "resume", database=database)
    assert resumed.returncode == 0, resumed.stderr

    conn = connect(database)
    try:
        snapshot = resume_snapshot(conn, CAMPAIGN)
    finally:
        conn.close()

    assert snapshot["scene"]["scene_id"] == "scene-b"
    assert snapshot["scene"]["name"] == "Sunken Archive"
    assert snapshot["scene"]["status"] == "closed"
    assert snapshot["in_world_date"] == "Day 2, dusk"
    assert snapshot["latest_session"]["session_id"] == "session-1"
    assert snapshot["latest_session"]["ended_at"] is not None


def test_scene_a_remains_inspectable_after_transition(database: Path) -> None:
    _play_a_full_session(database)
    conn = connect(database)
    try:
        rows = {
            row["scene_id"]: row
            for row in conn.execute(
                "SELECT scene_id, status, location_entity_id FROM scenes"
            ).fetchall()
        }
    finally:
        conn.close()
    assert rows["scene-a"]["status"] == "closed"
    assert rows["scene-a"]["location_entity_id"] == "hall"
    assert rows["scene-b"]["status"] == "closed"


def test_ending_the_session_left_no_open_scene(database: Path) -> None:
    _play_a_full_session(database)
    conn = connect(database)
    try:
        open_rows = conn.execute(
            "SELECT COUNT(*) FROM scenes WHERE campaign_id = ? AND status = 'open'",
            (CAMPAIGN,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert open_rows == 0


def test_presence_did_not_outlive_its_scene(database: Path) -> None:
    _play_a_full_session(database)
    conn = connect(database)
    try:
        members = conn.execute(
            "SELECT scene_id, entity_id, exited_at FROM scene_members ORDER BY scene_id"
        ).fetchall()
    finally:
        conn.close()
    assert {(row["scene_id"], row["entity_id"]) for row in members} == {
        ("scene-a", "pc-ada"),
        ("scene-a", "npc-vor"),
        ("scene-b", "pc-ada"),
    }
    # Everyone left scene A when it closed; Ada left scene B when it closed.
    assert all(row["exited_at"] is not None for row in members)


def test_world_state_written_in_scene_a_persists(database: Path) -> None:
    """Scene-scoped state written before the transition is not lost."""

    _play_a_full_session(database)
    conn = connect(database)
    try:
        conn.execute(
            "UPDATE scenes SET system_state = ? WHERE scene_id = 'scene-a'",
            (json.dumps({"flood_level": "waist"}),),
        )
    finally:
        conn.close()

    conn = connect(database)
    try:
        row = conn.execute(
            "SELECT system_state FROM scenes WHERE scene_id = 'scene-a'"
        ).fetchone()
    finally:
        conn.close()
    assert json.loads(row["system_state"])["flood_level"] == "waist"


def test_resume_ignores_unrelated_chat_history(database: Path) -> None:
    """Prose dropped in a memory file must not reach the authoritative snapshot."""

    _play_a_full_session(database)
    sentinel = "the secret passphrase is hunter2 and we are in the Dank Crypt"
    memory = REPO_ROOT / "memory"
    memory.mkdir(exist_ok=True)
    history = memory / "history.metta"
    history.write_text(sentinel, encoding="utf-8")
    try:
        conn = connect(database)
        try:
            rendered = json.dumps(resume_snapshot(conn, CAMPAIGN), default=str)
        finally:
            conn.close()
        assert sentinel not in rendered
        assert "hunter2" not in rendered
        assert "Dank Crypt" not in rendered
    finally:
        history.unlink(missing_ok=True)
        try:
            memory.rmdir()
        except OSError:
            pass
