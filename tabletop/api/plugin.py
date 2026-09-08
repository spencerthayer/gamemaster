"""Game-system plugin API: versioning, metadata, and the plugin contract.

The single canonical location for the plugin API version string. Game-system
plugins implement :class:`GameSystemPlugin` and depend only on this package;
they never import Omega, MeTTa, or any runtime implementation module.

Action and resolution models are owned by Phase 8; this module references
them by forward annotation only and does not define them.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping

from tabletop.api.capabilities import Capability
from tabletop.api.errors import (
    InvalidPluginConfigurationError,
    UnsupportedCapabilityError,
)

TABLETOP_PLUGIN_API_VERSION = "tabletop/v1"

_API_VERSION_PATTERN = r"^tabletop/v[0-9]+$"
_ID_PATTERN = r"^[a-z][a-z0-9_-]*$"


def is_compatible_api_version(api_version: str) -> bool:
    """Return True when an API version string is an exact match.

    Phase 6 uses exact-match compatibility only; Phase 7 enforcement happens
    during discovery. Deliberately not semantic-version negotiation.
    """
    return api_version == TABLETOP_PLUGIN_API_VERSION


def is_valid_system_id(system_id: str) -> bool:
    """Return True for machine-readable lowercase slug system ids.

    Single source of truth shared by ``GameSystemInfo`` and plugin manifest
    validation.
    """
    return bool(re.match(_ID_PATTERN, system_id))


@dataclass(frozen=True)
class GameSystemInfo:
    """Immutable identity metadata for a game-system plugin.

    ``id`` is machine-readable (lowercase slug). ``name`` is human-readable.
    The plugin's own release ``version`` and the plugin ``api_version`` it
    implements are separate concepts and must never be conflated. No
    filesystem paths, no manifest data, no Omega-specific metadata.
    """

    id: str
    name: str
    api_version: str
    version: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        if not re.match(_ID_PATTERN, self.id):
            raise InvalidPluginConfigurationError(
                f"system id {self.id!r} must match {_ID_PATTERN}"
            )
        if not re.match(_API_VERSION_PATTERN, self.api_version):
            raise InvalidPluginConfigurationError(
                f"api_version {self.api_version!r} must match {_API_VERSION_PATTERN}"
            )


@dataclass(frozen=True)
class ValidationIssue:
    """One structured validation problem found in system-owned state.

    ``path`` is a dot/bracket path into the state mapping, when known.
    ``code`` is a stable machine-readable issue code; ``message`` is for
    display. System-agnostic by design.
    """

    path: str | None
    code: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating system-owned state.

    Expected invalid game data is reported here rather than raised;
    exceptions remain reserved for integration contract failures.
    """

    valid: bool
    issues: tuple[ValidationIssue, ...] = ()

    @classmethod
    def ok(cls) -> "ValidationResult":
        return cls(valid=True, issues=())


class GameSystemPlugin(ABC):
    """Contract every game-system plugin implements.

    The runtime uses plugins without knowing anything about their game's
    mechanics. No D&D (or GURPS, or any other system) concept may appear
    here: armor class, hit points, saving throws, spell slots, initiative,
    skill rolls, and hit locations belong inside plugins.

    Capabilities advertise what this plugin currently implements, never what
    the underlying game conceptually supports.

    Schemas returned by the schema hooks describe system-owned state and are
    opaque to the generic runtime except for validation, display, and
    storage.
    """

    @property
    @abstractmethod
    def info(self) -> GameSystemInfo:
        """Identity metadata for this plugin."""

    @abstractmethod
    def capabilities(self) -> frozenset[Capability]:
        """Capabilities this plugin currently implements."""

    @abstractmethod
    def resolve(self, action: "GameAction", context: "ResolutionContext") -> "Resolution":
        """Resolve one action deterministically.

        ``GameAction``, ``ResolutionContext``, and ``Resolution`` are owned
        by Phase 8; referenced here by forward annotation so the contract
        shape is stable without freezing a temporary model. When a plugin
        cannot decide deterministically it must yield to GM adjudication
        rather than fabricate an outcome (final mechanism: Phase 9).
        """

    def supports(self, capability: Capability) -> bool:
        """Return True when this plugin implements the capability."""
        return capability in self.capabilities()

    def require_capability(self, capability: Capability) -> None:
        """Raise UnsupportedCapabilityError when not implemented."""
        if not self.supports(capability):
            raise UnsupportedCapabilityError(
                system_id=self.info.id, capability=capability
            )

    def initialize(self) -> None:
        """Prepare plugin resources. Safe no-op by default."""

    def shutdown(self) -> None:
        """Release plugin resources. Safe no-op by default."""

    def validate_state(self, state: Mapping[str, Any]) -> ValidationResult:
        """Validate system-owned state. Accepts everything by default."""
        return ValidationResult.ok()

    def character_schema(self) -> Mapping[str, Any]:
        """Describe character state owned by the system. Opaque; empty by default."""
        return {}

    def entity_schema(self, entity_type: str) -> Mapping[str, Any]:
        """Describe one entity type owned by the system. Opaque; empty by default."""
        return {}

    def state_schema(self) -> Mapping[str, Any]:
        """Describe system-level state. Opaque; empty by default."""
        return {}

    def rule_namespaces(self) -> frozenset[str]:
        """Rule namespaces this plugin owns, for future retrieval (Phase 20+)."""
        return frozenset()
