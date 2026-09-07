"""Bootstrap facade for the platform-agnostic Tabletop Runtime.

Phase 5 establishes the runtime lifecycle and Omega adapter boundary only.
Subsequent phases replace the shallow discovery and capability placeholders
with the real plugin API, registry, persistence, retrieval, mechanics, and
session implementations.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Mapping


class TabletopRuntime:
    """Minimal runtime facade initialized by the Omega-facing adapter.

    The facade deliberately owns no game-system semantics. It provides stable
    methods for Omega skills while later phases implement the subsystems behind
    those methods.
    """

    def __init__(
        self,
        repo_root: Path | str,
        *,
        campaign_roots: Iterable[Path | str] | None = None,
        system_roots: Iterable[Path | str] | None = None,
        active_campaign: str | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.campaign_roots = self._normalize_roots(
            campaign_roots
            if campaign_roots is not None
            else (self.repo_root / "campaigns", self.repo_root / "examples" / "campaigns")
        )
        self.system_roots = self._normalize_roots(
            system_roots if system_roots is not None else (self.repo_root / "systems",)
        )
        self.active_campaign = active_campaign
        self._campaigns: tuple[str, ...] = ()
        self._systems: tuple[str, ...] = ()
        self.refresh_discovery()

    @classmethod
    def from_environment(
        cls,
        repo_root: Path | str,
        environ: Mapping[str, str] | None = None,
    ) -> "TabletopRuntime":
        """Build runtime bootstrap configuration from environment variables.

        ``TABLETOP_CAMPAIGN_PATHS`` and ``TABLETOP_SYSTEM_PATHS`` use the host
        OS path separator. ``TABLETOP_CAMPAIGN`` optionally selects the active
        campaign by discovered directory name.
        """
        env = os.environ if environ is None else environ
        root = Path(repo_root).resolve()

        campaign_roots = cls._paths_from_env(
            env.get("TABLETOP_CAMPAIGN_PATHS"),
            defaults=(root / "campaigns", root / "examples" / "campaigns"),
        )
        system_roots = cls._paths_from_env(
            env.get("TABLETOP_SYSTEM_PATHS"),
            defaults=(root / "systems",),
        )
        active_campaign = env.get("TABLETOP_CAMPAIGN") or None
        return cls(
            root,
            campaign_roots=campaign_roots,
            system_roots=system_roots,
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
    def _discover_directories(roots: Iterable[Path], *, require_init: bool = False) -> tuple[str, ...]:
        names: set[str] = set()
        for root in roots:
            if not root.is_dir():
                continue
            for child in root.iterdir():
                if not child.is_dir() or child.name.startswith("."):
                    continue
                if require_init and not (child / "__init__.py").is_file():
                    continue
                names.add(child.name)
        return tuple(sorted(names))

    def refresh_discovery(self) -> dict[str, Any]:
        """Refresh shallow bootstrap discovery without importing executable code.

        Phase 7 replaces system discovery with manifest validation and a
        registry. Phase 11 replaces campaign directory discovery with the
        authoritative campaign store.
        """
        self._campaigns = self._discover_directories(self.campaign_roots)
        self._systems = self._discover_directories(self.system_roots, require_init=True)
        return self.bootstrap_status()

    def bootstrap_status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "operation": "initialize",
            "data": {
                "campaign_roots": [str(path) for path in self.campaign_roots],
                "system_roots": [str(path) for path in self.system_roots],
                "campaigns": list(self._campaigns),
                "systems": list(self._systems),
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
