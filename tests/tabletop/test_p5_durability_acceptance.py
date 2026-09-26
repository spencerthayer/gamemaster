"""P5 acceptance: duplicate ingress, crash recovery, and honest delivery.

Every scenario runs against a real database file, and the recovery scenarios
close and reopen it, so nothing here can pass on in-process state.
"""

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
from tabletop.campaign.models import PresenceType
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.delivery import (
    DeliveryStore,
    DeliveryUncertain,
    recover_deliveries,
)
from tabletop.orchestration.turn_job import (
    TurnJobStore,
    decide_recovery,
    explain_turn,
)
from tabletop.storage.sqlite import connect, migrate
from plugins.tabletop.turn_bridge import TurnBridge, commit_action_effect

_CAMPAIGN = "p5-demo"


def _open(path: Path) -> sqlite3.Connection:
    """Open the database, seeding a campaign only the first time."""
    conn = connect(path)
    migrate(conn)
    store = CampaignStore(conn)
    if store.get_campaign(_CAMPAIGN) is None:
        store.create_campaign(_CAMPAIGN, "P5 Demo", "freeform")
        store.upsert_entity(
            _CAMPAIGN, "pc-ada", "Ada", system_state={"resources": {"hp": 10}}
        )
        SceneStore(conn).open_scene(_CAMPAIGN, "scene-1", "Crossroads")
        SceneStore(conn).enter(_CAMPAIGN, "scene-1", "pc-ada", PresenceType.PC)
    return conn


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = _open(tmp_path / "p5.db")
    yield connection
    connection.close()


