"""CLI and runtime tests for campaign select / resume."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tabletop.api.workspace import Workspace
from tabletop.campaign.store import CampaignStore
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(database: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["TABLETOP_DATABASE_PATH"] = str(database)
    env.pop("TABLETOP_CAMPAIGN", None)
    return env


def _run(*args: str, database: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tabletop.cli", *args],
        cwd=REPO_ROOT,
        env=env or _env(database),
        capture_output=True,
        text=True,
        check=False,
    )


def _create(database: Path, campaign_id: str) -> None:
    result = _run(
        "campaign",
        "create",
        "--id",
        campaign_id,
        "--name",
        campaign_id,
        "--system",
        "freeform",
        database=database,
    )
    assert result.returncode == 0, result.stderr


def test_select_rejects_archived_and_archive_clears_selection(tmp_path: Path) -> None:
    database = tmp_path / "state" / "campaign.db"
    database.parent.mkdir(parents=True)
    _create(database, "night")
    assert _run("campaign", "select", "night", database=database).returncode == 0
    archive = _run("campaign", "archive", "--id", "night", database=database)
    assert archive.returncode == 0, archive.stderr
    active = database.parent / "active-campaign"
    assert not active.exists()
    selected = _run("campaign", "select", "night", database=database)
    assert selected.returncode != 0
    assert "archived" in selected.stderr
    assert not active.exists()


def test_select_writes_active_campaign_file(tmp_path: Path) -> None:
    database = tmp_path / "state" / "campaign.db"
    database.parent.mkdir(parents=True)
    _create(database, "night")
    result = _run("campaign", "select", "night", database=database)
    assert result.returncode == 0, result.stderr
    active = database.parent / "active-campaign"
    assert active.read_text(encoding="utf-8") == "night\n"


def test_runtime_resumes_selected_campaign_without_env(tmp_path: Path) -> None:
    database = tmp_path / "state" / "campaign.db"
    database.parent.mkdir(parents=True)
    _create(database, "night")
    _create(database, "day")
    assert _run("campaign", "select", "night", database=database).returncode == 0

    runtime = TabletopRuntime.from_environment(
        REPO_ROOT,
        environ={
            "TABLETOP_WORKSPACE": "campaign",
            "TABLETOP_DATABASE_PATH": str(database),
        },
    )
    try:
        result = runtime.current_campaign()
        assert result["ok"] is True
        assert result["data"]["campaign"] == "night"
    finally:
        runtime.shutdown()
        if runtime._connection is not None:
            runtime._connection.close()


def test_env_overrides_active_campaign_file(tmp_path: Path) -> None:
    database = tmp_path / "state" / "campaign.db"
    database.parent.mkdir(parents=True)
    _create(database, "night")
    _create(database, "day")
    assert _run("campaign", "select", "night", database=database).returncode == 0

    runtime = TabletopRuntime.from_environment(
        REPO_ROOT,
        environ={
            "TABLETOP_WORKSPACE": "campaign",
            "TABLETOP_DATABASE_PATH": str(database),
            "TABLETOP_CAMPAIGN": "day",
        },
    )
    try:
        result = runtime.current_campaign()
        assert result["ok"] is True
        assert result["data"]["campaign"] == "day"
    finally:
        runtime.shutdown()
        if runtime._connection is not None:
            runtime._connection.close()


def test_selected_missing_row_is_campaign_not_found(tmp_path: Path) -> None:
    database = tmp_path / "state" / "campaign.db"
    database.parent.mkdir(parents=True)
    conn = connect(database)
    migrate(conn)
    conn.close()
    (database.parent / "active-campaign").write_text("ghost\n", encoding="utf-8")

    runtime = TabletopRuntime.from_environment(
        REPO_ROOT,
        environ={
            "TABLETOP_WORKSPACE": "campaign",
            "TABLETOP_DATABASE_PATH": str(database),
        },
    )
    try:
        result = runtime.current_campaign()
        assert result["ok"] is False
        assert result["error"]["code"] == "campaign_not_found"
    finally:
        runtime.shutdown()
        if runtime._connection is not None:
            runtime._connection.close()


def test_directory_without_row_is_not_current_campaign(tmp_path: Path) -> None:
    database = tmp_path / "state" / "campaign.db"
    database.parent.mkdir(parents=True)
    campaigns = tmp_path / "campaigns"
    (campaigns / "only-dir").mkdir(parents=True)
    conn = connect(database)
    migrate(conn)
    CampaignStore(conn).create_campaign("real", "Real", "freeform")
    conn.close()

    runtime = TabletopRuntime(
        tmp_path,
        workspace=Workspace.CAMPAIGN,
        campaign_roots=[campaigns],
        plugin_roots=[],
        connection=connect(database),
    )
    try:
        result = runtime.current_campaign()
        assert result["ok"] is True
        assert result["data"]["campaign"] == "real"
    finally:
        runtime.shutdown()
        if runtime._connection is not None:
            runtime._connection.close()
