"""Delivery retry reuses stored output and never reruns an action."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.campaign.event_store import EventStore
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.delivery import (
    DeliveryStore,
    DeliveryUncertain,
    recover_deliveries,
)
from tabletop.orchestration.turn_job import TurnJobStore
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "campaign-1"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "recover.db")
    migrate(connection)
    CampaignStore(connection).create_campaign(_CAMPAIGN, "Recovery", "freeform")
    yield connection
    connection.close()


def _turn(conn: sqlite3.Connection) -> str:
    return TurnJobStore(conn).claim_ingress(_CAMPAIGN, "Ada tries the gate").turn_id


def test_a_pending_delivery_is_sent_and_marked_delivered(
    conn: sqlite3.Connection,
) -> None:
    turn_id = _turn(conn)
    store = DeliveryStore(conn)
    delivery = store.enqueue(turn_id, "websocket", "stored text")
    sent: list[str] = []

    outcomes = recover_deliveries(conn, lambda d: sent.append(d.text) or True)

    assert [o.status for o in outcomes] == ["delivered"]
    assert sent == ["stored text"]
    assert store.require(turn_id, delivery.delivery_id).status == "delivered"


def test_a_crash_after_enqueue_is_recovered(conn: sqlite3.Connection) -> None:
    """The row exists before the send, so a crash leaves work to find."""
    turn_id = _turn(conn)
    DeliveryStore(conn).enqueue(turn_id, "websocket", "stored text")
    outcomes = recover_deliveries(conn, lambda _d: True)
    assert [o.status for o in outcomes] == ["delivered"]


def test_a_refused_transport_is_retried_not_lost(conn: sqlite3.Connection) -> None:
    turn_id = _turn(conn)
    store = DeliveryStore(conn)
    delivery = store.enqueue(turn_id, "websocket", "text")
    outcomes = recover_deliveries(conn, lambda _d: False)
    assert [o.status for o in outcomes] == ["failed"]
    assert store.require(turn_id, delivery.delivery_id).attempts == 1
    assert store.require(turn_id, delivery.delivery_id).last_error == "transport refused"


def test_an_uncertain_send_becomes_ambiguous(conn: sqlite3.Connection) -> None:
    """A transport with no idempotency key cannot prove the send."""
    turn_id = _turn(conn)
    store = DeliveryStore(conn)
    delivery = store.enqueue(turn_id, "telegram", "text")

    def _uncertain(_delivery: object) -> bool:
        raise DeliveryUncertain("no ack")

    outcomes = recover_deliveries(conn, _uncertain)
    assert [o.status for o in outcomes] == ["ambiguous"]
    assert outcomes[0].duplicate_possible is True
    assert store.require(turn_id, delivery.delivery_id).status == "ambiguous"


def test_an_ambiguous_delivery_is_not_retried_automatically(
    conn: sqlite3.Connection,
) -> None:
    turn_id = _turn(conn)
    DeliveryStore(conn).enqueue(turn_id, "telegram", "text")

    def _uncertain(_delivery: object) -> bool:
        raise DeliveryUncertain("no ack")

    recover_deliveries(conn, _uncertain)
    again = recover_deliveries(conn, lambda _d: True)
    assert again == []


def test_retries_are_bounded(conn: sqlite3.Connection) -> None:
    turn_id = _turn(conn)
    store = DeliveryStore(conn)
    delivery = store.enqueue(turn_id, "websocket", "text")
    for _ in range(3):
        store.mark_sending(turn_id, delivery.delivery_id, client_seq=None)
        store.mark_failed(turn_id, delivery.delivery_id, reason="refused")
    # Back to pending, now at the cap.
    conn.execute(
        "UPDATE turn_deliveries SET status = 'pending' WHERE turn_id = ? AND delivery_id = ?",
        (turn_id, delivery.delivery_id),
    )
    outcomes = recover_deliveries(conn, lambda _d: True, max_attempts=3)
    assert [o.status for o in outcomes] == ["failed"]
    assert "exceeded" in store.require(turn_id, delivery.delivery_id).last_error


def test_a_websocket_client_seq_survives_a_retry(conn: sqlite3.Connection) -> None:
    turn_id = _turn(conn)
    store = DeliveryStore(conn)
    delivery = store.enqueue(turn_id, "websocket", "text")
    store.mark_sending(turn_id, delivery.delivery_id, client_seq=11)
    store.mark_ambiguous(turn_id, delivery.delivery_id, reason="crash")

    seqs: list[int | None] = []
    recover_deliveries(
        conn, lambda d: seqs.append(d.client_seq) or True
    )
    # The retry offered the same key, so the remote can deduplicate.
    assert seqs == []  # ambiguous is not retried automatically
    retried = store.mark_sending(turn_id, delivery.delivery_id, client_seq=None)
    assert retried.client_seq == 11


def test_segments_are_sent_in_order(conn: sqlite3.Connection) -> None:
    turn_id = _turn(conn)
    store = DeliveryStore(conn)
    store.enqueue(turn_id, "websocket", "first", segment=0)
    store.enqueue(turn_id, "websocket", "second", segment=1)
    order: list[str] = []
    recover_deliveries(conn, lambda d: order.append(d.text) or True)
    assert order == ["first", "second"]


def test_recovery_resends_stored_text_not_regenerated(conn: sqlite3.Connection) -> None:
    turn_id = _turn(conn)
    store = DeliveryStore(conn)
    delivery = store.enqueue(turn_id, "websocket", "exactly this text")
    store.mark_failed(turn_id, delivery.delivery_id, reason="crash")
    sent: list[str] = []
    recover_deliveries(conn, lambda d: sent.append(d.text) or True)
    assert sent == ["exactly this text"]


def test_recovery_never_runs_an_action(conn: sqlite3.Connection) -> None:
    """Only output is resent. The event log must be untouched."""
    turn_id = _turn(conn)
    store = TurnJobStore(conn)
    store.claim_action(turn_id, 0, "check")
    DeliveryStore(conn).enqueue(turn_id, "websocket", "text")
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    recover_deliveries(conn, lambda _d: True)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before
    assert TurnJobStore(conn).action_claims(turn_id)[0]["status"] == "claimed"


def test_recovery_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "restart.db"
    conn = connect(path)
    migrate(conn)
    CampaignStore(conn).create_campaign(_CAMPAIGN, "Recovery", "freeform")
    turn_id = TurnJobStore(conn).claim_ingress(_CAMPAIGN, "Ada tries the gate").turn_id
    DeliveryStore(conn).enqueue(turn_id, "websocket", "stored text")
    conn.close()

    reopened = connect(path)
    try:
        sent: list[str] = []
        outcomes = recover_deliveries(reopened, lambda d: sent.append(d.text) or True)
        assert sent == ["stored text"]
        assert [o.status for o in outcomes] == ["delivered"]
    finally:
        reopened.close()


def test_a_completed_response_stays_pending_until_delivery(
    conn: sqlite3.Connection,
) -> None:
    turn_id = _turn(conn)
    turns = TurnJobStore(conn)
    turns.transition(turn_id, "interpreting")
    turns.transition(turn_id, "narrating")
    turns.transition(turn_id, "completed")
    DeliveryStore(conn).enqueue(turn_id, "websocket", "text")
    assert turns.require(turn_id).status == "completed"

    recover_deliveries(conn, lambda _d: True)
    assert DeliveryStore(conn).list_for_turn(turn_id)[0].status == "delivered"
