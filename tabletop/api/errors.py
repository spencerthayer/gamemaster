"""Exceptions for game-system plugin integration contract failures.

Expected invalid game state is reported through ``ValidationResult`` objects,
never raised. These exceptions represent failures of the integration
contract itself: bad configuration, missing capabilities, broken lifecycle.
They carry only plain serializable fields so the Phase 5 adapter can encode
them without knowing game concepts.
"""

from __future__ import annotations

from tabletop.api.capabilities import Capability


class GameSystemError(Exception):
    """Base class for game-system plugin integration failures."""

    code = "game_system_error"


class UnsupportedCapabilityError(GameSystemError):
    """A caller required a capability the plugin does not implement."""

    code = "unsupported_capability"

    def __init__(self, *, system_id: str, capability: Capability) -> None:
        self.system_id = system_id
        self.capability = capability
        super().__init__(
            f"system {system_id!r} does not implement capability {capability.value!r}"
        )


class InvalidPluginConfigurationError(GameSystemError):
    """Plugin identity or configuration violates the plugin API contract."""

    code = "invalid_plugin_configuration"


class PluginInitializationError(GameSystemError):
    """A plugin failed during initialize()."""

    code = "plugin_initialization_error"


class StateValidationError(GameSystemError):
    """System-owned state is structurally unusable, not merely game-invalid."""

    code = "state_validation_error"
