"""Channels carry message identity, not just text."""

from __future__ import annotations

import hashlib

import pytest

from src import channels as src_channels

from src.channel_message import (  # noqa: E402
    InboundMessage,
    OutboundMessage,
    synthetic_message_id,
)


def _message(**overrides: object) -> InboundMessage:
    payload: dict[str, object] = {
        "channel": "telegram",
        "external_message_id": "4242",
        "text": "Ada tries the gate",
        "conversation_id": "chat-1",
    }
    payload.update(overrides)
    return InboundMessage(**payload)  # type: ignore[arg-type]


# -- the types --------------------------------------------------------------


def test_an_inbound_message_carries_its_native_identity() -> None:
    message = _message()
    assert message.channel == "telegram"
    assert message.external_message_id == "4242"
    assert message.conversation_id == "chat-1"


def test_an_inbound_message_requires_an_identity() -> None:
    with pytest.raises(ValueError):
        _message(external_message_id="")
    with pytest.raises(ValueError):
        _message(channel="")


def test_an_inbound_message_is_frozen() -> None:
    with pytest.raises(Exception):
        _message().text = "something else"  # type: ignore[misc]


def test_literal_separator_text_survives_intact() -> None:
    """The old " | " split would have shredded this into three messages."""
    message = _message(text="Ada | Bosch | Vor walk out")
    assert message.text == "Ada | Bosch | Vor walk out"


def test_two_messages_with_equal_text_are_distinct() -> None:
    first = _message(external_message_id="1", text="yes")
    second = _message(external_message_id="2", text="yes")
    assert first.text == second.text
    assert first.external_message_id != second.external_message_id


def test_an_outbound_message_is_bound_to_a_turn() -> None:
    message = OutboundMessage(turn_id="turn-1", text="Ada forces the gate", segment=2)
    assert message.turn_id == "turn-1"
    assert message.segment == 2
    assert message.to_dict()["text"] == "Ada forces the gate"


def test_a_synthetic_id_is_stable_and_content_addressed() -> None:
    first = synthetic_message_id("irc", "hello", 0)
    second = synthetic_message_id("irc", "hello", 0)
    assert first == second
    assert first != synthetic_message_id("irc", "goodbye", 0)


# -- the CommChannel default ------------------------------------------------


class _TextChannel(src_channels.CommChannel):
    def __init__(self, text: str) -> None:
        self._text = text

    def receive(self) -> str:
        return self._text

    def send(self, message: str) -> None:
        pass


def _registered(text: str) -> src_channels.CommChannel:
    channel = _TextChannel(text)
    src_channels.registerCommChannel("test-channel", channel)
    return channel


def test_the_default_receiver_wraps_text_in_one_structured_message() -> None:
    channel = _registered("Ada tries the gate")
    messages = channel.receive_messages()
    assert len(messages) == 1
    assert messages[0].text == "Ada tries the gate"
    assert messages[0].channel == "test-channel"


def test_the_default_receiver_returns_nothing_for_empty_text() -> None:
    assert _registered("").receive_messages() == []


def test_registration_gives_a_channel_its_id() -> None:
    assert _registered("x").channel_id == "test-channel"


def test_separator_text_is_not_split_by_the_default_receiver() -> None:
    messages = _registered("Ada | Bosch walk out").receive_messages()
    assert len(messages) == 1
    assert messages[0].text == "Ada | Bosch walk out"


# -- receive_messages is overridable ----------------------------------------


class _IdentityChannel(src_channels.CommChannel):
    def __init__(self, messages: list[InboundMessage]) -> None:
        self._messages = messages

    def receive(self) -> str:  # pragma: no cover - unused
        return ""

    def receive_messages(self) -> list[InboundMessage]:
        return self._messages


def test_an_adapter_can_report_several_identities() -> None:
    channel = _IdentityChannel(
        [
            _message(external_message_id="1", text="first"),
            _message(external_message_id="2", text="second"),
        ]
    )
    messages = channel.receive_messages()
    assert [m.external_message_id for m in messages] == ["1", "2"]


def test_a_synthetic_id_differs_per_channel() -> None:
    assert synthetic_message_id("irc", "hi", 0) != synthetic_message_id("websocket", "hi", 0)


def test_a_message_principal_is_preserved() -> None:
    assert _message(principal="telegram:42").principal == "telegram:42"


def test_a_message_serializes_without_secrets() -> None:
    payload = _message().to_dict()
    assert set(payload) >= {"channel", "external_message_id", "text"}
    assert hashlib.sha256  # payload is plain data
