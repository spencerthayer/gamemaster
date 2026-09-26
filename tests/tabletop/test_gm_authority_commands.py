"""Every GM mutation goes through a service and emits its event."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.campaign.event_store import EventStore
from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.gm_commands import (
    GmCommandError,
    GmMutatingCommand,
    apply_gm_mutation,
    parse_gm_mutation,
)
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "campaign-1"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "gm-auth.db")
    migrate(connection)
    store = CampaignStore(connection)
    store.create_campaign(_CAMPAIGN, "First", "freeform")
    for entity_id in ("pc-ada", "npc-vor", "loc-hall"):
        store.upsert_entity(_CAMPAIGN, entity_id, entity_id)
    yield connection
    connection.close()


def _types(conn: sqlite3.Connection) -> list[str]:
    return [event.event_type for event in EventStore(conn).read(_CAMPAIGN)]


def _run(conn: sqlite3.Connection, text: str) -> dict:
    return apply_gm_mutation(conn, parse_gm_mutation(text), _CAMPAIGN)


# -- parsing ----------------------------------------------------------------


def test_a_mutation_parses_its_verb_and_arguments() -> None:
    mutation = parse_gm_mutation("scene open scene-1 Hall")
    assert mutation.command is GmMutatingCommand.SCENE_OPEN
    assert mutation.arguments == ("scene-1", "Hall")


def test_a_slash_prefix_is_accepted() -> None:
    assert parse_gm_mutation("/gm scene close scene-1").command is (
        GmMutatingCommand.SCENE_CLOSE
    )


def test_an_unknown_mutation_is_rejected() -> None:
    with pytest.raises(GmCommandError, match="unknown GM command"):
        parse_gm_mutation("scene obliterate scene-1")


def test_wrong_argument_count_is_rejected() -> None:
    with pytest.raises(GmCommandError, match="takes 2 arguments"):
        parse_gm_mutation("scene open scene-1")


def test_a_non_identifier_argument_is_rejected() -> None:
    # A semicolon and spaces are what make this not a plain identifier.
    with pytest.raises(GmCommandError):
        parse_gm_mutation("scene open scene-1 Hall; DROP TABLE scenes")


# -- scene controls ---------------------------------------------------------


def test_scene_open_writes_the_scene_and_its_event(
    conn: sqlite3.Connection,
) -> None:
    result = _run(conn, "scene open scene-1 Hall")
    assert result["scene_id"] == "scene-1"
    assert SceneStore(conn).get_scene(_CAMPAIGN, "scene-1").status.value == "open"
    assert "scene.opened" in _types(conn)


def test_scene_close_closes_and_emits(conn: sqlite3.Connection) -> None:
    _run(conn, "scene open scene-1 Hall")
    _run(conn, "scene close scene-1")
    assert SceneStore(conn).get_scene(_CAMPAIGN, "scene-1").status.value == "closed"
    assert _types(conn).count("scene.closed") == 1


def test_scene_transition_closes_then_opens(conn: sqlite3.Connection) -> None:
    _run(conn, "scene open scene-a Hall")
    _run(conn, "scene transition scene-b Archive")
    scenes = SceneStore(conn)
    assert scenes.get_scene(_CAMPAIGN, "scene-a").status.value == "closed"
    assert scenes.get_open_scene(_CAMPAIGN).scene_id == "scene-b"
    assert _types(conn)[-2:] == ["scene.closed", "scene.opened"]


def test_scene_enter_and_exit_emit_their_events(conn: sqlite3.Connection) -> None:
    _run(conn, "scene open scene-1 Hall")
    _run(conn, "scene enter scene-1 pc-ada pc")
    assert SceneStore(conn).get_present_entity_ids(_CAMPAIGN, "scene-1") == ("pc-ada",)
    _run(conn, "scene exit scene-1 pc-ada")
    assert SceneStore(conn).get_present_entity_ids(_CAMPAIGN, "scene-1") == ()
    assert "scene.entity_entered" in _types(conn)
    assert "scene.entity_exited" in _types(conn)


def test_scene_location_emits_a_change(conn: sqlite3.Connection) -> None:
    _run(conn, "scene open scene-1 Hall")
    _run(conn, "scene location scene-1 loc-hall")
    assert SceneStore(conn).get_scene(_CAMPAIGN, "scene-1").location_entity_id == (
        "loc-hall"
    )
    assert "scene.location_changed" in _types(conn)


def test_time_set_emits_a_clock_change(conn: sqlite3.Connection) -> None:
    _run(conn, "time set Day-3")
    assert SceneStore(conn).get_game_time(_CAMPAIGN).in_world_label == "Day-3"
    assert "scene.time_changed" in _types(conn)


# -- rulings ----------------------------------------------------------------


def test_recording_a_ruling_does_not_confirm_it(conn: sqlite3.Connection) -> None:
    """Recording and precedence are separate; a recording is not precedent."""
    result = _run(conn, "ruling record ruling-1 gate")
    assert result["canon_state"] == "proposed"
    assert result["knowledge_state"] == "unrevealed"
    assert "ruling.recorded" in _types(conn)


def test_promoting_a_ruling_confirms_it_without_revealing_it(
    conn: sqlite3.Connection,
) -> None:
    _run(conn, "ruling record ruling-1 gate")
    result = _run(conn, "ruling promote ruling-1")
    assert result["canon_state"] == "confirmed"
    # Confirming precedent must not leak the ruling to players.
    assert result["knowledge_state"] == "unrevealed"
    assert "ruling.promoted" in _types(conn)


# -- no arbitrary targets ---------------------------------------------------


def test_a_mutation_never_targets_arbitrary_sql(conn: sqlite3.Connection) -> None:
    for text in (
        "scene obliterate everything",
        "fact reject; DROP TABLE facts",
        "sql DELETE FROM campaigns",
    ):
        with pytest.raises(GmCommandError):
            parse_gm_mutation(text)


def test_every_mutation_emits_at_least_one_event(
    conn: sqlite3.Connection,
) -> None:
    before = len(_types(conn))
    _run(conn, "scene open scene-1 Hall")
    assert len(_types(conn)) > before
