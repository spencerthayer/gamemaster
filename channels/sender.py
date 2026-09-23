"""Trusted channel sender principal carried via ContextVar."""

from __future__ import annotations

import contextvars
import os
from typing import Any

EXPECTED_SENDER_ENV_VAR = "OMEGA_EXPECTED_SENDER"

_current_sender: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "omega_current_sender",
    default=None,
)


def current_sender() -> str | None:
    """Return the accepted opaque sender for this interpreter context."""

    return _current_sender.get()


def set_current_sender(sender: str | None) -> contextvars.Token[str | None]:
    return _current_sender.set(sender)


def reset_current_sender(token: contextvars.Token[str | None]) -> None:
    _current_sender.reset(token)


def expected_sender_from_environ(environ: dict[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    value = env.get(EXPECTED_SENDER_ENV_VAR)
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def accept_sender(
    authenticated_sender: str | None,
    *,
    expected_sender: str | None,
) -> bool:
    """Return True when the inbound sender may become a human turn.

    When ``expected_sender`` is unset, preserve generic channel behavior
    (accept). When set, require an exact opaque match.
    """

    if expected_sender is None:
        return True
    if authenticated_sender is None:
        return False
    return authenticated_sender == expected_sender


class SenderGate:
    """Queue-facing gate that discards unauthorized payloads."""

    def __init__(self, expected_sender: str | None) -> None:
        self.expected_sender = expected_sender

    def allow(self, authenticated_sender: str | None) -> bool:
        return accept_sender(
            authenticated_sender,
            expected_sender=self.expected_sender,
        )

    def accept_turn(self, authenticated_sender: str | None, text: str) -> str | None:
        """Return text for an accepted turn, or None when the payload is discarded.

        Discarded payloads must not become an empty human message.
        """

        if not self.allow(authenticated_sender):
            return None
        if authenticated_sender is not None:
            set_current_sender(authenticated_sender)
        return text
