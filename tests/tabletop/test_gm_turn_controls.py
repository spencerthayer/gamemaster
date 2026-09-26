"""Retry semantics are distinct, and pausing never discards queued turns."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.campaign.scenes import (
    CampaignPaused,
    is_paused,
    pause_campaign,
    require_not_paused,
    resume_campaign,
)
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.delivery import DeliveryStore
from tabletop.orchestration.gm_commands import (
    GmTurnError,
    gm_cancel_turn,
    gm_retry_delivery,
    gm_retry_generation,
    gm_turn_status,
)
from tabletop.orchestration.turn_job import TurnJobStore
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "campaign-1"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "pause.db")
    migrate(connection)
    CampaignStore(connection).create_campaign(_CAMPAIGN, "First", "freeform")
    yield connection
    connection.close()


def _turn(conn: sqlite3.Connection) -> str:
    return TurnJobStore(conn).claim_ingress(_CAMPAIGN, "Ada tries the gate").turn_id


def _committed(conn: sqlite3.Connection, turn_id: str) -> None:
    store = TurnJobStore(conn)
    store.claim_action(turn_id, 0, "check")
    store.commit_action(turn_id, 0, event_sequence=1)


# -- pause ------------------------------------------------------------------


def test_a_new_campaign_is_not_paused(conn: sqlite3.Connection) -> None:
    assert is_paused(conn, _CAMPAIGN) is False


def test_pausing_stops_new_player_processing(conn: sqlite3.Connection) -> None:
    pause_campaign(conn, _CAMPAIGN, reason="session break")
    assert is_paused(conn, _CAMPAIGN) is True
    with pytest.raises(CampaignPaused, match="paused"):
        require_not_paused(conn, _CAMPAIGN)


def test_resuming_permits_processing_again(conn: sqlite3.Connection) -> None:
    pause_campaign(conn, _CAMPAIGN)
    resume_campaign(conn, _CAMPAIGN)
    assert is_paused(conn, _CAMPAIGN) is False
    require_not_paused(conn, _CAMPAIGN)


def test_pausing_does_not_discard_queued_turns(conn: sqlite3.Connection) -> None:
    turn_id = _turn(conn)
    pause_campaign(conn, _CAMPAIGN)
    assert TurnJobStore(conn).require(turn_id).status == "received"
    assert gm_turn_status(conn, _CAMPAIGN, turn_id)["status"] == "received"


def test_a_paused_campaign_can_still_be_inspected(conn: sqlite3.Connection) -> None:
    turn_id = _turn(conn)
    pause_campaign(conn, _CAMPAIGN)
    status = gm_turn_status(conn, _CAMPAIGN, turn_id)
    assert status["turn_id"] == turn_id
    assert status["status"] == "received"


def test_queued_turns_continue_in_order_after_resume(conn: sqlite3.Connection) -> None:
    first = _turn(conn)
    second = _turn(conn)
    pause_campaign(conn, _CAMPAIGN)
    resume_campaign(conn, _CAMPAIGN)
    queued = TurnJobStore(conn).list_for_campaign(_CAMPAIGN)
    assert [job.turn_id for job in queued] == [first, second]


def test_pause_is_scoped_to_one_campaign(tmp_path: Path, conn: sqlite3.Connection) -> None:
    CampaignStore(conn).create_campaign("campaign-2", "Second", "freeform")
    pause_campaign(conn, _CAMPAIGN)
    assert is_paused(conn, "campaign-2") is False


# -- retry-generation -------------------------------------------------------


def test_retry_generation_is_allowed_before_a_commit(
    conn: sqlite3.Connection,
) -> None:
    turn_id = _turn(conn)
    result = gm_retry_generation(conn, turn_id)
    assert result["retried"] == "generation"


def test_retry_generation_is_refused_after_a_commit(
    conn: sqlite3.Connection,
) -> None:
    turn_id = _turn(conn)
    _committed(conn, turn_id)
    with pytest.raises(GmTurnError, match="already committed"):
        gm_retry_generation(conn, turn_id)


def test_retry_generation_names_the_alternative(conn: sqlite3.Connection) -> None:
    turn_id = _turn(conn)
    _committed(conn, turn_id)
    with pytest.raises(GmTurnError, match="retry-delivery"):
        gm_retry_generation(conn, turn_id)


# -- retry-delivery ---------------------------------------------------------


def test_retry_delivery_resends_stored_output(conn: sqlite3.Connection) -> None:
    turn_id = _turn(conn)
    DeliveryStore(conn).enqueue(turn_id, "websocket", "stored text")
    sent: list[str] = []
    result = gm_retry_delivery(
        conn, _CAMPAIGN, turn_id, lambda d: sent.append(d.text) or True
    )
    assert sent == ["stored text"]
    assert result["retried"] == "delivery"
    assert result["outcomes"][0]["status"] == "delivered"


def test_retry_delivery_never_reruns_a_committed_action(
    conn: sqlite3.Connection,
) -> None:
    turn_id = _turn(conn)
    _committed(conn, turn_id)
    DeliveryStore(conn).enqueue(turn_id, "websocket", "text")
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    gm_retry_delivery(conn, _CAMPAIGN, turn_id, lambda _d: True)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before
    assert TurnJobStore(conn).action_claims(turn_id)[0]["status"] == "committed"


def test_retry_delivery_reuses_the_delivery_id(conn: sqlite3.Connection) -> None:
    turn_id = _turn(conn)
    delivery = DeliveryStore(conn).enqueue(turn_id, "websocket", "text")
    DeliveryStore(conn).mark_failed(turn_id, delivery.delivery_id, reason="refused")
    gm_retry_delivery(conn, _CAMPAIGN, turn_id, lambda _d: True)
    assert DeliveryStore(conn).require(turn_id, delivery.delivery_id).status == (
        "delivered"
    )


# -- cancel -----------------------------------------------------------------


def test_cancelling_reports_no_committed_effects(conn: sqlite3.Connection) -> None:
    turn_id = _turn(conn)
    result = gm_cancel_turn(conn, turn_id)
    assert result["cancelled"] is True
    assert result["authoritative_effects_committed"] is False


def test_cancelling_states_when_effects_were_already_committed(
    conn: sqlite3.Connection,
) -> None:
    turn_id = _turn(conn)
    _committed(conn, turn_id)
    result = gm_cancel_turn(conn, turn_id)
    assert result["authoritative_effects_committed"] is True


def test_cancelling_a_terminal_turn_is_refused(conn: sqlite3.Connection) -> None:
    turn_id = _turn(conn)
    gm_cancel_turn(conn, turn_id)
    with pytest.raises(GmTurnError):
        gm_cancel_turn(conn, turn_id)


# -- status -----------------------------------------------------------------


def test_turn_status_reports_what_a_retry_would_do(
    conn: sqlite3.Connection,
) -> None:
    turn_id = _turn(conn)
    TurnJobStore(conn).transition(turn_id, "interpreting")
    TurnJobStore(conn).transition(turn_id, "resolving")
    status = gm_turn_status(conn, _CAMPAIGN, turn_id)
    assert status["committed_effect"] is False
    assert status["recovery"]["action"] == "retry_resolution"


def test_turn_status_reports_a_committed_turn(conn: sqlite3.Connection) -> None:
    turn_id = _turn(conn)
    TurnJobStore(conn).transition(turn_id, "interpreting")
    TurnJobStore(conn).transition(turn_id, "resolving")
    _committed(conn, turn_id)
    status = gm_turn_status(conn, _CAMPAIGN, turn_id)
    assert status["committed_effect"] is True
    assert status["recovery"]["action"] == "commit_pending"


def test_status_rejects_a_turn_from_another_campaign(
    conn: sqlite3.Connection,
) -> None:
    turn_id = _turn(conn)
    with pytest.raises(GmTurnError, match="not in campaign"):
        gm_turn_status(conn, "campaign-2", turn_id)
