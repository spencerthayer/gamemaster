"""Turn-linked generation receipts and a durable delivery outbox.

Neither is campaign canon. They record what happened operationally so a turn
can be explained and a delivery retried without regenerating it.

The delivery guarantee is transport-honest. A channel that supplies a stable
idempotency key gets one; one that does not moves to ``ambiguous`` after an
uncertain send rather than claiming a delivery this system cannot prove.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from tabletop.storage.sqlite import transaction

#: The only status from which a retry is safe: nothing was sent.
RETRYABLE = frozenset({"pending", "failed"})


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class GenerationReceipt:
    """One model generation performed during a turn."""

    receipt_id: str
    turn_id: str
    ordinal: int
    created_at: str
    provider: str | None = None
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    prompt_sha256: str | None = None
    response_sha256: str | None = None
    output_segments: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "turn_id": self.turn_id,
            "ordinal": self.ordinal,
            "created_at": self.created_at,
            "provider": self.provider,
            "model": self.model,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "latency_ms": self.latency_ms,
            "prompt_sha256": self.prompt_sha256,
            "response_sha256": self.response_sha256,
            "output_segments": list(self.output_segments),
        }


@dataclass(frozen=True)
class Delivery:
    """One output segment waiting to, or having reached, a channel."""

    delivery_id: str
    turn_id: str
    channel: str
    segment: int
    text: str
    status: str
    attempts: int
    created_at: str
    updated_at: str
    conversation_id: str | None = None
    client_seq: int | None = None
    remote_ack: str | None = None
    last_error: str | None = None

    @property
    def is_retryable(self) -> bool:
        """True when nothing was sent, so a retry cannot duplicate."""
        return self.status in RETRYABLE


class DeliveryStore:
    """Read and write turn deliveries."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def enqueue(
        self,
        turn_id: str,
        channel: str,
        text: str,
        *,
        segment: int = 0,
        conversation_id: str | None = None,
    ) -> Delivery:
        """Queue one output segment. The delivery id is stable across retries."""
        delivery_id = uuid.uuid4().hex
        now = _now()
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO turn_deliveries "
                "(delivery_id, turn_id, channel, conversation_id, segment, text, "
                "status, attempts, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)",
                (delivery_id, turn_id, channel, conversation_id, segment, text, now, now),
            )
        return self.require(turn_id, delivery_id)

    def get(self, turn_id: str, delivery_id: str) -> Delivery | None:
        row = self.conn.execute(
            "SELECT * FROM turn_deliveries WHERE turn_id = ? AND delivery_id = ?",
            (turn_id, delivery_id),
        ).fetchone()
        return None if row is None else _delivery_from_row(row)

    def require(self, turn_id: str, delivery_id: str) -> Delivery:
        delivery = self.get(turn_id, delivery_id)
        if delivery is None:
            raise LookupError(f"delivery not found: {delivery_id}")
        return delivery

    def list_for_turn(self, turn_id: str) -> tuple[Delivery, ...]:
        rows = self.conn.execute(
            "SELECT * FROM turn_deliveries WHERE turn_id = ? ORDER BY segment",
            (turn_id,),
        ).fetchall()
        return tuple(_delivery_from_row(row) for row in rows)

    def list_by_status(self, status: str) -> tuple[Delivery, ...]:
        rows = self.conn.execute(
            "SELECT * FROM turn_deliveries WHERE status = ? ORDER BY created_at, segment",
            (status,),
        ).fetchall()
        return tuple(_delivery_from_row(row) for row in rows)

    def mark_sending(self, turn_id: str, delivery_id: str, *, client_seq: int | None) -> Delivery:
        """Record that a send is about to happen, before it does.

        Writing this first is what makes a crash mid-send recoverable: the
        row is no longer ``pending``, so recovery knows the outcome is unknown.
        """
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE turn_deliveries SET status = 'sending', attempts = attempts + 1, "
                "client_seq = COALESCE(?, client_seq), updated_at = ? "
                "WHERE turn_id = ? AND delivery_id = ?",
                (client_seq, _now(), turn_id, delivery_id),
            )
        return self.require(turn_id, delivery_id)

    def mark_delivered(
        self, turn_id: str, delivery_id: str, *, remote_ack: str | None = None
    ) -> Delivery:
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE turn_deliveries SET status = 'delivered', remote_ack = ?, "
                "updated_at = ? WHERE turn_id = ? AND delivery_id = ?",
                (remote_ack, _now(), turn_id, delivery_id),
            )
        return self.require(turn_id, delivery_id)

    def mark_ambiguous(
        self, turn_id: str, delivery_id: str, *, reason: str
    ) -> Delivery:
        """Record an uncertain send on a transport with no idempotency key."""
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE turn_deliveries SET status = 'ambiguous', last_error = ?, "
                "updated_at = ? WHERE turn_id = ? AND delivery_id = ?",
                (reason, _now(), turn_id, delivery_id),
            )
        return self.require(turn_id, delivery_id)

    def mark_failed(
        self, turn_id: str, delivery_id: str, *, reason: str
    ) -> Delivery:
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE turn_deliveries SET status = 'failed', last_error = ?, "
                "updated_at = ? WHERE turn_id = ? AND delivery_id = ?",
                (reason, _now(), turn_id, delivery_id),
            )
        return self.require(turn_id, delivery_id)


