"""Duplicate ingress returns one turn; illegal transitions fail closed."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.turn_job import (
    LEGAL_TRANSITIONS,
    TERMINAL_STATUSES,
    TurnJobStore,
    TurnTransitionError,
)
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "campaign-1"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "store.db")
    migrate(connection)
    CampaignStore(connection).create_campaign(_CAMPAIGN, "First", "freeform")
    yield connection
    connection.close()


@pytest.fixture
def store(conn: sqlite3.Connection) -> TurnJobStore:
    return TurnJobStore(conn)


def _in(store: TurnJobStore, **overrides: object):
    return store.claim_ingress(
        _CAMPAIGN, "Ada tries the gate", **overrides  # type: ignore[arg-type]
    )


# -- identity ---------------------------------------------------------------


def test_a_new_turn_starts_received(store: TurnJobStore) -> None:
    job = _in(store)
    assert job.status == "received"
    assert job.campaign_id == _CAMPAIGN
    assert job.input_text == "Ada tries the gate"
    assert job.is_terminal is False


def test_duplicate_native_ingress_returns_the_existing_turn(
    store: TurnJobStore,
) -> None:
    first = _in(store, channel="telegram", conversation_id="chat-1", external_message_id="99")
    second = _in(store, channel="telegram", conversation_id="chat-1", external_message_id="99")
    assert second.turn_id == first.turn_id
    assert len(store.list_for_campaign(_CAMPAIGN)) == 1


def test_equal_text_from_a_different_message_is_a_new_turn(
    store: TurnJobStore,
) -> None:
    first = _in(store, channel="telegram", conversation_id="chat-1", external_message_id="99")
    second = _in(store, channel="telegram", conversation_id="chat-1", external_message_id="100")
    assert second.turn_id != first.turn_id
    assert len(store.list_for_campaign(_CAMPAIGN)) == 2


def test_ingress_without_channel_identity_is_always_a_new_turn(
    store: TurnJobStore,
) -> None:
    _in(store)
    _in(store)
    assert len(store.list_for_campaign(_CAMPAIGN)) == 2


# -- transitions ------------------------------------------------------------


def test_a_legal_transition_writes(store: TurnJobStore) -> None:
    job = _in(store)
    moved = store.transition(job.turn_id, "interpreting")
    assert moved.status == "interpreting"


def test_an_illegal_transition_raises_and_writes_nothing(
    store: TurnJobStore,
) -> None:
    job = _in(store)
    with pytest.raises(TurnTransitionError, match="cannot move"):
        store.transition(job.turn_id, "delivered")
    assert store.require(job.turn_id).status == "received"


def test_a_terminal_turn_cannot_move(store: TurnJobStore) -> None:
    job = _in(store)
    store.transition(job.turn_id, "cancelled")
    for target in LEGAL_TRANSITIONS["cancelled"]:
        assert target not in LEGAL_TRANSITIONS["cancelled"]
    with pytest.raises(TurnTransitionError):
        store.transition(job.turn_id, "interpreting")


def test_a_transition_records_its_disposition(store: TurnJobStore) -> None:
    job = _in(store)
    moved = store.transition(job.turn_id, "interpreting", disposition="narrative")
    assert moved.disposition == "narrative"


def test_every_status_is_reachable_from_some_status() -> None:
    reachable = {target for targets in LEGAL_TRANSITIONS.values() for target in targets}
    reachable |= set(LEGAL_TRANSITIONS)
    assert reachable == set(LEGAL_TRANSITIONS)


def test_no_status_transitions_to_itself() -> None:
    for status, targets in LEGAL_TRANSITIONS.items():
        assert status not in targets, status


# -- awaiting states --------------------------------------------------------


def test_a_turn_can_wait_for_a_player_then_resume(store: TurnJobStore) -> None:
    job = _in(store)
    store.transition(job.turn_id, "interpreting")
    store.transition(job.turn_id, "awaiting_player")
    store.transition(job.turn_id, "interpreting")
    store.transition(job.turn_id, "resolving")
    assert store.require(job.turn_id).status == "resolving"


def test_a_turn_can_wait_for_the_gm(store: TurnJobStore) -> None:
    job = _in(store)
    store.transition(job.turn_id, "interpreting")
    store.transition(job.turn_id, "awaiting_gm")
    assert store.require(job.turn_id).status == "awaiting_gm"


# -- leases -----------------------------------------------------------------


def _expiry(seconds: int) -> str:
    return (
        (datetime.now(timezone.utc) + timedelta(seconds=seconds))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def test_a_lease_is_acquired_and_released(store: TurnJobStore) -> None:
    job = _in(store)
    leased = store.acquire_lease(job.turn_id, "worker-1", expires_at=_expiry(60))
    assert leased.lease_owner == "worker-1"
    assert store.release_lease(job.turn_id).lease_owner is None


def test_a_live_lease_cannot_be_stolen(store: TurnJobStore) -> None:
    job = _in(store)
    store.acquire_lease(job.turn_id, "worker-1", expires_at=_expiry(60))
    with pytest.raises(TurnTransitionError, match="leased to"):
        store.acquire_lease(job.turn_id, "worker-2", expires_at=_expiry(60))


def test_an_expired_lease_can_be_taken_over(store: TurnJobStore) -> None:
    """A crash leaves a lease behind; recovery needs to be able to take it."""
    job = _in(store)
    store.acquire_lease(job.turn_id, "worker-1", expires_at=_expiry(-1))
    taken = store.acquire_lease(job.turn_id, "worker-2", expires_at=_expiry(60))
    assert taken.lease_owner == "worker-2"


def test_the_same_owner_may_reacquire_its_own_lease(store: TurnJobStore) -> None:
    job = _in(store)
    store.acquire_lease(job.turn_id, "worker-1", expires_at=_expiry(60))
    again = store.acquire_lease(job.turn_id, "worker-1", expires_at=_expiry(120))
    assert again.lease_owner == "worker-1"


# -- action effect claims ---------------------------------------------------


def test_claiming_an_action_creates_one_claim(store: TurnJobStore) -> None:
    job = _in(store)
    assert store.claim_action(job.turn_id, 0, "check") == "claimed"
    claims = store.action_claims(job.turn_id)
    assert len(claims) == 1
    assert claims[0]["status"] == "claimed"
    assert claims[0]["event_sequence"] is None


def test_reclaiming_the_same_slot_returns_the_existing_status(
    store: TurnJobStore,
) -> None:
    job = _in(store)
    store.claim_action(job.turn_id, 0, "check")
    store.commit_action(job.turn_id, 0, event_sequence=7)
    # A recovery pass must read the ledger, not rerun the action.
    assert store.claim_action(job.turn_id, 0, "check") == "committed"
    assert len(store.action_claims(job.turn_id)) == 1


def test_a_committed_effect_is_recoverable_after_a_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "restart.db"
    conn = connect(path)
    migrate(conn)
    CampaignStore(conn).create_campaign(_CAMPAIGN, "First", "freeform")
    store = TurnJobStore(conn)
    job = store.claim_ingress(_CAMPAIGN, "Ada tries the gate")
    store.claim_action(job.turn_id, 0, "check")
    store.commit_action(job.turn_id, 0, event_sequence=7)
    turn_id = job.turn_id
    conn.close()

    reopened = connect(path)
    try:
        recovered = TurnJobStore(reopened)
        assert recovered.has_committed_effect(turn_id) is True
        assert recovered.action_claims(turn_id)[0]["event_sequence"] == 7
    finally:
        reopened.close()


def test_several_actions_in_one_turn_get_several_claims(
    store: TurnJobStore,
) -> None:
    job = _in(store)
    store.claim_action(job.turn_id, 0, "check")
    store.claim_action(job.turn_id, 1, "apply_damage")
    assert len(store.action_claims(job.turn_id)) == 2


def test_a_narrative_turn_records_no_applicable_action(
    store: TurnJobStore,
) -> None:
    job = _in(store)
    store.claim_action(job.turn_id, 0, "")
    store.mark_action_not_applicable(job.turn_id, 0)
    assert store.action_claims(job.turn_id)[0]["status"] == "not_applicable"
    assert store.has_committed_effect(job.turn_id) is False


def test_committing_an_unclaimed_action_is_rejected(store: TurnJobStore) -> None:
    job = _in(store)
    with pytest.raises(LookupError):
        store.commit_action(job.turn_id, 0, event_sequence=7)


def test_a_turn_with_no_claims_has_no_committed_effect(
    store: TurnJobStore,
) -> None:
    job = _in(store)
    assert store.has_committed_effect(job.turn_id) is False


def test_listing_filters_by_status(store: TurnJobStore) -> None:
    first = _in(store)
    second = _in(store)
    store.transition(second.turn_id, "cancelled")
    assert [j.turn_id for j in store.list_for_campaign(_CAMPAIGN, status="received")] == [
        first.turn_id
    ]


# -- untrusted channel identity --------------------------------------------


def test_a_blank_message_id_creates_distinct_turns(
    conn: sqlite3.Connection,
) -> None:
    """An empty string is not NULL, so it would collide on the partial index.

    ``claim_ingress`` skipped the duplicate lookup because the value was
    falsy, then raised IntegrityError on insert. A channel with no usable
    identity must simply produce a new turn each time.
    """
    store = TurnJobStore(conn)
    first = store.claim_ingress(
        _CAMPAIGN, "first", channel="irc", conversation_id="#chan",
        external_message_id="",
    )
    second = store.claim_ingress(
        _CAMPAIGN, "second", channel="irc", conversation_id="#chan",
        external_message_id="",
    )
    assert first.turn_id != second.turn_id
    assert store.require(first.turn_id).external_message_id is None


def test_a_whitespace_message_id_is_treated_as_absent(conn: sqlite3.Connection) -> None:
    store = TurnJobStore(conn)
    turn = store.claim_ingress(
        _CAMPAIGN, "hi", channel="irc", external_message_id="   "
    )
    assert store.require(turn.turn_id).external_message_id is None


def test_a_real_message_id_still_deduplicates(conn: sqlite3.Connection) -> None:
    store = TurnJobStore(conn)
    first = store.claim_ingress(
        _CAMPAIGN, "x", channel="t", conversation_id="a", external_message_id="42"
    )
    second = store.claim_ingress(
        _CAMPAIGN, "x", channel="t", conversation_id="a", external_message_id="42"
    )
    assert first.turn_id == second.turn_id


def test_an_oversized_message_id_is_refused(conn: sqlite3.Connection) -> None:
    store = TurnJobStore(conn)
    with pytest.raises(ValueError, match="at most"):
        store.claim_ingress(
            _CAMPAIGN, "x", channel="t", external_message_id="X" * 5000
        )


def test_an_oversized_input_text_is_refused(conn: sqlite3.Connection) -> None:
    """Channel text is untrusted; one huge message must not fill the database."""
    store = TurnJobStore(conn)
    with pytest.raises(ValueError, match="at most"):
        store.claim_ingress(_CAMPAIGN, "Z" * 70_000, channel="t", external_message_id="1")
