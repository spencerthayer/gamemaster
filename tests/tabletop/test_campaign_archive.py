"""Campaign archive and restore (ADR 0010)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tabletop.api.workspace import Workspace
from tabletop.campaign.event_store import EventStore, EventType
from tabletop.campaign.store import CampaignStore
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate, transaction

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


def test_archive_restore_and_list_filter(tmp_path: Path) -> None:
    database = tmp_path / "state" / "campaign.db"
    _prepare(database)
    archive = _run("campaign", "archive", "--id", "night", database=database)
    assert archive.returncode == 0, archive.stderr + archive.stdout
    listed = _run("campaign", "list", database=database)
    assert "night" not in listed.stdout
    listed_all = _run("campaign", "list", "--all", database=database)
    assert "night" in listed_all.stdout
    assert "archived" in listed_all.stdout

    conn = connect(database)
    try:
        migrate(conn)
        campaign = CampaignStore(conn).get_campaign("night")
        assert campaign is not None
        assert campaign["archived_at"]
        events = [e.event_type for e in EventStore(conn).read("night")]
        assert EventType.CAMPAIGN_ARCHIVED.value in events
    finally:
        conn.close()

    second = _run("campaign", "archive", "--id", "night", database=database)
    assert second.returncode != 0

    restore = _run("campaign", "restore", "--id", "night", database=database)
    assert restore.returncode == 0, restore.stderr
    start = _run(
        "campaign", "session", "start", "--session-id", "s1", database=database
    )
    assert start.returncode == 0, start.stderr


def test_archive_with_open_session_fails(tmp_path: Path) -> None:
    database = tmp_path / "state" / "campaign.db"
    _prepare(database)
    assert (
        _run(
            "campaign", "session", "start", "--session-id", "s1", database=database
        ).returncode
        == 0
    )
    archive = _run("campaign", "archive", "--id", "night", database=database)
    assert archive.returncode != 0
    assert "open_session" in (archive.stderr + archive.stdout)


def test_archived_blocks_start_session_and_resolve(tmp_path: Path) -> None:
    database = tmp_path / "state" / "campaign.db"
    _prepare(database)
    assert _run("campaign", "archive", "--id", "night", database=database).returncode == 0
    start = _run(
        "campaign", "session", "start", "--session-id", "s1", database=database
    )
    assert start.returncode != 0

    conn = connect(database)
    runtime = TabletopRuntime(
        REPO_ROOT,
        workspace=Workspace.CAMPAIGN,
        plugin_roots=[],
        active_campaign="night",
        connection=conn,
    )
    try:
        result = runtime.resolve_action('{"actor":"ada","action":"look"}')
        assert result["ok"] is False
        assert result["error"]["code"] == "campaign_archived"
    finally:
        runtime.shutdown()
        conn.close()


def test_archive_session_race_leaves_one_winner(tmp_path: Path) -> None:
    database = tmp_path / "state" / "campaign.db"
    _prepare(database)
    conn_a = connect(database)
    conn_b = connect(database)
    migrate(conn_a)
    migrate(conn_b)
    store_a = CampaignStore(conn_a)
    winners = []
    try:
        with transaction(conn_a):
            store_a.archive_campaign("night", archived_at="2026-09-22T00:00:00Z")
            winners.append("archive")
        try:
            with transaction(conn_b):
                conn_b.execute(
                    "INSERT INTO sessions "
                    "(session_id, campaign_id, started_at, participants, "
                    "important_facts, open_threads, checklist_step) "
                    "VALUES (?, ?, ?, '[]', '[]', '[]', 0)",
                    ("s1", "night", "2026-09-22T00:00:01Z"),
                )
                winners.append("session")
        except Exception:
            pass
    finally:
        conn_a.close()
        conn_b.close()
    assert winners[0] == "archive"
    conn = connect(database)
    try:
        campaign = CampaignStore(conn).get_campaign("night")
        assert campaign is not None
        assert campaign["archived_at"] is not None
    finally:
        conn.close()