class GenerationReceiptStore:
    """Read and write turn-linked generation receipts."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record(
        self,
        turn_id: str,
        ordinal: int,
        *,
        provider: str | None = None,
        model: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        latency_ms: int | None = None,
        prompt_text: str | None = None,
        response_text: str | None = None,
        output_segments: Sequence[str] = (),
    ) -> GenerationReceipt:
        receipt_id = uuid.uuid4().hex
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO generation_receipts "
                "(receipt_id, turn_id, ordinal, created_at, provider, model, "
                "tokens_in, tokens_out, latency_ms, prompt_sha256, response_sha256, "
                "output_segments) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    receipt_id,
                    turn_id,
                    ordinal,
                    _now(),
                    provider,
                    model,
                    tokens_in,
                    tokens_out,
                    latency_ms,
                    _sha256(prompt_text),
                    _sha256(response_text),
                    _segments_json(output_segments),
                ),
            )
        return self.require(turn_id, ordinal)

    def require(self, turn_id: str, ordinal: int) -> GenerationReceipt:
        row = self.conn.execute(
            "SELECT * FROM generation_receipts WHERE turn_id = ? AND ordinal = ?",
            (turn_id, ordinal),
        ).fetchone()
        if row is None:
            raise LookupError(f"no generation receipt for turn {turn_id} ordinal {ordinal}")
        return _receipt_from_row(row)

    def list_for_turn(self, turn_id: str) -> tuple[GenerationReceipt, ...]:
        rows = self.conn.execute(
            "SELECT * FROM generation_receipts WHERE turn_id = ? ORDER BY ordinal",
            (turn_id,),
        ).fetchall()
        return tuple(_receipt_from_row(row) for row in rows)


def _sha256(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _segments_json(segments: Sequence[str]) -> str:
    import json

    return json.dumps(list(segments), separators=(",", ":"))


def _receipt_from_row(row: sqlite3.Row) -> GenerationReceipt:
    import json

    try:
        segments = tuple(json.loads(row["output_segments"]))
    except (TypeError, ValueError):
        segments = ()
    return GenerationReceipt(
        receipt_id=str(row["receipt_id"]),
        turn_id=str(row["turn_id"]),
        ordinal=int(row["ordinal"]),
        created_at=str(row["created_at"]),
        provider=row["provider"],
        model=row["model"],
        tokens_in=row["tokens_in"],
        tokens_out=row["tokens_out"],
        latency_ms=row["latency_ms"],
        prompt_sha256=row["prompt_sha256"],
        response_sha256=row["response_sha256"],
        output_segments=segments,
    )


def _delivery_from_row(row: sqlite3.Row) -> Delivery:
    return Delivery(
        delivery_id=str(row["delivery_id"]),
        turn_id=str(row["turn_id"]),
        channel=str(row["channel"]),
        segment=int(row["segment"]),
        text=str(row["text"]),
        status=str(row["status"]),
        attempts=int(row["attempts"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        conversation_id=row["conversation_id"],
        client_seq=row["client_seq"],
        remote_ack=row["remote_ack"],
        last_error=row["last_error"],
    )


#: Bounded retry. Past this the delivery is reported, not retried forever: a
#: channel that is down should not spin a worker.
MAX_DELIVERY_ATTEMPTS = 5


@dataclass(frozen=True)
class DeliveryOutcome:
    """What one delivery attempt did."""

    delivery_id: str
    status: str
    attempts: int
    duplicate_possible: bool = False


def recover_deliveries(
    conn: sqlite3.Connection,
    send: Callable[[Delivery], bool],
    *,
    max_attempts: int = MAX_DELIVERY_ATTEMPTS,
) -> list[DeliveryOutcome]:
    """Retry every deliverable segment, in per-turn order.

    ``send`` returns True when the remote accepted the message. A transport
    without an idempotency key reports an uncertain send by raising
    ``DeliveryUncertain``; that becomes ``ambiguous`` rather than a retry,
    because the message may already be on the channel.

    Nothing here reruns an action. Only stored output is resent.
    """
    store = DeliveryStore(conn)
    outcomes: list[DeliveryOutcome] = []
    # Both are retryable: `pending` never went out, `failed` was refused. A
    # crashed-before-send delivery sits in one of exactly these two states.
    recoverable = sorted(
        (
            *store.list_by_status("pending"),
            *store.list_by_status("failed"),
        ),
        key=lambda d: (d.turn_id, d.segment),
    )
    for delivery in recoverable:
        if delivery.attempts >= max_attempts:
            store.mark_failed(
                delivery.turn_id,
                delivery.delivery_id,
                reason=f"exceeded {max_attempts} delivery attempts",
            )
            outcomes.append(
                DeliveryOutcome(delivery.delivery_id, "failed", delivery.attempts + 1)
            )
            continue
        store.mark_sending(
            delivery.turn_id, delivery.delivery_id, client_seq=delivery.client_seq
        )
        try:
            accepted = send(delivery)
        except DeliveryUncertain as exc:
            store.mark_ambiguous(
                delivery.turn_id, delivery.delivery_id, reason=str(exc)
            )
            outcomes.append(
                DeliveryOutcome(
                    delivery.delivery_id, "ambiguous", delivery.attempts + 1,
                    duplicate_possible=True,
                )
            )
            continue
        if accepted:
            store.mark_delivered(delivery.turn_id, delivery.delivery_id)
            outcomes.append(
                DeliveryOutcome(delivery.delivery_id, "delivered", delivery.attempts + 1)
            )
        else:
            store.mark_failed(
                delivery.turn_id, delivery.delivery_id, reason="transport refused"
            )
            outcomes.append(
                DeliveryOutcome(delivery.delivery_id, "failed", delivery.attempts + 1)
            )
    return outcomes


class DeliveryUncertain(Exception):
    """A send whose outcome the transport cannot confirm.

    Raised by a transport with no idempotency key when it cannot tell whether
    the remote accepted the message. The delivery becomes ``ambiguous``: an
    operator may resend and accept a possible duplicate, but the system never
    claims a delivery it cannot prove.
    """
