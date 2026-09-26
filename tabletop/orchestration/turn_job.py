"""Durable turn jobs: identity, legal transitions, leases, and effect claims.

The store is the only writer of ``turn_jobs`` and ``turn_action_effects``.
Duplicate ingress returns the existing turn rather than creating a second one,
which is what makes a channel retry safe.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from tabletop.storage.sqlite import transaction

#: The declared turn lifecycle. A transition absent from this map is illegal
#: and fails closed rather than being written.
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "received": frozenset({"interpreting", "cancelled", "failed"}),
    "interpreting": frozenset(
        {"awaiting_player", "awaiting_gm", "resolving", "narrating", "cancelled", "failed"}
    ),
    "awaiting_player": frozenset({"interpreting", "cancelled", "failed"}),
    "awaiting_gm": frozenset({"interpreting", "cancelled", "failed"}),
    "resolving": frozenset({"narrating", "awaiting_gm", "failed", "cancelled"}),
    "narrating": frozenset({"completed", "delivery_pending", "failed", "cancelled"}),
    "completed": frozenset({"delivery_pending", "delivered", "failed"}),
    "delivery_pending": frozenset({"delivered", "failed"}),
    "delivered": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

TERMINAL_STATUSES = frozenset({"delivered", "failed", "cancelled"})


class TurnTransitionError(RuntimeError):
    """A turn was moved to a status it cannot legally reach."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class TurnJob:
    """One durable turn."""

    turn_id: str
    campaign_id: str
    status: str
    input_text: str
    created_at: str
    updated_at: str
    session_id: str | None = None
    scene_id: str | None = None
    channel: str | None = None
    conversation_id: str | None = None
    external_message_id: str | None = None
    resumes_turn_id: str | None = None
    principal_id: str | None = None
    disposition: str | None = None
    failure_reason: str | None = None
    lease_owner: str | None = None
    lease_expires_at: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


