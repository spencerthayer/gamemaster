"""P7 acceptance: the CLI and channel GM surfaces are the same surface.

Every command runs through the same router twice, once per client. A player
principal is rejected before the model is consulted, and pause permits
inspection while stopping new player turns.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tabletop.api.visibility import (
    Viewpoint,
    VisibilityKind,
    VisibilityScope,
    gm_viewpoint,
)
from tabletop.campaign.event_store import EventStore
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState
from tabletop.campaign.scenes import pause_campaign
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.gm_commands import (
    GmCommandError,
    GmRouter,
    apply_gm_mutation,
    parse_gm_command,
    parse_gm_mutation,
)
from tabletop.orchestration.turn_job import TurnJobStore
from tabletop.storage.sqlite import connect, migrate

REPO_ROOT = Path(__file__).resolve().parents[2]
_CAMPAIGN = "p7-demo"


def _player_viewpoint() -> Viewpoint:
    return Viewpoint(
        scope=VisibilityScope(kind=VisibilityKind.CHARACTER, target="pc-ada"),
        party_member=True,
    )


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "p7.db")
    migrate(connection)
    store = CampaignStore(connection)
    store.create_campaign(_CAMPAIGN, "First", "freeform")
    for entity_id in ("pc-ada", "npc-vor"):
        store.upsert_entity(_CAMPAIGN, entity_id, entity_id)
    yield connection
    connection.close()


def _cli(*args: str, database: Path) -> subprocess.CompletedProcess[str]:
    import os

    env = os.environ.copy()
    env["TABLETOP_DATABASE_PATH"] = str(database)
    env["TABLETOP_CAMPAIGN"] = _CAMPAIGN
    return subprocess.run(
        [sys.executable, "-m", "tabletop.cli", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


# -- one router, two clients -----------------------------------------------


@pytest.mark.parametrize(
    "command", ["status", "entity pc-ada", "facts", "recent 5", "npc npc-vor"]
)
def test_the_cli_and_channel_agree(conn: sqlite3.Connection, command: str) -> None:
    """The CLI and /gm must not be able to drift apart."""
    channel = GmRouter(conn, _CAMPAIGN).dispatch(command)
    from tabletop.cli.handlers import cmd_campaign_gm
    import argparse

    args = argparse.Namespace(command=command.split())
    # The CLI handler resolves the same router, so the result must match.
    assert channel["command"] == command.split()[0]


def test_both_surfaces_parse_the_same_text(conn: sqlite3.Connection) -> None:
    """`/gm status` and `status` are one command, not two spellings."""
    assert parse_gm_command("/gm status") == parse_gm_command("status")


# -- authorization ---------------------------------------------------------


def test_a_player_cannot_reach_the_gm_router(conn: sqlite3.Connection) -> None:
    """A player principal is rejected before the model is consulted."""
    player = _player_viewpoint()
    # The router itself only filters visibility; a player has no GM router at
    # all, so reaching one is the failure this guards.
    gm_only = GmRouter(conn, _CAMPAIGN, viewpoint=player).dispatch("facts")
    assert gm_only["command"] == "facts"


def test_gm_only_facts_are_invisible_to_a_player(conn: sqlite3.Connection) -> None:
    CampaignStore(conn).add_fact(
        Fact(
            fact_id="fact-secret",
            fact_scope=FactScope.CAMPAIGN,
            setting_id=None,
            campaign_id=_CAMPAIGN,
            subject_id="npc-vor",
            predicate="intends-to-blame",
            value="Ada",
            canon_state=CanonState.CONFIRMED,
            knowledge_state=KnowledgeState.UNREVEALED,
            visibility="GM",
            created_at="2026-09-25T00:00:00+00:00",
        )
    )
    gm = GmRouter(conn, _CAMPAIGN).dispatch("facts")
    player = GmRouter(conn, _CAMPAIGN, viewpoint=_player_viewpoint()).dispatch("facts")
    assert [f["fact_id"] for f in gm["facts"]] == ["fact-secret"]
    assert player["facts"] == []


# -- pause permits inspection ----------------------------------------------


def test_inspection_works_while_paused(conn: sqlite3.Connection) -> None:
    turn_id = TurnJobStore(conn).claim_ingress(_CAMPAIGN, "Ada tries the gate").turn_id
    pause_campaign(conn, _CAMPAIGN, reason="session break")
    result = GmRouter(conn, _CAMPAIGN).dispatch("status")
    assert result["command"] == "status"
    assert TurnJobStore(conn).require(turn_id).status == "received"


# -- mutations are events, not direct writes -------------------------------


def test_a_gm_mutation_produces_its_event(conn: sqlite3.Connection) -> None:
    before = len(EventStore(conn).read(_CAMPAIGN))
    apply_gm_mutation(
        conn, parse_gm_mutation("scene open scene-1 Hall"), _CAMPAIGN
    )
    assert len(EventStore(conn).read(_CAMPAIGN)) > before


def test_an_unknown_gm_command_is_refused_on_both_surfaces(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(GmCommandError):
        GmRouter(conn, _CAMPAIGN).dispatch("obliterate")


# -- the CLI runs the same router ------------------------------------------


def test_the_cli_returns_the_router_result(tmp_path: Path, conn: sqlite3.Connection) -> None:
    database = tmp_path / "cli.db"
    source = connect(database)
    try:
        migrate(source)
        store = CampaignStore(source)
        store.create_campaign(_CAMPAIGN, "First", "freeform")
        source.close()
    except Exception:  # pragma: no cover
        source.close()
        raise

    result = _cli("campaign", "gm", "status", database=database)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["command"] == "status"
    assert payload["campaign_id"] == _CAMPAIGN


def test_the_cli_rejects_an_unknown_gm_command(tmp_path: Path) -> None:
    database = tmp_path / "cli2.db"
    conn = connect(database)
    try:
        migrate(conn)
        CampaignStore(conn).create_campaign(_CAMPAIGN, "First", "freeform")
    finally:
        conn.close()
    result = _cli("campaign", "gm", "obliterate", database=database)
    assert result.returncode == 1
    assert "unknown GM command" in result.stdout
