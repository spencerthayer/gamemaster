"""CLI tests for campaign create."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tabletop.campaign.store import CampaignStore
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


def test_create_freeform_campaign(tmp_path: Path) -> None:
    database = tmp_path / "campaign.db"
    result = _run(
        "campaign",
        "create",
        "--id",
        "night",
        "--name",
        "The Long Night",
        "--system",
        "freeform",
        database=database,
    )
    assert result.returncode == 0, result.stderr

    conn = connect(database)
    try:
        migrate(conn)
        campaign = CampaignStore(conn).get_campaign("night")
    finally:
        conn.close()

    assert campaign is not None
    assert campaign["campaign_id"] == "night"
    assert campaign["name"] == "The Long Night"
    assert campaign["system_id"] == "freeform"
    assert campaign["system_version"] == "0.1.0"


def test_duplicate_create_fails(tmp_path: Path) -> None:
    database = tmp_path / "campaign.db"
    first = _run(
        "campaign",
        "create",
        "--id",
        "night",
        "--name",
        "Night",
        "--system",
        "freeform",
        database=database,
    )
    assert first.returncode == 0
    second = _run(
        "campaign",
        "create",
        "--id",
        "night",
        "--name",
        "Other",
        "--system",
        "freeform",
        database=database,
    )
    assert second.returncode != 0

    conn = connect(database)
    try:
        assert len(CampaignStore(conn).list_campaigns()) == 1
    finally:
        conn.close()


def test_unknown_system_leaves_zero_rows(tmp_path: Path) -> None:
    database = tmp_path / "campaign.db"
    result = _run(
        "campaign",
        "create",
        "--id",
        "night",
        "--name",
        "Night",
        "--system",
        "not-a-plugin",
        database=database,
    )
    assert result.returncode != 0

    if database.exists():
        conn = connect(database)
        try:
            migrate(conn)
            assert CampaignStore(conn).list_campaigns() == []
        finally:
            conn.close()


def test_invalid_slug_rejected(tmp_path: Path) -> None:
    database = tmp_path / "campaign.db"
    for bad_id in ("Night Out", "UPPER", "a" * 65):
        result = _run(
            "campaign",
            "create",
            "--id",
            bad_id,
            "--name",
            "Night",
            "--system",
            "freeform",
            database=database,
        )
        assert result.returncode != 0, bad_id