class TurnJobStore:
    """Read and write durable turn jobs and their effect claims."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def claim_ingress(
        self,
        campaign_id: str,
        input_text: str,
        *,
        channel: str | None = None,
        conversation_id: str | None = None,
        external_message_id: str | None = None,
        principal_id: str | None = None,
        session_id: str | None = None,
        scene_id: str | None = None,
        resumes_turn_id: str | None = None,
    ) -> TurnJob:
        """Claim one inbound message, or return the turn that already owns it.

        A retry of the same native message returns the original turn with no
        second claim. Without channel identity every call is a new turn, since
        there is nothing to deduplicate on.
        """
        if channel and external_message_id:
            existing = self.find_by_ingress(
                campaign_id, channel, conversation_id, external_message_id
            )
            if existing is not None:
                return existing

        turn_id = uuid.uuid4().hex
        now = _now()
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO turn_jobs "
                "(turn_id, campaign_id, session_id, scene_id, status, channel, "
                "conversation_id, external_message_id, resumes_turn_id, principal_id, "
                "input_text, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'received', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    turn_id,
                    campaign_id,
                    session_id,
                    scene_id,
                    channel,
                    conversation_id,
                    external_message_id,
                    resumes_turn_id,
                    principal_id,
                    input_text,
                    now,
                    now,
                ),
            )
        return self.require(turn_id)

    def find_by_ingress(
        self,
        campaign_id: str,
        channel: str,
        conversation_id: str | None,
        external_message_id: str,
    ) -> TurnJob | None:
        row = self.conn.execute(
            "SELECT turn_id FROM turn_jobs "
            "WHERE campaign_id = ? AND channel = ? AND conversation_id IS ? "
            "AND external_message_id = ?",
            (campaign_id, channel, conversation_id, external_message_id),
        ).fetchone()
        return None if row is None else self.get(str(row["turn_id"]))

    def get(self, turn_id: str) -> TurnJob | None:
        row = self.conn.execute(
            "SELECT * FROM turn_jobs WHERE turn_id = ?", (turn_id,)
        ).fetchone()
        return None if row is None else _job_from_row(row)

    def require(self, turn_id: str) -> TurnJob:
        job = self.get(turn_id)
        if job is None:
            raise LookupError(f"turn not found: {turn_id}")
        return job

    def list_for_campaign(
        self, campaign_id: str, *, status: str | None = None
    ) -> tuple[TurnJob, ...]:
        if status is None:
            rows = self.conn.execute(
                "SELECT turn_id FROM turn_jobs WHERE campaign_id = ? ORDER BY created_at",
                (campaign_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT turn_id FROM turn_jobs WHERE campaign_id = ? AND status = ? "
                "ORDER BY created_at",
                (campaign_id, status),
            ).fetchall()
        return tuple(self.get(str(row["turn_id"])) for row in rows)  # type: ignore[misc]

    def transition(
        self,
        turn_id: str,
        status: str,
        *,
        disposition: str | None = None,
        failure_reason: str | None = None,
    ) -> TurnJob:
        """Move a turn to a status the lifecycle allows.

        An illegal transition raises rather than writing, so a bad recovery
        decision cannot quietly rewrite history.
        """
        job = self.require(turn_id)
        allowed = LEGAL_TRANSITIONS.get(job.status, frozenset())
        if status not in allowed:
            raise TurnTransitionError(
                f"turn {turn_id} cannot move from {job.status!r} to {status!r}; "
                f"allowed: {sorted(allowed)}"
            )
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE turn_jobs SET status = ?, disposition = COALESCE(?, disposition), "
                "failure_reason = COALESCE(?, failure_reason), updated_at = ? "
                "WHERE turn_id = ?",
                (status, disposition, failure_reason, _now(), turn_id),
            )
        return self.require(turn_id)

    def acquire_lease(
        self, turn_id: str, owner: str, *, expires_at: str
    ) -> TurnJob:
        """Claim a turn for processing. A live lease is not stealable."""
        job = self.require(turn_id)
        if job.lease_owner is not None and job.lease_owner != owner and not _expired(
            job.lease_expires_at
        ):
            raise TurnTransitionError(
                f"turn {turn_id} is leased to {job.lease_owner!r} until "
                f"{job.lease_expires_at}"
            )
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE turn_jobs SET lease_owner = ?, lease_expires_at = ?, updated_at = ? "
                "WHERE turn_id = ?",
                (owner, expires_at, _now(), turn_id),
            )
        return self.require(turn_id)

    def release_lease(self, turn_id: str) -> TurnJob:
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE turn_jobs SET lease_owner = NULL, lease_expires_at = NULL, "
                "updated_at = ? WHERE turn_id = ?",
                (_now(), turn_id),
            )
        return self.require(turn_id)

    # -- action effect claims --------------------------------------------

    def claim_action(self, turn_id: str, ordinal: int, action_type: str) -> str:
        """Claim one action slot. Re-claiming the same slot returns the claim.

        The ledger entry existing is the proof an effect was committed once, so
        a recovery pass reads it rather than rerunning the action.
        """
        with transaction(self.conn):
            existing = self.conn.execute(
                "SELECT status FROM turn_action_effects WHERE turn_id = ? AND ordinal = ?",
                (turn_id, ordinal),
            ).fetchone()
            if existing is not None:
                return str(existing["status"])
            self.conn.execute(
                "INSERT INTO turn_action_effects "
                "(turn_id, ordinal, action_type, status, claimed_at) "
                "VALUES (?, ?, ?, 'claimed', ?)",
                (turn_id, ordinal, action_type, _now()),
            )
        return "claimed"

    def commit_action(
        self, turn_id: str, ordinal: int, *, event_sequence: int
    ) -> None:
        with transaction(self.conn):
            cursor = self.conn.execute(
                "UPDATE turn_action_effects SET status = 'committed', "
                "committed_at = ?, event_sequence = ? WHERE turn_id = ? AND ordinal = ?",
                (_now(), event_sequence, turn_id, ordinal),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"no action claim for turn {turn_id} ordinal {ordinal}")

    def mark_action_not_applicable(self, turn_id: str, ordinal: int) -> None:
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE turn_action_effects SET status = 'not_applicable', "
                "committed_at = ? WHERE turn_id = ? AND ordinal = ?",
                (_now(), turn_id, ordinal),
            )

    def action_claims(self, turn_id: str) -> tuple[dict[str, Any], ...]:
        rows = self.conn.execute(
            "SELECT turn_id, ordinal, action_type, status, event_sequence, "
            "claimed_at, committed_at FROM turn_action_effects "
            "WHERE turn_id = ? ORDER BY ordinal",
            (turn_id,),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def has_committed_effect(self, turn_id: str) -> bool:
        """True when any action in this turn already committed an effect."""
        row = self.conn.execute(
            "SELECT 1 FROM turn_action_effects "
            "WHERE turn_id = ? AND status = 'committed' LIMIT 1",
            (turn_id,),
        ).fetchone()
        return row is not None


def _expired(timestamp: str | None) -> bool:
    if timestamp is None:
        return True
    return timestamp <= _now()


def _job_from_row(row: sqlite3.Row) -> TurnJob:
    return TurnJob(
        turn_id=str(row["turn_id"]),
        campaign_id=str(row["campaign_id"]),
        status=str(row["status"]),
        input_text=str(row["input_text"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        session_id=row["session_id"],
        scene_id=row["scene_id"],
        channel=row["channel"],
        conversation_id=row["conversation_id"],
        external_message_id=row["external_message_id"],
        resumes_turn_id=row["resumes_turn_id"],
        principal_id=row["principal_id"],
        disposition=row["disposition"],
        failure_reason=row["failure_reason"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
    )
