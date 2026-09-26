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
from typing import Any

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


#: Channel-supplied text is untrusted and unbounded. A cap keeps one oversized
#: message from writing megabytes into the campaign database per turn.
MAX_INPUT_TEXT_LENGTH = 64_000

#: A native message id is a short opaque token. Anything longer is not an id.
MAX_IDENTITY_LENGTH = 512


def _clean_identity(value, field_name):
    """Normalize a channel identity field to a bounded, non-blank string.

    A blank value becomes NULL. An empty string is not NULL, so it would
    defeat the partial unique index's intent and collide with a real message
    while skipping the duplicate lookup.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None")
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > MAX_IDENTITY_LENGTH:
        raise ValueError(
            f"{field_name} must be at most {MAX_IDENTITY_LENGTH} characters"
        )
    return stripped


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
        second claim. A channel that supplies no usable identity produces a
        new turn every time, since there is nothing to deduplicate on.

        Identity fields are normalized and bounded here rather than trusted:
        a blank id is stored as NULL, because an empty string is not NULL and
        would collide with the partial unique index while skipping the
        duplicate lookup, raising IntegrityError instead of creating a turn.
        """
        external_message_id = _clean_identity(external_message_id, "external_message_id")
        conversation_id = _clean_identity(conversation_id, "conversation_id")
        channel = _clean_identity(channel, "channel")
        principal_id = _clean_identity(principal_id, "principal_id")
        if not isinstance(input_text, str):
            raise TypeError("input_text must be a string")
        if len(input_text) > MAX_INPUT_TEXT_LENGTH:
            raise ValueError(
                f"input_text must be at most {MAX_INPUT_TEXT_LENGTH} characters"
            )

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
            return self.claim_action_in_transaction(turn_id, ordinal, action_type)

    def claim_action_in_transaction(
        self, turn_id: str, ordinal: int, action_type: str
    ) -> str:
        """Claim one action slot inside a caller-owned transaction.

        The effect's claim must land in the same transaction as the event and
        state changes it describes, so a rollback cannot leave a claim
        describing an effect that does not exist.
        """
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
            self.commit_action_in_transaction(
                turn_id, ordinal, event_sequence=event_sequence
            )

    def commit_action_in_transaction(
        self, turn_id: str, ordinal: int, *, event_sequence: int
    ) -> None:
        """Commit a claim inside a caller-owned transaction."""
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


#: Statuses a recovery pass may safely restart. A turn mid-delivery is not one
#: of them: its output may already have reached the channel.
RESUMABLE = frozenset({"received", "interpreting", "awaiting_player", "awaiting_gm"})


@dataclass(frozen=True)
class RecoveryDecision:
    """What a recovery pass should do with one turn.

    ``can_retry_resolution`` is decided by whether an authoritative effect is
    already committed, never by how long the turn has been stuck. A turn that
    crashed after committing is old and still not retryable; a turn that
    crashed before committing is new and already retryable.
    """

    turn_id: str
    action: str
    reason: str
    committed_event_sequences: tuple[int, ...] = ()

    @property
    def can_retry_resolution(self) -> bool:
        return self.action == "retry_resolution"

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "action": self.action,
            "reason": self.reason,
            "committed_event_sequences": list(self.committed_event_sequences),
        }


def committed_event_sequences(store: TurnJobStore, turn_id: str) -> tuple[int, ...]:
    return tuple(
        int(claim["event_sequence"])
        for claim in store.action_claims(turn_id)
        if claim["status"] == "committed" and claim["event_sequence"] is not None
    )


def decide_recovery(store: TurnJobStore, turn_id: str) -> RecoveryDecision:
    """Classify one turn for a recovery pass.

    Event presence decides, not wall-clock age. Age alone cannot distinguish a
    turn that crashed before committing from one that crashed after, and
    rerunning the latter would apply a mechanical effect twice.
    """
    job = store.require(turn_id)
    committed = committed_event_sequences(store, turn_id)

    if job.status in TERMINAL_STATUSES:
        return RecoveryDecision(
            turn_id, "none", f"turn is already {job.status}", committed
        )

    if job.status == "narrating":
        # The action already resolved; only the narration is missing.
        return RecoveryDecision(
            turn_id, "retry_narration", "resolution is committed; narration pending", committed
        )

    # A committed effect outranks the status. A turn that crashed between
    # committing and updating its own status still looks like `interpreting`,
    # and classifying it from the status alone would rerun the action.
    if committed:
        return RecoveryDecision(
            turn_id, "commit_pending", "an action effect is already committed", committed
        )

    if job.status == "resolving":
        return RecoveryDecision(
            turn_id, "retry_resolution", "no action effect was committed", ()
        )

    # Delivery work outranks the committed-effect check. A resolved turn
    # always commits an effect, so testing evidence first would classify
    # every pending output as commit work and the resend would never happen.
    if job.status in ("completed", "delivery_pending"):
        return RecoveryDecision(
            turn_id, "retry_delivery", "output is stored and awaits delivery", committed
        )

    # A committed effect outranks the remaining statuses. A turn that crashed
    # between committing and updating its own state still reads as
    # `interpreting`, and classifying it from the status alone would rerun the
    # action.
    if committed:
        return RecoveryDecision(
            turn_id, "commit_pending", "an action effect is already committed", committed
        )

    if job.status in RESUMABLE:
        return RecoveryDecision(
            turn_id, "retry_resolution", f"turn is {job.status} with no committed effect", committed
        )

    return RecoveryDecision(turn_id, "none", f"turn is {job.status}", committed)


def explain_turn(
    conn: sqlite3.Connection, turn_id: str
) -> dict[str, Any]:
    """Assemble one turn's audit record from durable records.

    Read-only. Anything the log does not record is reported as absent rather
    than reconstructed from chat history.
    """
    from tabletop.orchestration.delivery import DeliveryStore, GenerationReceiptStore

    store = TurnJobStore(conn)
    job = store.require(turn_id)
    return {
        "turn": {
            "turn_id": job.turn_id,
            "campaign_id": job.campaign_id,
            "status": job.status,
            "input_text": job.input_text,
            "disposition": job.disposition,
            "session_id": job.session_id,
            "scene_id": job.scene_id,
            "channel": job.channel,
            "conversation_id": job.conversation_id,
            "external_message_id": job.external_message_id,
            "resumes_turn_id": job.resumes_turn_id,
            "principal_id": job.principal_id,
            "failure_reason": job.failure_reason,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        },
        "action_effects": [dict(claim) for claim in store.action_claims(turn_id)],
        "generations": [
            receipt.to_dict() for receipt in GenerationReceiptStore(conn).list_for_turn(turn_id)
        ],
        "deliveries": [
            {
                "delivery_id": d.delivery_id,
                "channel": d.channel,
                "segment": d.segment,
                "status": d.status,
                "attempts": d.attempts,
                "remote_ack": d.remote_ack,
                "last_error": d.last_error,
            }
            for d in DeliveryStore(conn).list_for_turn(turn_id)
        ],
        "recovery": decide_recovery(store, turn_id).to_dict(),
    }
