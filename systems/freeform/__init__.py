"""Freeform reference system with generic checks and resource tracking."""

from __future__ import annotations

from typing import Any, Mapping

from tabletop.api.actions import GameAction
from tabletop.api.capabilities import Capability
from tabletop.api.errors import StateValidationError
from tabletop.api.plugin import (
    TABLETOP_PLUGIN_API_VERSION,
    GameSystemInfo,
    GameSystemPlugin,
    ValidationResult,
)
from tabletop.api.resolution import (
    Resolution,
    ResolutionContext,
    ResolutionStatus,
    StateChange,
    StateOperation,
)
from tabletop.dice import roller

_SYSTEM_ID = "freeform"
_DEFAULT_EXPRESSION = "1d20"


class FreeformPlugin(GameSystemPlugin):
    """Generic checks without system-specific character or combat concepts."""

    @property
    def info(self) -> GameSystemInfo:
        return GameSystemInfo(
            id=_SYSTEM_ID,
            name="Freeform Reference System",
            api_version=TABLETOP_PLUGIN_API_VERSION,
            version="0.1.0",
            description=(
                "Platform-agnostic freeform play with generic checks, opposed "
                "checks, and resource tracking."
            ),
        )

    def capabilities(self) -> frozenset[Capability]:
        """Advertise only the mechanics implemented by this plugin."""
        return frozenset(
            {
                Capability.DICE,
                Capability.ACTION_RESOLUTION,
                Capability.OPPOSED_RESOLUTION,
                Capability.RESOURCE_TRACKING,
            }
        )

    def resolve(self, action: GameAction, context: ResolutionContext) -> Resolution:
        """Resolve a supported generic action without mutating context state."""
        if action.action_type == "check":
            return self._resolve_check(action)
        if action.action_type == "opposed_check":
            return self._resolve_opposed_check(action)
        if action.action_type == "decrement_resource":
            return self._resolve_resource_decrement(action, context)
        return Resolution(
            outcome={},
            status=ResolutionStatus.UNSUPPORTED,
            explanation=(
                f"Freeform does not model action type {action.action_type!r}."
            ),
        )

    def _resolve_check(self, action: GameAction) -> Resolution:
        difficulty = action.parameters.get("difficulty")
        if not _is_number(difficulty):
            return _unresolved("A check requires a numeric target difficulty.")

        expression = action.parameters.get("expression", _DEFAULT_EXPRESSION)
        if not isinstance(expression, str):
            return _unresolved("A check requires a dice expression string.")

        result = roller.roll(expression)
        check_result = "success" if result.total >= difficulty else "failure"
        return Resolution(
            outcome={
                "check_result": check_result,
                "roll_total": result.total,
                "difficulty": difficulty,
            },
            rolls=(result,),
            explanation=(
                f"Rolled {result.total} against difficulty {difficulty}: "
                f"{check_result}."
            ),
        )

    def _resolve_opposed_check(self, action: GameAction) -> Resolution:
        if len(action.targets) != 1:
            return _unresolved("An opposed check requires exactly one target.")

        actor_expression = action.parameters.get(
            "actor_expression", _DEFAULT_EXPRESSION
        )
        target_expression = action.parameters.get(
            "target_expression", _DEFAULT_EXPRESSION
        )
        if not isinstance(actor_expression, str) or not isinstance(
            target_expression, str
        ):
            return _unresolved(
                "An opposed check requires dice expression strings for both sides."
            )

        actor_roll = roller.roll(actor_expression)
        target_roll = roller.roll(target_expression)
        if actor_roll.total > target_roll.total:
            opposed_result = "actor"
        elif target_roll.total > actor_roll.total:
            opposed_result = "target"
        else:
            opposed_result = "tie"

        return Resolution(
            outcome={
                "opposed_result": opposed_result,
                "actor_total": actor_roll.total,
                "target_total": target_roll.total,
            },
            rolls=(actor_roll, target_roll),
            explanation=(
                f"Actor rolled {actor_roll.total}; target rolled "
                f"{target_roll.total}: {opposed_result}."
            ),
        )

    def _resolve_resource_decrement(
        self, action: GameAction, context: ResolutionContext
    ) -> Resolution:
        raw_path = action.parameters.get("path")
        amount = action.parameters.get("amount", 1)
        if not isinstance(raw_path, (tuple, list)) or not raw_path:
            return _unresolved("A resource decrement requires a non-empty state path.")
        if not all(
            isinstance(component, (str, int)) and not isinstance(component, bool)
            for component in raw_path
        ):
            return _unresolved("A resource path may contain only strings and integers.")
        if not _is_number(amount) or amount <= 0:
            return _unresolved("A resource decrement requires a positive numeric amount.")

        path = tuple(raw_path)
        current = _read_path(context.state, path)
        if not _is_number(current):
            return _unresolved("The resource path must point to a numeric value.")
        new_value = current - amount
        return Resolution(
            outcome={
                "resource_result": "decremented",
                "previous_value": current,
                "new_value": new_value,
            },
            state_changes=(
                StateChange(
                    operation=StateOperation.SET,
                    path=path,
                    value=new_value,
                ),
            ),
            explanation=(
                f"Decremented resource at {path!r} from {current} to {new_value}."
            ),
        )

    def validate_state(self, state: Mapping[str, Any]) -> ValidationResult:
        """Freeform accepts any mapping as narrative state."""
        if not isinstance(state, Mapping):
            raise StateValidationError("freeform state must be a mapping")
        return ValidationResult.ok()

    def state_schema(self) -> Mapping[str, Any]:
        """Freeform keeps narrative and resource state schema-free."""
        return {}


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _read_path(
    state: Mapping[str, Any], path: tuple[str | int, ...]
) -> object | None:
    current: object = state
    for component in path:
        if isinstance(current, Mapping) and component in current:
            current = current[component]
        elif (
            isinstance(current, (tuple, list))
            and isinstance(component, int)
            and 0 <= component < len(current)
        ):
            current = current[component]
        else:
            return None
    return current


def _unresolved(explanation: str) -> Resolution:
    return Resolution(
        outcome={},
        status=ResolutionStatus.UNRESOLVED,
        explanation=explanation,
    )
