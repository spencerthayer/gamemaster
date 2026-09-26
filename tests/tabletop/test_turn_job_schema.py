"""Durable turn identity: one native message, one turn, one effect per ordinal."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "campaign-1"

STATUSES = (
    "received",
    "interpreting",
    "awaiting_player",
    "awaiting_gm",
    "resolving",
    "narrating",
    "completed",
    "delivery_pending",
    "delivered",
    "failed",
    "cancelled",
)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "turns.db")
    migrate(connection)
    CampaignStore(connection).create_campaign(_CAMPAIGN, "First", "freeform")
    yield connection
    connection.close()


def _turn(conn: sqlite3.Connection, turn_id: str = "turn-1", **overrides: object) -> None:
    columns = {
        "turn_id": turn_id,
        "campaign_id": _CAMPAIGN,
        "status": "received",
        "input_text": "Ada tries the gate",
        "created_at": "2026-09-25T00:00:00Z",
        "updated_at": "2026-09-25T00:00:00Z",
    }
    columns.update(overrides)
    conn.execute(
        "INSERT INTO turn_jobs "
        "(turn_id, campaign_id, status, input_text, created_at, updated_at, "
        "channel, conversation_id, external_message_id, resumes_turn_id, principal_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            columns["turn_id"],
            columns["campaign_id"],
            columns["status"],
            columns["input_text"],
            columns["created_at"],
            columns["updated_at"],
            overrides.get("channel"),
            overrides.get("conversation_id"),
            overrides.get("external_message_id"),
            overrides.get("resumes_turn_id"),
            overrides.get("principal_id"),
        ),
    )


def test_turn_jobs_table_exists_with_every_field(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(turn_jobs)").fetchall()
    }
    assert {
        "turn_id", "campaign_id", "session_id", "scene_id", "status",
        "channel", "conversation_id", "external_message_id",
        "resumes_turn_id", "principal_id", "input_text", "disposition",
        "failure_reason", "lease_owner", "lease_expires_at",
        "created_at", "updated_at",
    } <= columns


@pytest.mark.parametrize("status", STATUSES)
def test_every_declared_status_is_accepted(conn: sqlite3.Connection, status: str) -> None:
    _turn(conn, f"turn-{status}", status=status)
    row = conn.execute(
        "SELECT status FROM turn_jobs WHERE turn_id = ?", (f"turn-{status}",)
    ).fetchone()
    assert row["status"] == status


def test_an_unknown_status_is_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _turn(conn, "turn-bad", status="pondering")


def test_duplicate_native_message_identity_is_rejected(conn: sqlite3.Connection) -> None:
    _turn(
        conn, "turn-1",
        channel="telegram", conversation_id="chat-1", external_message_id="99",
    )
    with pytest.raises(sqlite3.IntegrityError):
        _turn(
            conn, "turn-2",
            channel="telegram", conversation_id="chat-1", external_message_id="99",
        )


def test_the_same_message_id_on_another_channel_is_a_different_turn(
    conn: sqlite3.Connection,
) -> None:
    _turn(
        conn, "turn-1",
        channel="telegram", conversation_id="chat-1", external_message_id="99",
    )
    _turn(
        conn, "turn-2",
        channel="slack", conversation_id="chat-1", external_message_id="99",
    )
    assert conn.execute("SELECT COUNT(*) FROM turn_jobs").fetchone()[0] == 2


def test_turns_without_channel_identity_do_not_collide(
    conn: sqlite3.Connection,
) -> None:
    _turn(conn, "turn-1")
    _turn(conn, "turn-2")
    assert conn.execute("SELECT COUNT(*) FROM turn_jobs").fetchone()[0] == 2


def test_a_resumed_turn_can_point_at_its_parent(conn: sqlite3.Connection) -> None:
    _turn(conn, "turn-1")
    _turn(
        conn, "turn-2",
        status="awaiting_player",
        resumes_turn_id="turn-1",
    )
    row = conn.execute(
        "SELECT resumes_turn_id FROM turn_jobs WHERE turn_id = 'turn-2'"
    ).fetchone()
    assert row["resumes_turn_id"] == "turn-1"


def test_action_effect_claims_are_unique_per_turn_and_ordinal(
    conn: sqlite3.Connection,
) -> None:
    _turn(conn)
    conn.execute(
        "INSERT INTO turn_action_effects "
        "(turn_id, ordinal, action_type, status, claimed_at) "
        "VALUES ('turn-1', 0, 'check', 'claimed', '2026-09-25T00:00:00Z')",
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO turn_action_effects "
            "(turn_id, ordinal, action_type, status, claimed_at) "
            "VALUES ('turn-1', 0, 'check', 'claimed', '2026-09-25T00:01:00Z')",
        )


def test_two_ordinals_in_one_turn_are_allowed(conn: sqlite3.Connection) -> None:
    _turn(conn)
    for ordinal in (0, 1):
        conn.execute(
            "INSERT INTO turn_action_effects "
            "(turn_id, ordinal, action_type, status, claimed_at) "
            "VALUES ('turn-1', ?, 'check', 'claimed', '2026-09-25T00:00:00Z')",
            (ordinal,),
        )
    assert conn.execute("SELECT COUNT(*) FROM turn_action_effects").fetchone()[0] == 2


def test_an_effect_claim_records_its_committed_event(conn: sqlite3.Connection) -> None:
    _turn(conn)
    conn.execute(
        "INSERT INTO turn_action_effects "
        "(turn_id, ordinal, action_type, status, claimed_at, committed_at, event_sequence) "
        "VALUES ('turn-1', 0, 'check', 'committed', '2026-09-25T00:00:00Z', "
        "'2026-09-25T00:00:01Z', 7)",
    )
    row = conn.execute(
        "SELECT status, event_sequence FROM turn_action_effects WHERE turn_id = 'turn-1'"
    ).fetchone()
    assert row["status"] == "committed"
    assert row["event_sequence"] == 7


def test_an_unknown_effect_status_is_rejected(conn: sqlite3.Connection) -> None:
    _turn(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO turn_action_effects "
            "(turn_id, ordinal, action_type, status, claimed_at) "
            "VALUES ('turn-1', 0, 'check', 'pending', '2026-09-25T00:00:00Z')",
        )


def test_a_turn_cannot_reference_a_campaign_that_does_not_exist(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _turn(conn, campaign_id="missing")


def test_deleting_a_campaign_deletes_its_turns(conn: sqlite3.Connection) -> None:
    _turn(conn)
    CampaignStore(conn).archive_campaign(_CAMPAIGN)
    conn.execute("DELETE FROM campaigns WHERE campaign_id = ?", (_CAMPAIGN,))
    assert conn.execute("SELECT COUNT(*) FROM turn_jobs").fetchone()[0] == 0
