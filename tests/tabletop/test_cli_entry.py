"""Operator CLI entry point."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
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


# -- stale campaign selection ----------------------------------------------


def test_a_stale_selection_file_falls_back_to_the_only_campaign(
    tmp_path: Path,
) -> None:
    """The selection file is shared by every database in its directory.

    It can therefore name a campaign that does not exist in this one.
    Trusting it blindly made every command fail with a confusing
    "campaign not found" instead of doing the obvious thing.
    """
    from tabletop.cli.runtime_factory import resolve_campaign_id
    from tabletop.campaign.selection import write_active_campaign_file
    from tabletop.campaign.store import CampaignStore
    from tabletop.storage.sqlite import connect, migrate

    database = tmp_path / "campaigns.db"
    conn = connect(database)
    migrate(conn)
    CampaignStore(conn).create_campaign("only-one", "Only", "freeform")
    conn.close()

    write_active_campaign_file(database, "some-other-campaign")
    env = {"TABLETOP_DATABASE_PATH": str(database)}
    assert resolve_campaign_id(environ=env) == "only-one"


def test_a_valid_selection_file_is_honored(tmp_path: Path) -> None:
    from tabletop.cli.runtime_factory import resolve_campaign_id
    from tabletop.campaign.selection import write_active_campaign_file
    from tabletop.campaign.store import CampaignStore
    from tabletop.storage.sqlite import connect, migrate

    database = tmp_path / "campaigns.db"
    conn = connect(database)
    migrate(conn)
    store = CampaignStore(conn)
    store.create_campaign("alpha", "A", "freeform")
    store.create_campaign("beta", "B", "freeform")
    conn.close()

    write_active_campaign_file(database, "beta")
    env = {"TABLETOP_DATABASE_PATH": str(database)}
    assert resolve_campaign_id(environ=env) == "beta"


def test_an_explicit_missing_campaign_is_an_error(tmp_path: Path) -> None:
    """An explicit request for a campaign that is absent must still fail."""
    from tabletop.cli.runtime_factory import resolve_campaign_id
    from tabletop.campaign.store import CampaignStore
    from tabletop.storage.sqlite import connect, migrate

    database = tmp_path / "campaigns.db"
    conn = connect(database)
    migrate(conn)
    CampaignStore(conn).create_campaign("alpha", "A", "freeform")
    conn.close()

    env = {"TABLETOP_DATABASE_PATH": str(database), "TABLETOP_CAMPAIGN": "ghost"}
    with pytest.raises(SystemExit, match="not found"):
        resolve_campaign_id(environ=env)


def test_an_archived_campaign_is_selected_so_the_command_says_archived(
    tmp_path: Path,
) -> None:
    """Resolving an archived campaign must not report it as missing.

    Auto-selection only ever picks an active campaign, but an explicit
    selection of an archived one is honored so the caller gets the specific
    "archived" refusal rather than a vague "not found".
    """
    from tabletop.cli.runtime_factory import resolve_campaign_id
    from tabletop.campaign.store import CampaignStore
    from tabletop.storage.sqlite import connect, migrate

    database = tmp_path / "campaigns.db"
    conn = connect(database)
    migrate(conn)
    store = CampaignStore(conn)
    store.create_campaign("live", "L", "freeform")
    store.create_campaign("old", "O", "freeform")
    store.archive_campaign("old")
    conn.close()

    env = {"TABLETOP_DATABASE_PATH": str(database), "TABLETOP_CAMPAIGN": "old"}
    assert resolve_campaign_id(environ=env) == "old"


def test_auto_selection_skips_an_archived_campaign(tmp_path: Path) -> None:
    from tabletop.cli.runtime_factory import resolve_campaign_id
    from tabletop.campaign.store import CampaignStore
    from tabletop.storage.sqlite import connect, migrate

    database = tmp_path / "campaigns.db"
    conn = connect(database)
    migrate(conn)
    store = CampaignStore(conn)
    store.create_campaign("live", "L", "freeform")
    store.create_campaign("old", "O", "freeform")
    store.archive_campaign("old")
    conn.close()

    env = {"TABLETOP_DATABASE_PATH": str(database)}
    assert resolve_campaign_id(environ=env) == "live"
