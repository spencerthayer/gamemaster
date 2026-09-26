"""Recovery decisions come from event presence, never from wall-clock age."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.delivery import DeliveryStore, GenerationReceiptStore
from tabletop.orchestration.turn_job import (
    LEGAL_TRANSITIONS,
    TERMINAL_STATUSES,
    TurnJobStore,
    TurnTransitionError,
    decide_recovery,
    explain_turn,
)
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "campaign-1"

ALL_STATUSES = tuple(LEGAL_TRANSITIONS)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "recovery.db")
    migrate(connection)
    CampaignStore(connection).create_campaign(_CAMPAIGN, "First", "freeform")
    yield connection
    connection.close()


@pytest.fixture
def store(conn: sqlite3.Connection) -> TurnJobStore:
    return TurnJobStore(conn)


def _turn(store: TurnJobStore) -> str:
    return store.claim_ingress(_CAMPAIGN, "Ada tries the gate").turn_id


def _age(conn: sqlite3.Connection, turn_id: str, days: int) -> None:
    """Backdate a turn so age and evidence can be told apart."""
    stamp = (
        (datetime.now(timezone.utc) - timedelta(days=days))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    conn.execute(
        "UPDATE turn_jobs SET created_at = ?, updated_at = ? WHERE turn_id = ?",
        (stamp, stamp, turn_id),
    )


# -- the table covers every status ------------------------------------------


def test_the_transition_table_covers_every_declared_status() -> None:
    assert set(ALL_STATUSES) == {
        "received", "interpreting", "awaiting_player", "awaiting_gm",
        "resolving", "narrating", "completed", "delivery_pending",
        "delivered", "failed", "cancelled",
    }


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_every_status_is_representable(store: TurnJobStore, status: str) -> None:
    turn_id = _turn(store)
    store.conn.execute(
        "UPDATE turn_jobs SET status = ? WHERE turn_id = ?", (status, turn_id)
    )
    assert store.require(turn_id).status == status


def test_terminal_statuses_have_no_exits() -> None:
    for status in TERMINAL_STATUSES:
        assert LEGAL_TRANSITIONS[status] == frozenset(), status


def test_an_illegal_transition_fails_closed(store: TurnJobStore) -> None:
    turn_id = _turn(store)
    with pytest.raises(TurnTransitionError):
        store.transition(turn_id, "delivered")
    assert store.require(turn_id).status == "received"


# -- event presence decides, not age ----------------------------------------


def test_a_crashed_pre_commit_turn_retries(
    store: TurnJobStore, conn: sqlite3.Connection
) -> None:
    turn_id = _turn(store)
    store.transition(turn_id, "interpreting")
    store.transition(turn_id, "resolving")
    _age(conn, turn_id, days=7)
    decision = decide_recovery(store, turn_id)
    assert decision.action == "retry_resolution"
    assert decision.can_retry_resolution is True


def test_a_crashed_post_commit_turn_never_retries(
    store: TurnJobStore, conn: sqlite3.Connection
) -> None:
    """The same age, but an effect is committed. Rerunning would apply it twice."""
    turn_id = _turn(store)
    store.transition(turn_id, "interpreting")
    store.transition(turn_id, "resolving")
    store.claim_action(turn_id, 0, "check")
    store.commit_action(turn_id, 0, event_sequence=7)
    _age(conn, turn_id, days=7)
    decision = decide_recovery(store, turn_id)
    assert decision.action == "commit_pending"
    assert decision.can_retry_resolution is False
    assert decision.committed_event_sequences == (7,)


def test_a_fresh_post_commit_turn_also_never_retries(store: TurnJobStore) -> None:
    """Age is irrelevant: a new turn with a committed effect is the same case."""
    turn_id = _turn(store)
    store.transition(turn_id, "interpreting")
    store.transition(turn_id, "resolving")
    store.claim_action(turn_id, 0, "check")
    store.commit_action(turn_id, 0, event_sequence=1)
    assert decide_recovery(store, turn_id).can_retry_resolution is False


def test_a_turn_awaiting_a_player_retries(store: TurnJobStore) -> None:
    turn_id = _turn(store)
    store.transition(turn_id, "interpreting")
    store.transition(turn_id, "awaiting_player")
    assert decide_recovery(store, turn_id).action == "retry_resolution"


def test_a_turn_awaiting_the_gm_retries(store: TurnJobStore) -> None:
    turn_id = _turn(store)
    store.transition(turn_id, "interpreting")
    store.transition(turn_id, "awaiting_gm")
    assert decide_recovery(store, turn_id).action == "retry_resolution"


def test_a_narrating_turn_retries_only_narration(store: TurnJobStore) -> None:
    turn_id = _turn(store)
    for status in ("interpreting", "resolving", "narrating"):
        store.transition(turn_id, status)
    decision = decide_recovery(store, turn_id)
    assert decision.action == "retry_narration"
    assert decision.can_retry_resolution is False


def test_a_completed_turn_retries_delivery_only(store: TurnJobStore) -> None:
    turn_id = _turn(store)
    for status in ("interpreting", "resolving", "narrating", "completed"):
        store.transition(turn_id, status)
    decision = decide_recovery(store, turn_id)
    assert decision.action == "retry_delivery"
    assert decision.can_retry_resolution is False


def test_a_delivery_pending_turn_retries_delivery(store: TurnJobStore) -> None:
    turn_id = _turn(store)
    for status in ("interpreting", "resolving", "narrating", "completed", "delivery_pending"):
        store.transition(turn_id, status)
    assert decide_recovery(store, turn_id).action == "retry_delivery"


def test_a_terminal_turn_is_never_retried(store: TurnJobStore) -> None:
    for terminal in TERMINAL_STATUSES:
        turn_id = _turn(store)
        store.conn.execute(
            "UPDATE turn_jobs SET status = ? WHERE turn_id = ?", (terminal, turn_id)
        )
        assert decide_recovery(store, turn_id).action == "none", terminal


# -- continuation links -----------------------------------------------------


def test_a_resumed_turn_points_at_its_parent(store: TurnJobStore) -> None:
    first = _turn(store)
    second = store.claim_ingress(
        _CAMPAIGN, "the gate, on the left", resumes_turn_id=first
    )
    assert second.resumes_turn_id == first


# -- explanation ------------------------------------------------------------


def test_explain_reports_the_whole_turn(
    conn: sqlite3.Connection, store: TurnJobStore
) -> None:
    turn_id = _turn(store)
    store.transition(turn_id, "interpreting")
    store.claim_action(turn_id, 0, "check")
    store.commit_action(turn_id, 0, event_sequence=7)
    GenerationReceiptStore(conn).record(turn_id, 0, response_text="a narration")
    DeliveryStore(conn).enqueue(turn_id, "websocket", "a narration")

    explained = explain_turn(conn, turn_id)
    assert explained["turn"]["turn_id"] == turn_id
    assert explained["turn"]["input_text"] == "Ada tries the gate"
    assert explained["action_effects"][0]["event_sequence"] == 7
    assert len(explained["generations"]) == 1
    assert explained["deliveries"][0]["status"] == "pending"
    assert explained["recovery"]["action"] == "commit_pending"


def test_explain_marks_absent_evidence_rather_than_inventing_it(
    conn: sqlite3.Connection, store: TurnJobStore
) -> None:
    turn_id = _turn(store)
    explained = explain_turn(conn, turn_id)
    assert explained["generations"] == []
    assert explained["deliveries"] == []
    assert explained["action_effects"] == []
    assert explained["turn"]["disposition"] is None


def test_explain_writes_nothing(conn: sqlite3.Connection, store: TurnJobStore) -> None:
    turn_id = _turn(store)
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    explain_turn(conn, turn_id)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before