def _action() -> GameAction:
    return GameAction(
        actor=EntityRef(id="pc-ada"),
        action_type="apply_damage",
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


# -- duplicate ingress ------------------------------------------------------


def test_the_same_native_message_twice_commits_one_effect(
    conn: sqlite3.Connection,
) -> None:
    """A channel retry must not apply a mechanical action twice."""
    bridge = TurnBridge(conn, campaign_id=_CAMPAIGN)
    ingress = dict(
        channel="telegram", conversation_id="chat-1", external_message_id="99"
    )

    first = bridge.receive("Ada forces the gate", **ingress)
    assert bridge.is_duplicate(first) is False
    commit_action_effect(
        conn, campaign_id=_CAMPAIGN, turn_id=first.turn_id, ordinal=0,
        action=_action(), resolution=_resolution(),
    )

    second = bridge.receive("Ada forces the gate", **ingress)
    assert second.turn_id == first.turn_id
    assert bridge.is_duplicate(second) is True
    commit_action_effect(
        conn, campaign_id=_CAMPAIGN, turn_id=second.turn_id, ordinal=0,
        action=_action(), resolution=_resolution(),
    )

    assert _resolved_events(conn) == ["action.resolved"]
    assert _hp(conn) == 7
    assert len(TurnJobStore(conn).list_for_campaign(_CAMPAIGN)) == 1


def test_equal_text_from_a_different_message_is_a_second_turn(
    conn: sqlite3.Connection,
) -> None:
    bridge = TurnBridge(conn, campaign_id=_CAMPAIGN)
    bridge.receive(
        "yes", channel="telegram", conversation_id="chat-1", external_message_id="1"
    )
    second = bridge.receive(
        "yes", channel="telegram", conversation_id="chat-1", external_message_id="2"
    )
    assert bridge.is_duplicate(second) is False
    assert len(TurnJobStore(conn).list_for_campaign(_CAMPAIGN)) == 2


# -- crash recovery ---------------------------------------------------------


def test_a_process_killed_after_commit_delivers_stored_output(
    tmp_path: Path,
) -> None:
    """Commit the action, die, restart, and deliver without rerolling."""
    path = tmp_path / "crash.db"
    conn = _open(path)
    bridge = TurnBridge(conn, campaign_id=_CAMPAIGN)
    turn = bridge.receive(
        "Ada forces the gate",
        channel="websocket", conversation_id="c1", external_message_id="1",
    )
    bridge.start_generation(turn)
    commit_action_effect(
        conn, campaign_id=_CAMPAIGN, turn_id=turn.turn_id, ordinal=0,
        action=_action(), resolution=_resolution(),
    )
    bridge.record_generation(turn, 0, response_text="Ada wounds the hound")
    bridge.enqueue_output(turn, "websocket", "Ada wounds the hound")
    turn_id = turn.turn_id
    conn.close()

    reopened = _open(path)
    try:
        # The action effect is already there and must not be redone.
        assert _resolved_events(reopened) == ["action.resolved"]
        assert _hp(reopened) == 7
        assert TurnJobStore(reopened).has_committed_effect(turn_id) is True
        assert decide_recovery(TurnJobStore(reopened), turn_id).can_retry_resolution is False

        sent: list[str] = []
        outcomes = recover_deliveries(reopened, lambda d: sent.append(d.text) or True)
        assert sent == ["Ada wounds the hound"]
        assert [o.status for o in outcomes] == ["delivered"]
        # Delivery must not have rerun the action.
        assert _resolved_events(reopened) == ["action.resolved"]
        assert _hp(reopened) == 7
    finally:
        reopened.close()


def test_a_turn_stays_pending_until_its_output_is_delivered(
    conn: sqlite3.Connection,
) -> None:
    bridge = TurnBridge(conn, campaign_id=_CAMPAIGN)
    turn = bridge.receive("Ada forces the gate")
    bridge.start_generation(turn)
    bridge.record_generation(turn, 0, response_text="text")
    bridge.record_disposition(turn, "resolve")
    assert TurnJobStore(conn).require(turn.turn_id).status != "delivered"

    delivery_id = bridge.enqueue_output(turn, "websocket", "text")
    assert TurnJobStore(conn).require(turn.turn_id).status != "delivered"
    bridge.mark_delivered(turn, delivery_id)
    assert TurnJobStore(conn).require(turn.turn_id).status == "delivered"


# -- honest delivery --------------------------------------------------------


def test_a_websocket_delivery_gets_exactly_once_semantics(
    conn: sqlite3.Connection,
) -> None:
    """A stable client_seq lets the remote deduplicate a retry."""
    turn = TurnBridge(conn, campaign_id=_CAMPAIGN).receive("Ada forces the gate")
    store = DeliveryStore(conn)
    delivery = store.enqueue(turn.turn_id, "websocket", "text")
    store.mark_sending(turn.turn_id, delivery.delivery_id, client_seq=7)
    store.mark_ambiguous(turn.turn_id, delivery.delivery_id, reason="no ack")

    retried = store.mark_sending(turn.turn_id, delivery.delivery_id, client_seq=None)
    assert retried.client_seq == 7
    store.mark_delivered(turn.turn_id, delivery.delivery_id, remote_ack="ack-7")
    assert store.require(turn.turn_id, delivery.delivery_id).remote_ack == "ack-7"


def test_a_telegram_delivery_reports_ambiguity_rather_than_claiming_delivery(
    conn: sqlite3.Connection,
) -> None:
    turn = TurnBridge(conn, campaign_id=_CAMPAIGN).receive("Ada forces the gate")
    DeliveryStore(conn).enqueue(turn.turn_id, "telegram", "text")

    def _uncertain(_d: object) -> bool:
        raise DeliveryUncertain("telegram gave no idempotency key")

    outcomes = recover_deliveries(conn, _uncertain)
    assert [o.status for o in outcomes] == ["ambiguous"]
    assert outcomes[0].duplicate_possible is True


# -- inspection -------------------------------------------------------------


def test_a_paused_turn_can_still_be_inspected(conn: sqlite3.Connection) -> None:
    turn = TurnBridge(conn, campaign_id=_CAMPAIGN).receive("Ada forces the gate")
    bridge = TurnBridge(conn, campaign_id=_CAMPAIGN)
    bridge.start_generation(turn)
    explained = explain_turn(conn, turn.turn_id)
    assert explained["turn"]["input_text"] == "Ada forces the gate"
    assert explained["recovery"]["action"] == "retry_resolution"
