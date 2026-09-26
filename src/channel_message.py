"""Structured channel ingress and egress.

A channel used to hand back a bare string, and the loop concatenated several
messages with ``" | "``. That lost every native message id, made literal
``" | "`` in a player's text indistinguishable from a separator, and left
duplicate suppression working on text equality rather than message identity.

``InboundMessage`` and ``OutboundMessage`` carry the identity each transport
already provides, so a turn can be claimed against a real message id.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class InboundMessage:
    """One message received from a channel, with its native identity."""

    channel: str
    external_message_id: str
    text: str
    conversation_id: str | None = None
    principal: str | None = None
    reply_to: str | None = None
    received_at: str | None = None
    raw_route: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.channel:
            raise ValueError("channel is required")
        if not self.external_message_id:
            raise ValueError("external_message_id is required")
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "external_message_id": self.external_message_id,
            "text": self.text,
            "conversation_id": self.conversation_id,
            "principal": self.principal,
            "reply_to": self.reply_to,
            "received_at": self.received_at,
        }


@dataclass(frozen=True)
class OutboundMessage:
    """One message segment to send, bound to the turn that produced it."""

    turn_id: str
    text: str
    delivery_id: str | None = None
    route: Mapping[str, Any] | None = None
    segment: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "delivery_id": self.delivery_id,
            "text": self.text,
            "route": None if self.route is None else dict(self.route),
            "segment": self.segment,
        }


def synthetic_message_id(channel: str, text: str, ordinal: int) -> str:
    """A stable id for a transport that supplies no native one.

    Content-addressed, so the same text at the same position is the same
    message. That is deliberately weaker than a real provider id and cannot
    distinguish a genuine repeat from a retry; IRC is the one transport with no
    identity at all.
    """

    digest = hashlib.sha256(f"{channel}\x00{text}".encode("utf-8")).hexdigest()
    return f"{channel}:{digest[:32]}:{ordinal}"
