"""Generation receipts and the delivery outbox are turn-linked and honest."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.delivery import (
    DeliveryStore,
    GenerationReceiptStore,
)
from tabletop.orchestration.turn_job import TurnJobStore
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "campaign-1"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "delivery.db")
    migrate(connection)
    CampaignStore(connection).create_campaign(_CAMPAIGN, "First", "freeform")
    yield connection
    connection.close()


@pytest.fixture
def turn_id(conn: sqlite3.Connection) -> str:
    return TurnJobStore(conn).claim_ingress(_CAMPAIGN, "Ada tries the gate").turn_id


# -- generation receipts ----------------------------------------------------


def test_a_turn_may_record_several_generations(
    conn: sqlite3.Connection, turn_id: str
) -> None:
    store = GenerationReceiptStore(conn)
    first = store.record(turn_id, 0, provider="test", model="m", tokens_in=10, tokens_out=20)
    second = store.record(turn_id, 1, provider="test", model="m")
    assert first.ordinal == 0
    assert second.ordinal == 1
    assert len(store.list_for_turn(turn_id)) == 2


def test_an_ordinal_cannot_be_recorded_twice(
    conn: sqlite3.Connection, turn_id: str
) -> None:
    store = GenerationReceiptStore(conn)
    store.record(turn_id, 0)
    with pytest.raises(sqlite3.IntegrityError):
        store.record(turn_id, 0)


def test_a_receipt_hashes_its_prompt_and_response(
    conn: sqlite3.Connection, turn_id: str
) -> None:
    import hashlib

    receipt = GenerationReceiptStore(conn).record(
        turn_id, 0, prompt_text="hello", response_text="world"
    )
    assert receipt.prompt_sha256 == hashlib.sha256(b"hello").hexdigest()
    assert receipt.response_sha256 == hashlib.sha256(b"world").hexdigest()


def test_a_receipt_keeps_its_output_segments(
    conn: sqlite3.Connection, turn_id: str
) -> None:
    receipt = GenerationReceiptStore(conn).record(
        turn_id, 0, output_segments=["first", "second"]
    )
    assert receipt.output_segments == ("first", "second")


def test_a_receipt_records_provider_telemetry(
    conn: sqlite3.Connection, turn_id: str
) -> None:
    receipt = GenerationReceiptStore(conn).record(
        turn_id, 0, provider="p", model="m", tokens_in=1, tokens_out=2, latency_ms=30
    )
    assert (receipt.provider, receipt.model) == ("p", "m")
    assert (receipt.tokens_in, receipt.tokens_out, receipt.latency_ms) == (1, 2, 30)


def test_receipts_are_not_campaign_canon(
    conn: sqlite3.Connection, turn_id: str
) -> None:
    GenerationReceiptStore(conn).record(turn_id, 0, response_text="a narration")
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


# -- delivery outbox --------------------------------------------------------


def test_an_enqueued_delivery_starts_pending(conn: sqlite3.Connection, turn_id: str) -> None:
    delivery = DeliveryStore(conn).enqueue(turn_id, "websocket", "Ada forces the gate")
    assert delivery.status == "pending"
    assert delivery.attempts == 0
    assert delivery.is_retryable is True


def test_a_delivery_id_is_stable_across_state_changes(
    conn: sqlite3.Connection, turn_id: str
) -> None:
    store = DeliveryStore(conn)
    delivery = store.enqueue(turn_id, "websocket", "text")
    store.mark_sending(turn_id, delivery.delivery_id, client_seq=1)
    assert store.require(turn_id, delivery.delivery_id).delivery_id == delivery.delivery_id


def test_marking_sending_increments_attempts(
    conn: sqlite3.Connection, turn_id: str
) -> None:
    store = DeliveryStore(conn)
    delivery = store.enqueue(turn_id, "websocket", "text")
    store.mark_sending(turn_id, delivery.delivery_id, client_seq=1)
    store.mark_failed(turn_id, delivery.delivery_id, reason="timeout")
    again = store.mark_sending(turn_id, delivery.delivery_id, client_seq=2)
    assert again.attempts == 2


def test_a_delivered_delivery_is_not_retryable(
    conn: sqlite3.Connection, turn_id: str
) -> None:
    store = DeliveryStore(conn)
    delivery = store.enqueue(turn_id, "websocket", "text")
    store.mark_sending(turn_id, delivery.delivery_id, client_seq=1)
    done = store.mark_delivered(turn_id, delivery.delivery_id, remote_ack="ack-1")
    assert done.status == "delivered"
    assert done.remote_ack == "ack-1"
    assert done.is_retryable is False


def test_a_sending_delivery_is_not_retryable(
    conn: sqlite3.Connection, turn_id: str
) -> None:
    """Mid-send, the outcome is unknown, so a blind retry could duplicate."""
    store = DeliveryStore(conn)
    delivery = store.enqueue(turn_id, "websocket", "text")
    sending = store.mark_sending(turn_id, delivery.delivery_id, client_seq=1)
    assert sending.is_retryable is False


def test_an_uncertain_send_becomes_ambiguous(
    conn: sqlite3.Connection, turn_id: str
) -> None:
    store = DeliveryStore(conn)
    delivery = store.enqueue(turn_id, "telegram", "text")
    store.mark_sending(turn_id, delivery.delivery_id, client_seq=None)
    ambiguous = store.mark_ambiguous(turn_id, delivery.delivery_id, reason="no ack")
    assert ambiguous.status == "ambiguous"
    assert ambiguous.last_error == "no ack"
    assert ambiguous.is_retryable is False


def test_a_websocket_client_seq_survives_a_retry(
    conn: sqlite3.Connection, turn_id: str
) -> None:
    """A transport with a stable key can deduplicate on retry."""
    store = DeliveryStore(conn)
    delivery = store.enqueue(turn_id, "websocket", "text")
    store.mark_sending(turn_id, delivery.delivery_id, client_seq=7)
    store.mark_ambiguous(turn_id, delivery.delivery_id, reason="no ack")
    retried = store.mark_sending(turn_id, delivery.delivery_id, client_seq=None)
    assert retried.client_seq == 7


def test_several_segments_keep_their_order(
    conn: sqlite3.Connection, turn_id: str
) -> None:
    store = DeliveryStore(conn)
    store.enqueue(turn_id, "websocket", "first", segment=0)
    store.enqueue(turn_id, "websocket", "second", segment=1)
    assert [d.segment for d in store.list_for_turn(turn_id)] == [0, 1]
    assert [d.text for d in store.list_for_turn(turn_id)] == ["first", "second"]


def test_deliveries_can_be_listed_by_status(
    conn: sqlite3.Connection, turn_id: str
) -> None:
    store = DeliveryStore(conn)
    first = store.enqueue(turn_id, "websocket", "one")
    store.enqueue(turn_id, "websocket", "two")
    store.mark_delivered(turn_id, first.delivery_id)
    assert [d.text for d in store.list_by_status("pending")] == ["two"]


def test_a_delivery_keeps_its_conversation_route(
    conn: sqlite3.Connection, turn_id: str
) -> None:
    delivery = DeliveryStore(conn).enqueue(
        turn_id, "telegram", "text", conversation_id="chat-9"
    )
    assert delivery.conversation_id == "chat-9"


def test_deleting_a_turn_deletes_its_deliveries_and_receipts(
    conn: sqlite3.Connection, turn_id: str
) -> None:
    store = DeliveryStore(conn)
    store.enqueue(turn_id, "websocket", "text")
    GenerationReceiptStore(conn).record(turn_id, 0)
    conn.execute("DELETE FROM turn_jobs WHERE turn_id = ?", (turn_id,))
    assert conn.execute("SELECT COUNT(*) FROM turn_deliveries").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM generation_receipts").fetchone()[0] == 0


def test_stored_output_survives_a_restart(tmp_path: Path, turn_id: str) -> None:
    """Delivery retry must reuse stored text, not regenerate it."""
    path = tmp_path / "restart.db"
    conn = connect(path)
    migrate(conn)
    CampaignStore(conn).create_campaign(_CAMPAIGN, "First", "freeform")
    turn = TurnJobStore(conn).claim_ingress(_CAMPAIGN, "Ada tries the gate")
    delivery = DeliveryStore(conn).enqueue(turn.turn_id, "websocket", "stored text")
    DeliveryStore(conn).mark_failed(turn.turn_id, delivery.delivery_id, reason="crash")
    conn.close()

    reopened = connect(path)
    try:
        recovered = DeliveryStore(reopened).list_for_turn(turn.turn_id)
        assert len(recovered) == 1
        assert recovered[0].text == "stored text"
        assert recovered[0].is_retryable is True
    finally:
        reopened.close()
