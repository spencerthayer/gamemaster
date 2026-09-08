"""Registry of loaded, validated, initialized game-system plugins.

The registry owns plugin instances and nothing else: it never parses YAML,
never touches the filesystem, and never loads code. The runtime owns its
registry instance; there is no module-level singleton. Listing is sorted by
system id so output is deterministic for tests, logs, and prompt
construction.
"""

from __future__ import annotations

import logging

from tabletop.api.capabilities import Capability
from tabletop.api.errors import DuplicatePluginError, PluginNotFoundError
from tabletop.api.plugin import GameSystemPlugin

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Owns loaded game-system plugin instances, keyed by system id."""

    def __init__(self) -> None:
        self._plugins: dict[str, GameSystemPlugin] = {}

    def register(self, plugin: GameSystemPlugin) -> None:
        """Register one initialized plugin; duplicate ids fail closed."""
        system_id = plugin.info.id
        if system_id in self._plugins:
            raise DuplicatePluginError(
                f"system id {system_id!r} is already registered; "
                "duplicate configured plugins must fail closed"
            )
        self._plugins[system_id] = plugin
        logger.info("registered plugin %s", system_id)

    def unregister(self, system_id: str) -> None:
        """Remove one plugin; a missing id raises PluginNotFoundError."""
        if system_id not in self._plugins:
            raise PluginNotFoundError(f"system id {system_id!r} is not registered")
        del self._plugins[system_id]

    def get(self, system_id: str) -> GameSystemPlugin:
        """Return the plugin for ``system_id``; missing ids raise."""
        if system_id not in self._plugins:
            raise PluginNotFoundError(f"system id {system_id!r} is not registered")
        return self._plugins[system_id]

    def contains(self, system_id: str) -> bool:
        return system_id in self._plugins

    def list(self) -> tuple[GameSystemPlugin, ...]:
        """All registered plugins, sorted by system id."""
        return tuple(self._plugins[system_id] for system_id in sorted(self._plugins))

    def capabilities(self, system_id: str) -> frozenset[Capability]:
        """Capabilities of one registered plugin, from the plugin object itself."""
        return self.get(system_id).capabilities()

    def shutdown_all(self) -> tuple[tuple[str, Exception], ...]:
        """Shut down every plugin; one failure never blocks the others.

        Failures are collected and returned as (system_id, exception) pairs
        so callers can log or report them; they are never silently swallowed.
        """
        failures: list[tuple[str, Exception]] = []
        for system_id in sorted(self._plugins):
            try:
                self._plugins[system_id].shutdown()
            except Exception as exc:  # noqa: BLE001 - lifecycle must not abort siblings
                logger.error("shutdown failed for plugin %s: %s", system_id, exc)
                failures.append((system_id, exc))
        return tuple(failures)
