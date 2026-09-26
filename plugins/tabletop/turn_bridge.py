"""Bind one human message, its context, its generation, and its send to one turn.

This is the seam between the Omega loop and the durable turn. The loop used to
build context before it knew which message it was answering, call the model,
and send through a process-local queue. Nothing tied those steps together, so
a crash between them left no record of what was half-done.

``TurnBridge`` is the ordering. Every step it performs is receipted first and
runs second, so a crash leaves a turn that recovery can classify rather than a
gap nobody can see.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Sequence

from tabletop.campaign.scene_snapshot import build_scene_snapshot
from tabletop.orchestration.delivery import DeliveryStore, GenerationReceiptStore
from tabletop.orchestration.turn_job import TurnJob, TurnJobStore, TurnTransitionError


class TurnBridgeError(RuntimeError):
    """A turn step could not be completed."""


@dataclass(frozen=True)
class TurnOutcome:
    """What one bridged turn produced."""

    turn_id: str
    disposition: str
    narration: str = ""
    reused_existing_turn: bool = False
    delivery_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "disposition": self.disposition,
            "narration": self.narration,
            "reused_existing_turn": self.reused_existing_turn,
            "delivery_ids": list(self.delivery_ids),
        }


class TurnBridge:
    """Drive one turn from ingress to queued delivery."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        campaign_id: str,
        turn_store: TurnJobStore | None = None,
        delivery_store: DeliveryStore | None = None,
        receipt_store: GenerationReceiptStore | None = None,
    ) -> None:
        self.conn = conn
        self.campaign_id = campaign_id
        self.turns = turn_store or TurnJobStore(conn)
        self.deliveries = delivery_store or DeliveryStore(conn)
        self.receipts = receipt_store or GenerationReceiptStore(conn)

    def receive(
        self,
        text: str,
        *,
        channel: str | None = None,
        conversation_id: str | None = None,
        external_message_id: str | None = None,
        principal: str | None = None,
        session_id: str | None = None,
        scene_id: str | None = None,
    ) -> TurnJob:
        """Claim the inbound message before anything else happens.

        A duplicate native message returns the turn that already owns it, and
        the caller stops: a retry must not produce a second generation.
        """
        existing = None
        if channel and external_message_id:
            existing = self.turns.find_by_ingress(
                self.campaign_id, channel, conversation_id, external_message_id
            )
        turn = self.turns.claim_ingress(
            self.campaign_id,
            text,
            channel=channel,
            conversation_id=conversation_id,
            external_message_id=external_message_id,
            principal_id=principal,
            session_id=session_id,
            scene_id=scene_id,
        )
        self._reused = existing is not None and existing.turn_id == turn.turn_id
        return turn

    def is_duplicate(self, turn: TurnJob) -> bool:
        """True when this turn was already claimed by an earlier ingress."""
        return bool(getattr(self, "_reused", False))

    def build_context(self, turn: TurnJob, *, viewpoint: Any) -> dict[str, Any]:
        """Build this turn's context from the authoritative snapshot.

        Called after the turn exists, so the snapshot can be read for one
        specific message rather than whatever arrived last.
        """
        snapshot = build_scene_snapshot(
            self.conn, self.campaign_id, viewpoint=viewpoint
        )
        self._advance(turn.turn_id, "interpreting")
        return snapshot

    def start_generation(self, turn: TurnJob) -> None:
        self._advance(turn.turn_id, "interpreting")

    def record_generation(
        self,
        turn: TurnJob,
        ordinal: int,
        *,
        provider: str | None = None,
        model: str | None = None,
        prompt_text: str | None = None,
        response_text: str | None = None,
        output_segments: Sequence[str] = (),
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        latency_ms: int | None = None,
    ) -> None:
        """Receipt a completed model generation.

        Every generation has a durable identifier before its output is sent, so
        a delivered message can always be traced to the call that made it.
        """
        self.receipts.record(
            turn.turn_id,
            ordinal,
            provider=provider,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            prompt_text=prompt_text,
            response_text=response_text,
            output_segments=output_segments,
        )

    def record_disposition(self, turn: TurnJob, disposition: str) -> TurnJob:
        job = self.turns.require(turn.turn_id)
        if job.status == "interpreting":
            return self._advance(turn.turn_id, "narrating", disposition=disposition)
        return job

    def enqueue_output(
        self,
        turn: TurnJob,
        channel: str,
        text: str,
        *,
        conversation_id: str | None = None,
    ) -> str:
        """Queue output for delivery before it is sent, never after."""
        job = self.turns.require(turn.turn_id)
        if job.status in ("completed", "delivery_pending"):
            self.turns.transition(job.turn_id, "delivery_pending")
        delivery = self.deliveries.enqueue(
            turn.turn_id, channel, text, conversation_id=conversation_id
        )
        return delivery.delivery_id

    def mark_delivered(self, turn: TurnJob, delivery_id: str) -> None:
        self.deliveries.mark_delivered(turn.turn_id, delivery_id)
        outstanding = [
            delivery
            for delivery in self.deliveries.list_for_turn(turn.turn_id)
            if delivery.status != "delivered"
        ]
        if outstanding:
            return
        # Walk the lifecycle forward rather than giving up. A caller that
        # already recorded a disposition is further along, and a caller that
        # did not still owes the turn its terminal status.
        for target in ("narrating", "completed", "delivery_pending", "delivered"):
            self._advance(turn.turn_id, target)

    def _advance(
        self, turn_id: str, status: str, *, disposition: str | None = None
    ) -> TurnJob:
        try:
            return self.turns.transition(turn_id, status, disposition=disposition)
        except TurnTransitionError:
            # A turn already past this point is not an error worth raising
            # over: a retry replays the same steps.
            return self.turns.require(turn_id)


def commit_action_effect(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    turn_id: str,
    ordinal: int,
    action: Any,
    resolution: Any,
    scene_id: str | None = None,
) -> Any:
    """Apply one resolved action and its effect claim in a single transaction.

    The claim, the ``action.resolved`` event, and the state changes commit
    together. Writing the claim separately would leave two failure modes: a
    claim that says committed when the effect is not, and an effect that exists
    with no claim, so a recovery pass would rerun it.
    """
    from tabletop.campaign.event_store import apply_resolved_action_in_transaction
    from tabletop.storage.sqlite import transaction

    turns = TurnJobStore(conn)
    with transaction(conn):
        existing = turns.claim_action_in_transaction(turn_id, ordinal, action.action_type)
        if existing == "committed":
            # Already committed. The caller reads the stored outcome; rerolling
            # here would apply a second mechanical effect.
            return None

        def _record(persisted: Any) -> None:
            turns.commit_action_in_transaction(
                turn_id, ordinal, event_sequence=persisted.sequence
            )

        return apply_resolved_action_in_transaction(
            conn,
            campaign_id,
            action,
            resolution,
            scene_id=scene_id,
            on_committed=_record,
        )


def _committed_outcome(
    conn: sqlite3.Connection, campaign_id: str, turn_id: str, ordinal: int
) -> Any:
    """Rebuild the recorded result of an action already committed."""
    from tabletop.api.events import GameEvent
    from tabletop.campaign.event_store import EventStore

    claim = next(
        c
        for c in TurnJobStore(conn).action_claims(turn_id)
        if int(c["ordinal"]) == ordinal
    )
    sequence = int(claim["event_sequence"])
    for event in EventStore(conn).read(campaign_id):
        if event.sequence == sequence:
            return GameEvent(
                event_type=event.event_type, payload=event.payload
            )
    raise LookupError(f"turn {turn_id} claims event {sequence} that is not in the log")
