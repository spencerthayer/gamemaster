"""Resolution models.

``ResolutionContext`` is the plugin-visible snapshot supplied to
``GameSystemPlugin.resolve``. ``Resolution`` is the structured mechanical
result. Plugins describe desired ``StateChange`` values; they do not
mutate authoritative campaign persistence through the context.

``outcome`` is owned by the game-system plugin. The generic runtime does
not require success, failure, damage, or margin keys.

``ResolutionStatus`` (Phase 9) is the closed set of ways a resolve call can
end. It is deliberately separate from ``outcome``: status is the runtime's
business, outcome is the plugin's. A status other than ``RESOLVED`` is never
permission to invent a mechanical result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, assert_never

from tabletop.api._contract import (
    freeze_mapping,
    freeze_state_path,
    freeze_tuple,
    freeze_value,
    optional_non_empty_str,
    require_non_empty_str,
    to_jsonable,
)
from tabletop.api.errors import InvalidResolutionError
from tabletop.api.events import GameEvent
from tabletop.api.rules import RuleReference


class StateOperation(str, Enum):
    """Generic mutations the runtime can apply without game semantics."""

    SET = "set"
    DELETE = "delete"


@dataclass(frozen=True)
class ResolutionContext:
    """Input snapshot for one resolve call.

    This is data, not a service locator. It must not carry SQLite
    connections, the plugin registry, Omega, an LLM client, or a
    retriever. ``state`` is plugin-visible campaign/system state. Plugins
    read it and return requested changes through ``Resolution``; later
    runtime phases apply those changes transactionally.
    """

    campaign_id: str
    system_id: str
    scene_id: str | None = None
    state: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_non_empty_str(self.campaign_id, "campaign_id", InvalidResolutionError)
        require_non_empty_str(self.system_id, "system_id", InvalidResolutionError)
        optional_non_empty_str(self.scene_id, "scene_id", InvalidResolutionError)
        object.__setattr__(
            self,
            "state",
            freeze_mapping(self.state, "state", InvalidResolutionError),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "system_id": self.system_id,
            "scene_id": self.scene_id,
            "state": to_jsonable(self.state),
        }


@dataclass(frozen=True)
class StateChange:
    """A desired mutation of campaign state.

    The plugin describes the change. The generic runtime transports it
    without understanding game-specific path suffixes such as hit points
    or fatigue. A later phase applies the operation. This is not a
    ``GameEvent``: events record that something happened; state changes
    request a mutation.

    ``path`` is a tuple of components, not a dotted string, so keys and
    entity ids that contain dots stay unambiguous. ``previous_value`` is
    not part of this contract: the store, not the plugin, owns prior
    state when a mutation is applied.
    """

    operation: StateOperation
    path: tuple[str | int, ...]
    value: Any = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "operation", _coerce_state_operation(self.operation)
        )
        object.__setattr__(
            self, "path", freeze_state_path(self.path, InvalidResolutionError)
        )
        object.__setattr__(
            self,
            "value",
            freeze_value(self.value, "value", InvalidResolutionError),
        )
        match self.operation:
            case StateOperation.SET:
                pass
            case StateOperation.DELETE:
                if self.value is not None:
                    raise InvalidResolutionError(
                        "DELETE state changes must not include a value"
                    )
            case _:
                assert_never(self.operation)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation.value,
            "path": list(self.path),
            "value": to_jsonable(self.value),
        }


def _coerce_state_operation(value: StateOperation | str) -> StateOperation:
    if isinstance(value, StateOperation):
        return value
    if isinstance(value, str):
        try:
            return StateOperation(value)
        except ValueError:
            pass
    raise InvalidResolutionError(
        f"operation must be one of {[op.value for op in StateOperation]}, got {value!r}"
    )


@dataclass(frozen=True)
class RollResult:
    """Generic dice result contract.

    Phase 10 owns parsing and rolling. This type only transports an
    expression, a numeric total, and plugin-owned ``details``. Critical
    success, advantage, and success margin are not generic required
    fields; a system that needs them puts them in ``details`` or
    ``Resolution.outcome``.
    """

    expression: str
    total: int | float
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_non_empty_str(self.expression, "expression", InvalidResolutionError)
        if isinstance(self.total, bool) or not isinstance(self.total, (int, float)):
            raise InvalidResolutionError(
                f"total must be an int or float, got {type(self.total).__name__}"
            )
        if isinstance(self.total, float) and not math.isfinite(self.total):
            raise InvalidResolutionError(
                f"total must be a finite JSON number, got {self.total!r}"
            )
        object.__setattr__(
            self,
            "details",
            freeze_mapping(self.details, "details", InvalidResolutionError),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "expression": self.expression,
            "total": self.total,
            "details": to_jsonable(self.details),
        }


class ResolutionStatus(str, Enum):
    """How a resolve call ended. Closed set, not a boolean.

    ``RESOLVED``
        A deterministic mechanical result exists.
    ``RULING_REQUIRED``
        Enough information exists, but the rules intentionally leave a
        judgment call to the GM.
    ``UNRESOLVED``
        The system implements this mechanic, but required facts, rules, or
        data are missing.
    ``UNSUPPORTED``
        This plugin does not implement the mechanic at all.

    The three non-resolved states may all reach the same adjudication
    interface, but they are different problems and must not be presented to
    the GM identically. None of them licenses fabricated mechanical values.
    """

    RESOLVED = "resolved"
    RULING_REQUIRED = "ruling-required"
    UNRESOLVED = "unresolved"
    UNSUPPORTED = "unsupported"


def _coerce_resolution_status(value: "ResolutionStatus | str") -> "ResolutionStatus":
    if isinstance(value, ResolutionStatus):
        return value
    if isinstance(value, str):
        try:
            return ResolutionStatus(value)
        except ValueError:
            pass
    raise InvalidResolutionError(
        f"status must be one of {[s.value for s in ResolutionStatus]}, got {value!r}"
    )


@dataclass(frozen=True)
class Resolution:
    """Structured mechanical outcome of ``GameSystemPlugin.resolve``.

    ``outcome`` is system-owned structured data. The generic runtime must
    not require keys such as success, failure, damage, or margin.
    ``explanation`` is a human-readable account of the resolver's
    mechanical decision, not scene narration, LLM prose, or dialogue.

    ``status`` (Phase 9) carries the policy invariants, and
    ``requires_ruling`` is derived from it so the two can never disagree:

    - ``RULING_REQUIRED`` requires a non-empty ``ruling_question``; every
      other status forbids one.
    - ``UNRESOLVED`` and ``UNSUPPORTED`` carry no mechanical result: empty
      ``outcome``, no rolls, no state changes, no events. They require an
      ``explanation`` so the GM learns what is actually missing.
      ``rule_references`` stay allowed, since pointing at the relevant rule
      is the useful part.
    - ``RULING_REQUIRED`` may carry rolls and outcome data, but must not
      request state changes or propose events: nothing is settled until the
      GM rules.
    """

    outcome: Mapping[str, Any]
    status: ResolutionStatus = ResolutionStatus.RESOLVED
    rolls: tuple[RollResult, ...] = ()
    state_changes: tuple[StateChange, ...] = ()
    rule_references: tuple[RuleReference, ...] = ()
    events: tuple[GameEvent, ...] = ()
    ruling_question: str | None = None
    explanation: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "outcome",
            freeze_mapping(self.outcome, "outcome", InvalidResolutionError),
        )
        object.__setattr__(
            self,
            "rolls",
            freeze_tuple(self.rolls, RollResult, "rolls", InvalidResolutionError),
        )
        object.__setattr__(
            self,
            "state_changes",
            freeze_tuple(
                self.state_changes, StateChange, "state_changes", InvalidResolutionError
            ),
        )
        object.__setattr__(
            self,
            "rule_references",
            freeze_tuple(
                self.rule_references,
                RuleReference,
                "rule_references",
                InvalidResolutionError,
            ),
        )
        object.__setattr__(
            self,
            "events",
            freeze_tuple(self.events, GameEvent, "events", InvalidResolutionError),
        )
        object.__setattr__(self, "status", _coerce_resolution_status(self.status))
        optional_non_empty_str(self.explanation, "explanation", InvalidResolutionError)
        match self.status:
            case ResolutionStatus.RESOLVED:
                self._forbid_ruling_question()
            case ResolutionStatus.RULING_REQUIRED:
                require_non_empty_str(
                    self.ruling_question, "ruling_question", InvalidResolutionError
                )
                self._forbid_fields(
                    ("state_changes", "events"),
                    "nothing is settled until the GM rules",
                )
            case ResolutionStatus.UNRESOLVED | ResolutionStatus.UNSUPPORTED:
                self._forbid_ruling_question()
                self._forbid_fields(
                    ("rolls", "state_changes", "events"),
                    "a non-resolved status carries no mechanical result",
                )
                if self.outcome:
                    raise InvalidResolutionError(
                        f"{self.status.value} resolutions must have an empty outcome: "
                        "a non-resolved status carries no mechanical result"
                    )
                require_non_empty_str(
                    self.explanation, "explanation", InvalidResolutionError
                )
            case _:
                assert_never(self.status)

    @property
    def requires_ruling(self) -> bool:
        """True only for ``RULING_REQUIRED``. Derived, never stored."""
        return self.status is ResolutionStatus.RULING_REQUIRED

    def _forbid_ruling_question(self) -> None:
        if self.ruling_question is not None:
            raise InvalidResolutionError(
                "ruling_question is only valid when status is ruling-required"
            )

    def _forbid_fields(self, names: tuple[str, ...], reason: str) -> None:
        for name in names:
            if getattr(self, name):
                raise InvalidResolutionError(
                    f"{self.status.value} resolutions must not carry {name}: {reason}"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": to_jsonable(self.outcome),
            "status": self.status.value,
            "rolls": [roll.to_dict() for roll in self.rolls],
            "state_changes": [change.to_dict() for change in self.state_changes],
            "rule_references": [ref.to_dict() for ref in self.rule_references],
            "events": [event.to_dict() for event in self.events],
            "requires_ruling": self.requires_ruling,
            "ruling_question": self.ruling_question,
            "explanation": self.explanation,
        }
