"""A committed action effect survives every crash window, and never repeats."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.api.actions import GameAction
from tabletop.api.entities import EntityRef
from tabletop.api.resolution import (
    Resolution,
    ResolutionStatus,
    StateChange,
    StateOperation,
)
from tabletop.campaign.event_store import EventStore
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.turn_job import TurnJobStore
from tabletop.storage.sqlite import connect, migrate
from plugins.tabletop.turn_bridge import commit_action_effect

_CAMPAIGN = "campaign-1"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "effects.db")
    migrate(connection)
    store = CampaignStore(connection)
    store.create_campaign(_CAMPAIGN, "Effects", "freeform")
    store.upsert_entity(
        _CAMPAIGN, "pc-ada", "Ada", system_state={"resources": {"hp": 10}}
    )
    SceneStore(connection).open_scene(_CAMPAIGN, "scene-1", "Crossroads")
    yield connection
    connection.close()


def _turn(conn: sqlite3.Connection) -> str:
    return TurnJobStore(conn).claim_ingress(_CAMPAIGN, "Ada heals").turn_id


def _action() -> GameAction:
    return GameAction(
        actor=EntityRef(id="pc-ada"),
        action_type="apply_damage",
        parameters={"path": ("entities", "pc-ada", "system", "resources", "hp")},
    )


def _resolution(amount: int = 3) -> Resolution:
    return Resolution(
        outcome={"decremented": amount},
        status=ResolutionStatus.RESOLVED,
        state_changes=(
            StateChange(
                operation=StateOperation.SET,
                path=("entities", "pc-ada", "system", "resources", "hp"),
                value=10 - amount,
            ),
        ),
        explanation="hp reduced",
    )


def _resolved_events(conn: sqlite3.Connection) -> list[str]:
    return [
        event.event_type
        for event in EventStore(conn).read(_CAMPAIGN)
        if event.event_type == "action.resolved"
    ]


def _hp(conn: sqlite3.Connection) -> int:
    import json

    row = conn.execute(
        "SELECT system_state FROM entities WHERE entity_id = 'pc-ada'"
    ).fetchone()
    return json.loads(row["system_state"])["resources"]["hp"]


# -- the happy path ---------------------------------------------------------


def test_committing_writes_event_state_and_claim(
    conn: sqlite3.Connection,
) -> None:
    turn_id = _turn(conn)
    commit_action_effect(
        conn,
        campaign_id=_CAMPAIGN,
        turn_id=turn_id,
        ordinal=0,
        action=_action(),
        resolution=_resolution(),
    )
    assert _resolved_events(conn) == ["action.resolved"]
    assert _hp(conn) == 7
    claim = TurnJobStore(conn).action_claims(turn_id)[0]
    assert claim["status"] == "committed"
    assert claim["event_sequence"] == 1


# -- exactly once -----------------------------------------------------------


def test_a_second_commit_does_not_apply_the_effect_twice(
    conn: sqlite3.Connection,
) -> None:
    turn_id = _turn(conn)
    for _ in range(3):
        commit_action_effect(
            conn,
            campaign_id=_CAMPAIGN,
            turn_id=turn_id,
            ordinal=0,
            action=_action(),
            resolution=_resolution(),
        )
    assert _resolved_events(conn) == ["action.resolved"]
    assert _hp(conn) == 7


def test_a_replay_after_restart_does_not_rerun(conn: sqlite3.Connection) -> None:
    turn_id = _turn(conn)
    commit_action_effect(
        conn,
        campaign_id=_CAMPAIGN,
        turn_id=turn_id,
        ordinal=0,
        action=_action(),
        resolution=_resolution(),
    )
    TurnJobStore(conn).claim_action(turn_id, 0, "apply_damage")
    assert TurnJobStore(conn).has_committed_effect(turn_id) is True
    assert _resolved_events(conn) == ["action.resolved"]
    assert _hp(conn) == 7


def test_two_ordinals_apply_independently(conn: sqlite3.Connection) -> None:
    turn_id = _turn(conn)
    commit_action_effect(
        conn, campaign_id=_CAMPAIGN, turn_id=turn_id, ordinal=0,
        action=_action(), resolution=_resolution(3),
    )
    commit_action_effect(
        conn, campaign_id=_CAMPAIGN, turn_id=turn_id, ordinal=1,
        action=_action(), resolution=_resolution(2),
    )
    assert _resolved_events(conn) == ["action.resolved", "action.resolved"]
    # Each ordinal sets an absolute value, so the second write wins.
    assert _hp(conn) == 8
    assert len(TurnJobStore(conn).action_claims(turn_id)) == 2


# -- crash windows ----------------------------------------------------------


def test_a_failure_before_the_transaction_writes_nothing(
    conn: sqlite3.Connection,
) -> None:
    turn_id = _turn(conn)
    with pytest.raises(RuntimeError):
        with __import__("tabletop.storage.sqlite", fromlist=["transaction"]).transaction(conn):
            raise RuntimeError("crash before the plugin result is applied")
    assert _resolved_events(conn) == []
    assert _hp(conn) == 10
    assert TurnJobStore(conn).action_claims(turn_id) == ()


def test_a_failure_during_state_application_rolls_back_the_event(
    conn: sqlite3.Connection,
) -> None:
    """A crash between the event and the state change must leave neither."""
    turn_id = _turn(conn)
    broken = Resolution(
        outcome={},
        status=ResolutionStatus.RESOLVED,
        state_changes=(
            StateChange(
                operation=StateOperation.SET,
                path=("entities", "ghost", "system", "x"),
                value=1,
            ),
        ),
    )
    with pytest.raises(Exception):
        commit_action_effect(
            conn,
            campaign_id=_CAMPAIGN,
            turn_id=turn_id,
            ordinal=0,
            action=_action(),
            resolution=broken,
        )
    assert _resolved_events(conn) == []
    assert TurnJobStore(conn).action_claims(turn_id) == ()


def test_a_failure_after_the_event_rolls_back_the_event_too(
    conn: sqlite3.Connection,
) -> None:
    """The claim callback raising must not leave a half-committed effect."""
    from tabletop.campaign.event_store import apply_resolved_action_in_transaction
    from tabletop.storage.sqlite import transaction

    turn_id = _turn(conn)

    def _boom(_persisted: object) -> None:
        raise RuntimeError("crash before the transaction commits")

    with pytest.raises(RuntimeError):
        with transaction(conn):
            apply_resolved_action_in_transaction(
                conn, _CAMPAIGN, _action(), _resolution(), on_committed=_boom
            )
    assert _resolved_events(conn) == []
    assert _hp(conn) == 10


def test_a_claim_without_a_committed_effect_is_replayable(
    conn: sqlite3.Connection,
) -> None:
    """A crash after claiming but before applying leaves the action retryable."""
    turn_id = _turn(conn)
    TurnJobStore(conn).claim_action(turn_id, 0, "apply_damage")
    assert TurnJobStore(conn).has_committed_effect(turn_id) is False

    commit_action_effect(
        conn,
        campaign_id=_CAMPAIGN,
        turn_id=turn_id,
        ordinal=0,
        action=_action(),
        resolution=_resolution(),
    )
    assert _resolved_events(conn) == ["action.resolved"]
    assert _hp(conn) == 7


def test_recovery_reads_the_recorded_outcome_not_a_reroll(
    conn: sqlite3.Connection,
) -> None:
    turn_id = _turn(conn)
    commit_action_effect(
        conn, campaign_id=_CAMPAIGN, turn_id=turn_id, ordinal=0,
        action=_action(), resolution=_resolution(3),
    )
    # Replay with a different amount: the committed result must stand.
    commit_action_effect(
        conn, campaign_id=_CAMPAIGN, turn_id=turn_id, ordinal=0,
        action=_action(), resolution=_resolution(9),
    )
    assert _hp(conn) == 7
