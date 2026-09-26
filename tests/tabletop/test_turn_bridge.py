"""One message, one turn, one generation, one queued output."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.api.visibility import gm_viewpoint
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.turn_job import TurnJobStore
from plugins.tabletop.turn_bridge import TurnBridge

_CAMPAIGN = "campaign-1"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_and_seed(tmp_path)
    yield connection
    connection.close()


def connect_and_seed(tmp_path: Path) -> sqlite3.Connection:
    from tabletop.storage.sqlite import connect, migrate

    connection = connect(tmp_path / "bridge.db")
    migrate(connection)
    store = CampaignStore(connection)
    store.create_campaign(_CAMPAIGN, "Bridge", "freeform")
    store.upsert_entity(_CAMPAIGN, "pc-ada", "Ada")
    SceneStore(connection).open_scene(_CAMPAIGN, "scene-1", "Crossroads")
    SceneStore(connection).enter(_CAMPAIGN, "scene-1", "pc-ada", _pc())
    return connection


def _pc():
    from tabletop.campaign.models import PresenceType

    return PresenceType.NPC


def _bridge(conn: sqlite3.Connection) -> TurnBridge:
    return TurnBridge(conn, campaign_id=_CAMPAIGN)


# -- receipt first ----------------------------------------------------------


def test_the_turn_exists_before_context_is_built(conn: sqlite3.Connection) -> None:
    bridge = _bridge(conn)
    turn = bridge.receive(
        "Ada tries the gate",
        channel="telegram", conversation_id="chat-1", external_message_id="1",
    )
    stored = TurnJobStore(conn).require(turn.turn_id)
    assert stored.status == "received"
    assert stored.external_message_id == "1"
    assert stored.conversation_id == "chat-1"


def test_context_is_built_for_that_turn(conn: sqlite3.Connection) -> None:
    bridge = _bridge(conn)
    turn = bridge.receive("Ada tries the gate")
    snapshot = bridge.build_context(turn, viewpoint=gm_viewpoint())
    assert snapshot["scene"]["scene_id"] == "scene-1"
    assert TurnJobStore(conn).require(turn.turn_id).status == "interpreting"


def test_duplicate_ingress_reuses_the_turn(conn: sqlite3.Connection) -> None:
    bridge = _bridge(conn)
    first = bridge.receive(
        "Ada tries the gate",
        channel="telegram", conversation_id="chat-1", external_message_id="1",
    )
    second = bridge.receive(
        "Ada tries the gate",
        channel="telegram", conversation_id="chat-1", external_message_id="1",
    )
    assert second.turn_id == first.turn_id
    assert bridge.is_duplicate(second) is True
    assert len(TurnJobStore(conn).list_for_campaign(_CAMPAIGN)) == 1


def test_equal_text_from_another_message_is_not_a_duplicate(
    conn: sqlite3.Connection,
) -> None:
    bridge = _bridge(conn)
    bridge.receive(
        "yes", channel="telegram", conversation_id="chat-1", external_message_id="1"
    )
    second = bridge.receive(
        "yes", channel="telegram", conversation_id="chat-1", external_message_id="2"
    )
    assert bridge.is_duplicate(second) is False
    assert len(TurnJobStore(conn).list_for_campaign(_CAMPAIGN)) == 2


# -- every generation has a durable id --------------------------------------


def test_a_generation_is_receipted_before_output_is_sent(
    conn: sqlite3.Connection,
) -> None:
    bridge = _bridge(conn)
    turn = bridge.receive("Ada tries the gate")
    bridge.start_generation(turn)
    bridge.record_generation(
        turn, 0, provider="p", model="m", response_text="a narration",
        output_segments=["a narration"],
    )
    delivery_id = bridge.enqueue_output(turn, "websocket", "a narration")

    from tabletop.orchestration.delivery import DeliveryStore, GenerationReceiptStore

    assert len(GenerationReceiptStore(conn).list_for_turn(turn.turn_id)) == 1
    delivered = DeliveryStore(conn).get(turn.turn_id, delivery_id)
    assert delivered is not None
    assert delivered.text == "a narration"


def test_a_disposition_is_recorded_on_the_turn(conn: sqlite3.Connection) -> None:
    bridge = _bridge(conn)
    turn = bridge.receive("Ada tries the gate")
    bridge.start_generation(turn)
    bridge.record_generation(turn, 0, response_text="narration")
    bridge.record_disposition(turn, "resolve")
    assert TurnJobStore(conn).require(turn.turn_id).disposition == "resolve"


# -- delivery ---------------------------------------------------------------


def test_output_is_queued_not_sent(conn: sqlite3.Connection) -> None:
    bridge = _bridge(conn)
    turn = bridge.receive("Ada tries the gate")
    bridge.start_generation(turn)
    bridge.record_generation(turn, 0, response_text="narration")
    delivery_id = bridge.enqueue_output(turn, "websocket", "narration")

    from tabletop.orchestration.delivery import DeliveryStore

    assert DeliveryStore(conn).require(turn.turn_id, delivery_id).status == "pending"


def test_a_delivered_turn_reaches_the_terminal_status(
    conn: sqlite3.Connection,
) -> None:
    bridge = _bridge(conn)
    turn = bridge.receive("Ada tries the gate")
    bridge.start_generation(turn)
    bridge.record_generation(turn, 0, response_text="narration")
    delivery_id = bridge.enqueue_output(turn, "websocket", "narration")
    bridge.mark_delivered(turn, delivery_id)
    assert TurnJobStore(conn).require(turn.turn_id).status == "delivered"


def test_a_turn_with_pending_output_is_not_delivered(
    conn: sqlite3.Connection,
) -> None:
    bridge = _bridge(conn)
    turn = bridge.receive("Ada tries the gate")
    bridge.start_generation(turn)
    bridge.record_generation(turn, 0, response_text="narration")
    first = bridge.enqueue_output(turn, "websocket", "one")
    second = bridge.enqueue_output(turn, "websocket", "two")
    bridge.mark_delivered(turn, first)
    assert TurnJobStore(conn).require(turn.turn_id).status != "delivered"
    bridge.mark_delivered(turn, second)
    assert TurnJobStore(conn).require(turn.turn_id).status == "delivered"


def test_a_delivery_keeps_its_route(conn: sqlite3.Connection) -> None:
    bridge = _bridge(conn)
    turn = bridge.receive("Ada tries the gate", channel="telegram")
    bridge.start_generation(turn)
    bridge.record_generation(turn, 0, response_text="narration")
    delivery_id = bridge.enqueue_output(
        turn, "telegram", "narration", conversation_id="chat-1"
    )

    from tabletop.orchestration.delivery import DeliveryStore

    delivery = DeliveryStore(conn).require(turn.turn_id, delivery_id)
    assert delivery.channel == "telegram"
    assert delivery.conversation_id == "chat-1"


# -- replayed steps are not errors ------------------------------------------


def test_replaying_a_step_does_not_raise(conn: sqlite3.Connection) -> None:
    """A retry replays the same steps; a turn already past one is fine."""
    bridge = _bridge(conn)
    turn = bridge.receive("Ada tries the gate")
    bridge.start_generation(turn)
    bridge.start_generation(turn)
    bridge.build_context(turn, viewpoint=gm_viewpoint())
    assert TurnJobStore(conn).require(turn.turn_id).status == "interpreting"


def test_the_whole_turn_is_explainable(conn: sqlite3.Connection) -> None:
    from tabletop.orchestration.turn_job import explain_turn

    bridge = _bridge(conn)
    turn = bridge.receive(
        "Ada tries the gate",
        channel="telegram", conversation_id="chat-1", external_message_id="1",
    )
    bridge.start_generation(turn)
    bridge.record_generation(turn, 0, provider="p", model="m", response_text="narration")
    delivery_id = bridge.enqueue_output(turn, "telegram", "narration")
    bridge.mark_delivered(turn, delivery_id)

    explained = explain_turn(conn, turn.turn_id)
    assert explained["turn"]["external_message_id"] == "1"
    assert explained["generations"][0]["model"] == "m"
    assert explained["deliveries"][0]["status"] == "delivered"
    assert explained["turn"]["status"] == "delivered"
