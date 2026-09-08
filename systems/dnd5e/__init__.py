"""D&D 5e reference plugin: structural stub, no mechanics.

Conforms to the Phase 6 ``GameSystemPlugin`` API as the second structural
test proving two plugins can present different metadata and capabilities.
It implements no D&D mechanics (Phase 27) and advertises no capabilities,
because capabilities mean "implemented by this plugin", not "D&D 5e
conceptually has this feature". One rules revision (2014 or 2024) will be
selected in Phase 27, never mixed.
"""

from __future__ import annotations

from typing import Any, Mapping

from tabletop.api.capabilities import Capability
from tabletop.api.errors import GameSystemError
from tabletop.api.plugin import (
    TABLETOP_PLUGIN_API_VERSION,
    GameSystemInfo,
    GameSystemPlugin,
    ValidationResult,
)

_SYSTEM_ID = "dnd5e"


class Dnd5ePlugin(GameSystemPlugin):
    """D&D 5e contract stub: metadata only, no mechanics."""

    @property
    def info(self) -> GameSystemInfo:
        return GameSystemInfo(
            id=_SYSTEM_ID,
            name="D&D 5e Reference Plugin",
            api_version=TABLETOP_PLUGIN_API_VERSION,
            version="0.1.0",
            description=(
                "Structural stub conforming to the plugin API. Mechanics "
                "(ability checks, saving throws, attack rolls, combat) are "
                "Phase 27; capabilities stay unadvertised until implemented."
            ),
        )

    def capabilities(self) -> frozenset[Capability]:
        """Nothing implemented yet; D&D concepts are not advertised."""
        return frozenset()

    def resolve(self, action: "GameAction", context: "ResolutionContext") -> "Resolution":
        raise GameSystemError(
            "dnd5e resolve() is implemented in Phase 27; no mechanics exist yet"
        )

    def validate_state(self, state: Mapping[str, Any]) -> ValidationResult:
        """Phase 27 will validate 5e sheets; anything passes until then."""
        return ValidationResult.ok()
