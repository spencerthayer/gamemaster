"""Pure Python sender gate and ContextVar tests."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

_CHANNELS = Path(__file__).resolve().parents[1] / "channels"
if str(_CHANNELS) not in sys.path:
    sys.path.insert(0, str(_CHANNELS))

from sender import (  # noqa: E402
    SenderGate,
    accept_sender,
    current_sender,
    set_current_sender,
)


def test_accept_sender_when_expected_unset() -> None:
    assert accept_sender("99", expected_sender=None) is True
    assert accept_sender(None, expected_sender=None) is True


def test_accept_sender_requires_match_when_set() -> None:
    assert accept_sender("42", expected_sender="42") is True
    assert accept_sender("99", expected_sender="42") is False
    assert accept_sender(None, expected_sender="42") is False


def test_gate_discards_mismatch_without_empty_message() -> None:
    gate = SenderGate("42")
    assert gate.accept_turn("99", "Bob: hello") is None
    assert current_sender() is None
    accepted = gate.accept_turn("42", "Bob: hello")
    assert accepted == "Bob: hello"
    assert current_sender() == "42"


def test_display_name_does_not_set_sender() -> None:
    gate = SenderGate("99")
    gate.accept_turn("99", "Bob: hello")
    assert current_sender() == "99"


def test_empty_receive_leaves_sender() -> None:
    set_current_sender("42")
    assert current_sender() == "42"


@pytest.mark.parametrize(
    ("adapter", "authenticated"),
    [
        ("telegram", "42"),
        ("slack", "U123"),
        ("mattermost", "mm-user"),
        ("irc", "alice"),
        (
            "websocket",
            "websocket:" + hashlib.sha256(b"token").hexdigest(),
        ),
    ],
)
def test_channel_matrix_gate_rejects_mismatch(adapter: str, authenticated: str) -> None:
    gate = SenderGate(authenticated)
    assert gate.accept_turn("wrong", f"via {adapter}") is None
    assert gate.accept_turn(authenticated, f"via {adapter}") == f"via {adapter}"
    assert current_sender() == authenticated
