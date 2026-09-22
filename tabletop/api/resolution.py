"""Resolution models.

``ResolutionContext`` is the plugin-visible snapshot supplied to
``GameSystemPlugin.resolve``. ``Resolution`` is the structured mechanical
result. Plugins describe desired ``StateChange`` values; they do not
mutate authoritative campaign persistence through the context.

``outcome`` is owned by the game-system plugin. The generic runtime does
not require success, failure, damage, or margin keys.
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


@dataclass(frozen=True)
class Resolution:
    """Structured mechanical outcome of ``GameSystemPlugin.resolve``.

    ``outcome`` is system-owned structured data. The generic runtime must
    not require keys such as success, failure, damage, or margin.
    ``explanation`` is a human-readable account of the resolver's
    mechanical decision, not scene narration, LLM prose, or dialogue.
    ``requires_ruling`` True requires a non-empty ``ruling_question``;
    False forbids one.
    """

    outcome: Mapping[str, Any]
    rolls: tuple[RollResult, ...] = ()
    state_changes: tuple[StateChange, ...] = ()
    rule_references: tuple[RuleReference, ...] = ()
    events: tuple[GameEvent, ...] = ()
    requires_ruling: bool = False
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
        if not isinstance(self.requires_ruling, bool):
            raise InvalidResolutionError("requires_ruling must be a bool")
        if self.requires_ruling:
            require_non_empty_str(
                self.ruling_question, "ruling_question", InvalidResolutionError
            )
        elif self.ruling_question is not None:
            raise InvalidResolutionError(
                "ruling_question must be None when requires_ruling is False"
            )
        optional_non_empty_str(self.explanation, "explanation", InvalidResolutionError)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": to_jsonable(self.outcome),
            "rolls": [roll.to_dict() for roll in self.rolls],
            "state_changes": [change.to_dict() for change in self.state_changes],
            "rule_references": [ref.to_dict() for ref in self.rule_references],
            "events": [event.to_dict() for event in self.events],
            "requires_ruling": self.requires_ruling,
            "ruling_question": self.ruling_question,
            "explanation": self.explanation,
        }
