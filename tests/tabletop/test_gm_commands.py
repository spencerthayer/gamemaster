"""One closed GM command language, shared by CLI and channel."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.api.visibility import (
    Viewpoint,
    VisibilityKind,
    VisibilityScope,
)
from tabletop.campaign.event_store import EventStore, EventType
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.gm_commands import (
    GmCommand,
    GmCommandError,
    GmRouter,
    parse_gm_command,
)
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "campaign-1"


def _player_viewpoint() -> Viewpoint:
    return Viewpoint(
        scope=VisibilityScope(kind=VisibilityKind.CHARACTER, target="pc-ada"),
        party_member=True,
    )


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "gm.db")
    migrate(connection)
    store = CampaignStore(connection)
    store.create_campaign(_CAMPAIGN, "First", "freeform")
    for entity_id in ("pc-ada", "npc-vor"):
        store.upsert_entity(_CAMPAIGN, entity_id, entity_id)
    SceneStore(connection).open_scene(_CAMPAIGN, "scene-1", "Crossroads")
    EventStore(connection).append(
        _CAMPAIGN,
        _event(EventType.SCENE_OPENED, {"scene_id": "scene-1", "name": "Crossroads"}),
        scene_id="scene-1",
    )
    yield connection
    connection.close()


def _event(event_type: EventType, payload: dict[str, object]):
    from tabletop.api.events import GameEvent

    return GameEvent(event_type=event_type.value, payload=payload)


# -- parsing ----------------------------------------------------------------


@pytest.mark.parametrize("command", list(GmCommand))
def test_every_command_parses(command: GmCommand) -> None:
    request = parse_gm_command(command.value)
    assert request.command is command


def test_the_slash_prefix_is_accepted() -> None:
    assert parse_gm_command("/gm status").command is GmCommand.STATUS


def test_an_unknown_command_is_rejected() -> None:
    with pytest.raises(GmCommandError, match="unknown GM command"):
        parse_gm_command("drop everything")


def test_an_empty_command_is_rejected() -> None:
    with pytest.raises(GmCommandError, match="empty"):
        parse_gm_command("   ")


def test_an_argument_with_sql_is_rejected() -> None:
    with pytest.raises(GmCommandError, match="plain identifier"):
        parse_gm_command("entity pc-ada; DROP TABLE campaigns")


def test_an_overlong_argument_is_rejected() -> None:
    with pytest.raises(GmCommandError, match="at most"):
        parse_gm_command("entity " + "a" * 500)


def test_a_required_argument_is_enforced_at_dispatch(conn: sqlite3.Connection) -> None:
    router = GmRouter(conn, _CAMPAIGN)
    with pytest.raises(GmCommandError, match="requires an entity id"):
        router.dispatch("entity")


# -- dispatch ---------------------------------------------------------------


def test_status_reports_the_campaign(conn: sqlite3.Connection) -> None:
    result = GmRouter(conn, _CAMPAIGN).dispatch("status")
    assert result["command"] == "status"
    assert result["campaign_id"] == _CAMPAIGN
    assert result["scene"]["scene_id"] == "scene-1"


def test_entity_returns_one_entity(conn: sqlite3.Connection) -> None:
    result = GmRouter(conn, _CAMPAIGN).dispatch("entity pc-ada")
    assert result["entity"]["entity_id"] == "pc-ada"


def test_entity_reports_an_unknown_id(conn: sqlite3.Connection) -> None:
    with pytest.raises(GmCommandError, match="not in campaign"):
        GmRouter(conn, _CAMPAIGN).dispatch("entity ghost")


def test_facts_respect_the_viewpoint(conn: sqlite3.Connection) -> None:
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
    gm_view = GmRouter(conn, _CAMPAIGN).dispatch("facts")
    assert [f["fact_id"] for f in gm_view["facts"]] == ["fact-secret"]

    player = GmRouter(conn, _CAMPAIGN, viewpoint=_player_viewpoint()).dispatch("facts")
    assert player["facts"] == []


def test_recent_returns_the_event_tail(conn: sqlite3.Connection) -> None:
    result = GmRouter(conn, _CAMPAIGN).dispatch("recent 5")
    assert result["command"] == "recent"
    assert result["events"][0]["event_type"] == "scene.opened"


def test_recent_rejects_a_non_numeric_count(conn: sqlite3.Connection) -> None:
    with pytest.raises(GmCommandError, match="takes a count"):
        GmRouter(conn, _CAMPAIGN).dispatch("recent many")


def test_recent_rejects_an_out_of_range_count(conn: sqlite3.Connection) -> None:
    with pytest.raises(GmCommandError, match="between 1 and 100"):
        GmRouter(conn, _CAMPAIGN).dispatch("recent 5000")


def test_npc_returns_the_named_entity(conn: sqlite3.Connection) -> None:
    result = GmRouter(conn, _CAMPAIGN).dispatch("npc npc-vor")
    assert result["entity_id"] == "npc-vor"


def test_relationships_for_an_entity_with_none(conn: sqlite3.Connection) -> None:
    result = GmRouter(conn, _CAMPAIGN).dispatch("relationships pc-ada")
    assert result["relationships"] == []


# -- boundaries -------------------------------------------------------------


def test_the_router_issues_no_sql_of_its_own() -> None:
    """Reads go through the stores, so a rule change is one edit, not many."""
    source = Path(
        "/Users/spenceratgraybox/Work/_Personal/gamemaster/tabletop/orchestration/gm_commands.py"
    ).read_text()
    body = source.split('"""', 2)[-1]
    for statement in ("SELECT ", "INSERT ", "UPDATE ", "DELETE "):
        assert statement not in body.upper().replace("SELECTED", "")


def test_dispatch_writes_nothing(conn: sqlite3.Connection) -> None:
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    for command in ("status", "entity pc-ada", "facts", "recent 3", "npc npc-vor"):
        GmRouter(conn, _CAMPAIGN).dispatch(command)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before


def test_a_slash_command_and_a_bare_command_agree(conn: sqlite3.Connection) -> None:
    router = GmRouter(conn, _CAMPAIGN)
    assert router.dispatch("/gm status") == router.dispatch("status")
