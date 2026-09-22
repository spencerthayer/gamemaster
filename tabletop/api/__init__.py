"""Plugin API contracts shared between the runtime and game-system plugins."""

from tabletop.api.actions import GameAction
from tabletop.api.capabilities import Capability
from tabletop.api.entities import EntityRef
from tabletop.api.events import GameEvent
from tabletop.api.plugin import (
    TABLETOP_PLUGIN_API_VERSION,
    GameSystemInfo,
    GameSystemPlugin,
    ValidationIssue,
    ValidationResult,
)
from tabletop.api.resolution import (
    Resolution,
    ResolutionContext,
    RollResult,
    StateChange,
    StateOperation,
)
from tabletop.api.rules import RuleReference

__all__ = [
    "TABLETOP_PLUGIN_API_VERSION",
    "Capability",
    "EntityRef",
    "GameAction",
    "GameEvent",
    "GameSystemInfo",
    "GameSystemPlugin",
    "Resolution",
    "ResolutionContext",
    "RollResult",
    "RuleReference",
    "StateChange",
    "StateOperation",
    "ValidationIssue",
    "ValidationResult",
]
