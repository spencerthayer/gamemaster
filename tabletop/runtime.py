"""Bootstrap facade for the platform-agnostic Tabletop Runtime.

Phase 5 established the runtime lifecycle and Omega adapter boundary; Phase 7
replaced shallow system discovery with the real plugin pipeline: configured
trusted roots -> discover plugin.yaml -> validate manifests -> reject
incompatible API versions -> load entrypoints -> verify the GameSystemPlugin
contract -> cross-check identity -> initialize -> register. Campaign
discovery stays shallow until Phase 11 introduces the authoritative store.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from tabletop.plugins.discovery import discover_plugins, load_plugin
from tabletop.plugins.registry import PluginRegistry

PLUGIN_PATH_ENV_VAR = "TABLETOP_PLUGIN_PATH"
CAMPAIGN_PATHS_ENV_VAR = "TABLETOP_CAMPAIGN_PATHS"
CAMPAIGN_ENV_VAR = "TABLETOP_CAMPAIGN"


class TabletopRuntime:
    """Runtime facade owning plugin loading, registry, and campaign roots.

    Plugins load once at startup. Adding or removing a plugin requires a
    runtime restart; there is no hot reload. The registry instance lives on
    the runtime, never at module level.
    """

    def __init__(
        self,
        repo_root: Path | str,
        *,
        campaign_roots: Iterable[Path | str] | None = None,
        plugin_roots: Iterable[Path | str] | None = None,
        active_campaign: str | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.campaign_roots = self._normalize_roots(
            campaign_roots
            if campaign_roots is not None
            else (self.repo_root / "campaigns", self.repo_root / "examples" / "campaigns")
        )
        # Explicit roots (argument or env) must exist: a typo in a plugin
        # mount must not silently disable a game system. The built-in
        # <repo>/systems root is appended only when present, so an isolated
        # runtime checkout without built-ins still boots.
        explicit_roots = tuple(plugin_roots) if plugin_roots is not None else ()
        builtin = self.repo_root / "systems"
        roots = (*explicit_roots, builtin) if builtin.is_dir() else explicit_roots
        # Dedupe after resolution: from_environment may pass the built-in
        # root explicitly, and from_environment plus __init__ can both add it.
        self.plugin_roots = tuple(dict.fromkeys(self._normalize_roots(roots)))
        self.active_campaign = active_campaign
        self._campaigns: tuple[str, ...] = ()
        self._registry = PluginRegistry()
        self.refresh_discovery()
        self._load_plugins()

    @classmethod
    def from_environment(
        cls,
        repo_root: Path | str,
        environ: Mapping[str, str] | None = None,
    ) -> "TabletopRuntime":
        """Build runtime bootstrap configuration from environment variables.

        ``TABLETOP_CAMPAIGN_PATHS`` uses the host OS path separator.
        ``TABLETOP_PLUGIN_PATH`` lists external plugin roots (also
        path-separator separated); the built-in ``<repo>/systems`` root is
        always appended. ``TABLETOP_CAMPAIGN`` optionally selects the active
        campaign by discovered directory name. Configured plugin roots that
        do not exist fail startup: a typo in a plugin mount must not
        silently disable a game system.
        """
        env = os.environ if environ is None else environ
        root = Path(repo_root).resolve()

        campaign_roots = cls._paths_from_env(
            env.get(CAMPAIGN_PATHS_ENV_VAR),
            defaults=(root / "campaigns", root / "examples" / "campaigns"),
        )
        env_plugin_roots = cls._paths_from_env(env.get(PLUGIN_PATH_ENV_VAR), defaults=())
        plugin_roots = tuple(dict.fromkeys((*env_plugin_roots, root / "systems")))
        active_campaign = env.get(CAMPAIGN_ENV_VAR) or None
        return cls(
            root,
            campaign_roots=campaign_roots,
            plugin_roots=plugin_roots,
            active_campaign=active_campaign,
        )

    @staticmethod
    def _paths_from_env(value: str | None, *, defaults: tuple[Path, ...]) -> tuple[Path, ...]:
        if not value:
            return defaults
        return tuple(Path(part).expanduser() for part in value.split(os.pathsep) if part)

    @staticmethod
    def _normalize_roots(roots: Iterable[Path | str]) -> tuple[Path, ...]:
        return tuple(Path(root).expanduser().resolve() for root in roots)

    @staticmethod
    def _discover_campaign_directories(roots: Iterable[Path]) -> tuple[str, ...]:
        names: set[str] = set()
        for root in roots:
            if not root.is_dir():
                continue
            for child in root.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    names.add(child.name)
        return tuple(sorted(names))

    def refresh_discovery(self) -> dict[str, Any]:
        """Refresh shallow campaign discovery without importing executable code.

        Campaign directory discovery is a Phase 5 placeholder; Phase 11
        replaces it with the authoritative campaign store. Plugin discovery
        is not refreshable: plugins load once at startup (no hot reload).
        """
        self._campaigns = self._discover_campaign_directories(self.campaign_roots)
        return self.bootstrap_status()

    def _load_plugins(self) -> None:
        """Discover, load, initialize, and register all configured plugins.

        Fail closed: any configured plugin root that is missing, any
        malformed manifest, any incompatible API version, any contract or
        identity failure, and any duplicate system id aborts startup rather
        than silently skipping a configured game system.
        """
        candidates = discover_plugins(self.plugin_roots)
        for candidate in candidates:
            self._registry.register(load_plugin(candidate))

    # -- System introspection -------------------------------------------------

    def _system_record(self, plugin: Any) -> dict[str, Any]:
        return {
            "id": plugin.info.id,
            "name": plugin.info.name,
            "version": plugin.info.version,
            "api_version": plugin.info.api_version,
            "capabilities": sorted(
                capability.value for capability in plugin.capabilities()
            ),
        }

    def systems(self) -> list[dict[str, Any]]:
        """JSON-safe metadata for every registered system, sorted by id."""
        return [self._system_record(plugin) for plugin in self._registry.list()]

    def get_system(self, system_id: str) -> dict[str, Any]:
        """JSON-safe metadata for one registered system."""
        return self._system_record(self._registry.get(system_id))

    def system_capabilities(self, system_id: str) -> list[str]:
        """Sorted capability values of one registered system."""
        return sorted(
            capability.value for capability in self._registry.capabilities(system_id)
        )

    # -- Bootstrap and campaign surface ---------------------------------------

    def bootstrap_status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "operation": "initialize",
            "data": {
                "campaign_roots": [str(path) for path in self.campaign_roots],
                "plugin_roots": [str(path) for path in self.plugin_roots],
                "campaigns": list(self._campaigns),
                "systems": self.systems(),
                "active_campaign": self.active_campaign,
            },
        }

    def current_campaign(self) -> dict[str, Any]:
        if self.active_campaign:
            if self.active_campaign in self._campaigns:
                return self._ok("current-campaign", {"campaign": self.active_campaign})
            return self._error(
                "current-campaign",
                "configured_campaign_not_found",
                "Configured campaign was not discovered.",
                data={"campaign": self.active_campaign, "available": list(self._campaigns)},
            )
        if len(self._campaigns) == 1:
            return self._ok("current-campaign", {"campaign": self._campaigns[0]})
        if not self._campaigns:
            return self._error(
                "current-campaign",
                "campaign_not_configured",
                "No campaign is configured or discoverable.",
                data={"available": []},
            )
        return self._error(
            "current-campaign",
            "campaign_selection_required",
            "Multiple campaigns are discoverable and none is selected.",
            data={"available": list(self._campaigns)},
        )

    def shutdown(self) -> dict[str, Any]:
        """Shut down all plugins; collects but never swallows failures."""
        failures = self._registry.shutdown_all()
        return {
            "ok": not failures,
            "operation": "shutdown",
            "data": {},
            "failures": [
                {"system_id": system_id, "error": repr(exc)} for system_id, exc in failures
            ],
        }

    def current_scene(self) -> dict[str, Any]:
        return self._unavailable("current-scene", phase=11)

    def query_rules(self, query: str) -> dict[str, Any]:
        return self._unavailable("query-rules", phase=20, input_data={"query": query})

    def query_campaign(self, query: str) -> dict[str, Any]:
        return self._unavailable("query-campaign", phase=20, input_data={"query": query})

    def resolve_action(self, action: str) -> dict[str, Any]:
        return self._unavailable("resolve-action", phase=8, input_data={"action": action})

    def roll(self, expression: str) -> dict[str, Any]:
        return self._unavailable("roll", phase=10, input_data={"expression": expression})

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        return self._unavailable("get-entity", phase=11, input_data={"entity_id": entity_id})

    def get_relationships(self, entity_id: str) -> dict[str, Any]:
        return self._unavailable(
            "get-relationships", phase=15, input_data={"entity_id": entity_id}
        )

    def record_ruling(self, ruling: str) -> dict[str, Any]:
        return self._unavailable("record-ruling", phase=23, input_data={"ruling": ruling})

    def end_session(self) -> dict[str, Any]:
        return self._unavailable("end-session", phase=29)

    @staticmethod
    def _ok(operation: str, data: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {"ok": True, "operation": operation, "data": dict(data or {})}

    @staticmethod
    def _error(
        operation: str,
        code: str,
        message: str,
        *,
        data: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "operation": operation,
            "error": {"code": code, "message": message},
            "data": dict(data or {}),
        }

    @classmethod
    def _unavailable(
        cls,
        operation: str,
        *,
        phase: int,
        input_data: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = {"planned_phase": phase}
        if input_data:
            data["input"] = dict(input_data)
        return cls._error(
            operation,
            "capability_unavailable",
            f"{operation} is registered at the Omega boundary but is implemented in Phase {phase}.",
            data=data,
        )
