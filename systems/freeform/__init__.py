"""Freeform reference system: the minimal contract test implementation.

Implements the Phase 6 ``GameSystemPlugin`` API with no system-specific
gameplay. Freeform advertises the platform's generic dice roller while combat,
turn order, hit points, and numeric sheets remain unsupported. System mechanics
arrive in Phase 26.
"""

from __future__ import annotations

from typing import Any, Mapping

from tabletop.api.actions import GameAction
from tabletop.api.capabilities import Capability
from tabletop.api.errors import GameSystemError, StateValidationError
from tabletop.api.plugin import (
    TABLETOP_PLUGIN_API_VERSION,
    GameSystemInfo,
    GameSystemPlugin,
    ValidationResult,
)
from tabletop.api.resolution import Resolution, ResolutionContext

_SYSTEM_ID = "freeform"


class FreeformPlugin(GameSystemPlugin):
    """Minimal freeform game system: generic actions, nothing mechanical."""

    @property
    def info(self) -> GameSystemInfo:
        return GameSystemInfo(
            id=_SYSTEM_ID,
            name="Freeform Reference System",
            api_version=TABLETOP_PLUGIN_API_VERSION,
            version="0.1.0",
            description=(
                "Platform-agnostic freeform play: generic actions and opposed "
                "checks without classes, HP, AC, or initiative. Reference "
                "implementation of the plugin contract; mechanics arrive in "
                "Phase 26."
            ),
        )

    def capabilities(self) -> frozenset[Capability]:
        """Advertise generic runtime dice rolling."""
        return frozenset({Capability.DICE})

    def resolve(self, action: GameAction, context: ResolutionContext) -> Resolution:
        raise GameSystemError(
            "freeform resolve() is implemented in Phase 26; no mechanics exist yet"
        )

    def validate_state(self, state: Mapping[str, Any]) -> ValidationResult:
        """Freeform accepts any mapping as narrative state."""
        if not isinstance(state, Mapping):
            raise StateValidationError("freeform state must be a mapping")
        return ValidationResult.ok()

    def state_schema(self) -> Mapping[str, Any]:
        """Freeform keeps free-form narrative state; nothing to describe yet."""
        return {}
