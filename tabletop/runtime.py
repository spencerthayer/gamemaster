"""Bootstrap facade for the platform-agnostic Tabletop Runtime.

Phase 5 established the runtime lifecycle and Omega adapter boundary; Phase 7
replaced shallow system discovery with the real plugin pipeline: configured
trusted roots -> discover plugin.yaml -> validate manifests -> reject
incompatible API versions -> load entrypoints -> verify the GameSystemPlugin
contract -> cross-check identity -> initialize -> register. Campaign
discovery stays shallow until Phase 11 introduces the authoritative store.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from tabletop.api.errors import DiceExpressionError
from tabletop.api.workspace import Workspace, parse_workspace, skill_registration_entries
from tabletop.campaign.rulings import Ruling, RulingStore
from tabletop.dice.roller import roll as roll_dice
from tabletop.orchestration.session import SessionLifecycle
from tabletop.plugins.discovery import discover_plugins, load_plugin
from tabletop.plugins.registry import PluginRegistry

PLUGIN_PATH_ENV_VAR = "TABLETOP_PLUGIN_PATH"
CAMPAIGN_PATHS_ENV_VAR = "TABLETOP_CAMPAIGN_PATHS"
CAMPAIGN_ENV_VAR = "TABLETOP_CAMPAIGN"
WORKSPACE_ENV_VAR = "TABLETOP_WORKSPACE"


class TabletopRuntime:
    """Runtime facade owning plugin loading, registry, and campaign roots.

    Plugins load once at startup. Adding or removing a plugin requires a
    runtime restart; there is no hot reload. The registry instance lives on
    the runtime, never at module level. The workspace skill surface is fixed
    at construction from ``TABLETOP_WORKSPACE`` (or an explicit argument) and
    cannot be swapped afterward.
    """

    def __init__(
        self,
        repo_root: Path | str,
        *,
        workspace: Workspace,
        campaign_roots: Iterable[Path | str] | None = None,
        plugin_roots: Iterable[Path | str] | None = None,
        active_campaign: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        if not isinstance(workspace, Workspace):
            raise TypeError("workspace must be a Workspace enum member")
        self._workspace = workspace
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
        self._connection = connection
        self._campaigns: tuple[str, ...] = ()
        self._registry = PluginRegistry()
        self.refresh_discovery()
        self._load_plugins()

    @property
    def workspace(self) -> Workspace:
        """Active workspace capability surface. Fixed for this runtime instance."""

        return self._workspace

    def skill_registration_payload(self) -> dict[str, Any]:
        """Return the Omega skill registration list for this workspace only."""

        return {
            "ok": True,
            "workspace": self._workspace.value,
            "skills": list(skill_registration_entries(self._workspace)),
        }

    @classmethod
    def from_environment(
        cls,
        repo_root: Path | str,
        environ: Mapping[str, str] | None = None,
    ) -> "TabletopRuntime":
        """Build runtime bootstrap configuration from environment variables.

        ``TABLETOP_WORKSPACE`` is required (``setting`` or ``campaign``). An
        unset or unknown value fails startup rather than defaulting to the
        larger skill surface. ``TABLETOP_CAMPAIGN_PATHS`` uses the host OS path
        separator. ``TABLETOP_PLUGIN_PATH`` lists external plugin roots (also
        path-separator separated); the built-in ``<repo>/systems`` root is
        always appended. ``TABLETOP_CAMPAIGN`` optionally selects the active
        campaign by discovered directory name. Configured plugin roots that
        do not exist fail startup: a typo in a plugin mount must not
        silently disable a game system.
        """
        env = os.environ if environ is None else environ
        root = Path(repo_root).resolve()
        workspace = parse_workspace(env.get(WORKSPACE_ENV_VAR))

        campaign_roots = cls._paths_from_env(
            env.get(CAMPAIGN_PATHS_ENV_VAR),
            defaults=(root / "campaigns", root / "examples" / "campaigns"),
        )
        env_plugin_roots = cls._paths_from_env(env.get(PLUGIN_PATH_ENV_VAR), defaults=())
        plugin_roots = tuple(dict.fromkeys((*env_plugin_roots, root / "systems")))
        active_campaign = env.get(CAMPAIGN_ENV_VAR) or None
        return cls(
            root,
            workspace=workspace,
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
                "workspace": self._workspace.value,
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
        try:
            result = roll_dice(expression)
        except DiceExpressionError as exc:
            return self._error("roll", exc.code, str(exc))
        return self._ok("roll", result.to_dict())

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        return self._unavailable("get-entity", phase=11, input_data={"entity_id": entity_id})

    def get_relationships(self, entity_id: str) -> dict[str, Any]:
        return self._unavailable(
            "get-relationships", phase=15, input_data={"entity_id": entity_id}
        )

    def record_ruling(self, ruling: Ruling) -> dict[str, Any]:
        """Persist a complete ruling through the canon lifecycle."""
        if not isinstance(ruling, Ruling):
            return self._error(
                "record-ruling",
                "invalid_ruling",
                "record-ruling requires a complete Ruling record.",
            )
        if self._connection is None:
            return self._error(
                "record-ruling",
                "storage_not_configured",
                "record-ruling requires a configured campaign database connection.",
            )
        try:
            stored = RulingStore(self._connection).record(ruling)
        except (LookupError, sqlite3.IntegrityError, ValueError) as exc:
            return self._error("record-ruling", "ruling_not_recorded", str(exc))
        return self._ok("record-ruling", stored.to_dict())

    def end_session(self) -> dict[str, Any]:
        if self._connection is None:
            return self._storage_required("end-session")
        campaign_id = self.active_campaign
        if not campaign_id:
            return self._error(
                "end-session",
                "campaign_not_configured",
                "end-session requires an active campaign.",
            )
        projection_directory = next(
            (
                root / campaign_id
                for root in self.campaign_roots
                if (root / campaign_id).is_dir()
            ),
            self.campaign_roots[0] / campaign_id,
        )
        try:
            session = SessionLifecycle(
                self._connection,
                projection_directory,
            ).end_session(campaign_id)
        except (LookupError, OSError, sqlite3.Error, ValueError) as exc:
            return self._error(
                "end-session",
                "session_not_ended",
                str(exc),
                data={"campaign_id": campaign_id},
            )
        return self._ok("end-session", {"session": session.to_dict()})

    # -- Setting workspace surface (task 10 store + task 12 history) ---------

    def query_setting(self, query: str) -> dict[str, Any]:
        """Look up setting metadata by id or name fragment (task 10 schema)."""
        if self._connection is None:
            return self._storage_required("query-setting")
        needle = query.strip()
        if not needle:
            rows = self._connection.execute(
                "SELECT setting_id, name, created_at FROM settings "
                "ORDER BY created_at, setting_id"
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT setting_id, name, created_at FROM settings "
                "WHERE setting_id = ? OR name LIKE ? "
                "ORDER BY created_at, setting_id",
                (needle, f"%{needle}%"),
            ).fetchall()
        return self._ok(
            "query-setting",
            {"settings": [dict(row) for row in rows], "query": needle},
        )

    def edit_setting(self, edit: str) -> dict[str, Any]:
        """Create or rename a setting from a JSON object string (task 10)."""
        if self._connection is None:
            return self._storage_required("edit-setting")
        try:
            payload = json.loads(edit) if edit.strip() else {}
        except json.JSONDecodeError as exc:
            return self._error("edit-setting", "invalid_edit", str(exc))
        if not isinstance(payload, dict):
            return self._error(
                "edit-setting",
                "invalid_edit",
                "edit-setting expects a JSON object.",
            )
        setting_id = str(payload.get("setting_id", "")).strip()
        name = str(payload.get("name", "")).strip()
        if not setting_id or not name:
            return self._error(
                "edit-setting",
                "invalid_edit",
                "edit-setting requires setting_id and name.",
            )
        created_at = str(payload.get("created_at") or datetime.now(timezone.utc).isoformat())
        existing = self._connection.execute(
            "SELECT setting_id FROM settings WHERE setting_id = ?",
            (setting_id,),
        ).fetchone()
        if existing is None:
            self._connection.execute(
                "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
                (setting_id, name, created_at),
            )
        else:
            self._connection.execute(
                "UPDATE settings SET name = ? WHERE setting_id = ?",
                (name, setting_id),
            )
        self._connection.commit()
        row = self._connection.execute(
            "SELECT setting_id, name, created_at FROM settings WHERE setting_id = ?",
            (setting_id,),
        ).fetchone()
        return self._ok("edit-setting", {"setting": dict(row)})

    def get_world_entity(self, entity_id: str) -> dict[str, Any]:
        """Return one setting-owned entity (task 10 ownership model)."""
        if self._connection is None:
            return self._storage_required("get-world-entity")
        entity_key = entity_id.strip()
        if not entity_key:
            return self._error(
                "get-world-entity",
                "invalid_entity_id",
                "get-world-entity requires a non-empty entity id.",
            )
        row = self._connection.execute(
            "SELECT entity_id, owner_scope, setting_id, campaign_id, overrides_id, "
            "entity_type, name, system_state, metadata FROM entities "
            "WHERE owner_scope = 'setting' AND entity_id = ?",
            (entity_key,),
        ).fetchone()
        if row is None:
            return self._error(
                "get-world-entity",
                "entity_not_found",
                "Setting-owned entity was not found.",
                data={"entity_id": entity_key},
            )
        result = dict(row)
        result["system_state"] = json.loads(result["system_state"])
        result["metadata"] = json.loads(result["metadata"])
        return self._ok("get-world-entity", {"entity": result})

    def upsert_world_entity(self, entity: str) -> dict[str, Any]:
        """Create or update a setting-owned world entity (task 10)."""
        if self._connection is None:
            return self._storage_required("upsert-world-entity")
        try:
            payload = json.loads(entity) if entity.strip() else {}
        except json.JSONDecodeError as exc:
            return self._error("upsert-world-entity", "invalid_entity", str(exc))
        if not isinstance(payload, dict):
            return self._error(
                "upsert-world-entity",
                "invalid_entity",
                "upsert-world-entity expects a JSON object.",
            )
        entity_id = str(payload.get("entity_id", "")).strip()
        setting_id = str(payload.get("setting_id", "")).strip()
        name = str(payload.get("name", "")).strip()
        if not entity_id or not setting_id or not name:
            return self._error(
                "upsert-world-entity",
                "invalid_entity",
                "upsert-world-entity requires entity_id, setting_id, and name.",
            )
        entity_type = payload.get("entity_type")
        system_state = json.dumps(payload.get("system_state") or {}, separators=(",", ":"))
        metadata = json.dumps(payload.get("metadata") or {}, separators=(",", ":"))
        overrides_id = payload.get("overrides_id")
        cursor = self._connection.execute(
            "UPDATE entities SET entity_type = ?, name = ?, system_state = ?, "
            "metadata = ?, overrides_id = ? "
            "WHERE owner_scope = 'setting' AND setting_id = ? AND entity_id = ?",
            (
                entity_type,
                name,
                system_state,
                metadata,
                overrides_id,
                setting_id,
                entity_id,
            ),
        )
        if cursor.rowcount == 0:
            self._connection.execute(
                "INSERT INTO entities "
                "(entity_id, owner_scope, setting_id, campaign_id, overrides_id, "
                "entity_type, name, system_state, metadata) "
                "VALUES (?, 'setting', ?, NULL, ?, ?, ?, ?, ?)",
                (
                    entity_id,
                    setting_id,
                    overrides_id,
                    entity_type,
                    name,
                    system_state,
                    metadata,
                ),
            )
        self._connection.commit()
        return self.get_world_entity(entity_id)

    def query_world_history(self, query: str) -> dict[str, Any]:
        """Query setting-scoped facts as world history (tasks 10 and 12)."""
        if self._connection is None:
            return self._storage_required("query-world-history")
        needle = query.strip()
        if not needle:
            rows = self._connection.execute(
                "SELECT fact_id, setting_id, subject_id, predicate, value, "
                "canon_state, knowledge_state, created_at FROM facts "
                "WHERE fact_scope = 'setting' "
                "ORDER BY created_at, fact_id LIMIT 50"
            ).fetchall()
        else:
            like = f"%{needle}%"
            rows = self._connection.execute(
                "SELECT fact_id, setting_id, subject_id, predicate, value, "
                "canon_state, knowledge_state, created_at FROM facts "
                "WHERE fact_scope = 'setting' AND "
                "(subject_id LIKE ? OR predicate LIKE ? OR value LIKE ? "
                "OR setting_id = ?) "
                "ORDER BY created_at, fact_id LIMIT 50",
                (like, like, like, needle),
            ).fetchall()
        return self._ok(
            "query-world-history",
            {"facts": [dict(row) for row in rows], "query": needle},
        )

    def record_world_history(self, entry: str) -> dict[str, Any]:
        """Append a setting-scoped fact used as world history (task 10)."""
        if self._connection is None:
            return self._storage_required("record-world-history")
        try:
            payload = json.loads(entry) if entry.strip() else {}
        except json.JSONDecodeError as exc:
            return self._error("record-world-history", "invalid_entry", str(exc))
        if not isinstance(payload, dict):
            return self._error(
                "record-world-history",
                "invalid_entry",
                "record-world-history expects a JSON object.",
            )
        required = ("fact_id", "setting_id", "subject_id", "predicate", "value")
        missing = [key for key in required if not str(payload.get(key, "")).strip()]
        if missing:
            return self._error(
                "record-world-history",
                "invalid_entry",
                f"record-world-history requires {', '.join(required)}.",
            )
        created_at = str(
            payload.get("created_at") or datetime.now(timezone.utc).isoformat()
        )
        try:
            self._connection.execute(
                "INSERT INTO facts "
                "(fact_id, fact_scope, setting_id, campaign_id, subject_id, predicate, "
                "value, canon_state, knowledge_state, visibility, valid_from, "
                "valid_until, source_document_id, source_chunk_id, import_job_id, "
                "extraction_method, source_ownership, created_at) "
                "VALUES (?, 'setting', ?, NULL, ?, ?, ?, ?, ?, ?, NULL, NULL, "
                "NULL, NULL, NULL, NULL, NULL, ?)",
                (
                    str(payload["fact_id"]).strip(),
                    str(payload["setting_id"]).strip(),
                    str(payload["subject_id"]).strip(),
                    str(payload["predicate"]).strip(),
                    str(payload["value"]).strip(),
                    str(payload.get("canon_state") or "confirmed"),
                    str(payload.get("knowledge_state") or "known"),
                    str(payload.get("visibility") or "PUBLIC"),
                    created_at,
                ),
            )
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            return self._error("record-world-history", "history_not_recorded", str(exc))
        return self._ok(
            "record-world-history",
            {
                "fact_id": str(payload["fact_id"]).strip(),
                "setting_id": str(payload["setting_id"]).strip(),
            },
        )

    # -- Campaign-only surface helpers ----------------------------------------

    def read_session(self, session_id: str) -> dict[str, Any]:
        """Read one session row from the campaign store (task 10 schema)."""
        if self._connection is None:
            return self._storage_required("read-session")
        key = session_id.strip()
        if not key:
            return self._error(
                "read-session",
                "invalid_session_id",
                "read-session requires a non-empty session id.",
            )
        row = self._connection.execute(
            "SELECT session_id, campaign_id, started_at, ended_at, participants, "
            "transcript_reference, event_start_sequence, event_end_sequence, "
            "summary, important_facts, open_threads "
            "FROM sessions WHERE session_id = ?",
            (key,),
        ).fetchone()
        if row is None:
            return self._error(
                "read-session",
                "session_not_found",
                "Session was not found.",
                data={"session_id": key},
            )
        result = dict(row)
        result["participants"] = json.loads(result["participants"])
        result["event_range"] = {
            "start": result.pop("event_start_sequence"),
            "end": result.pop("event_end_sequence"),
        }
        result["important_facts"] = json.loads(result["important_facts"])
        result["open_threads"] = json.loads(result["open_threads"])
        return self._ok("read-session", {"session": result})

    def get_party_state(self) -> dict[str, Any]:
        """Return party membership recorded on the active campaign system state."""
        if self._connection is None:
            return self._storage_required("get-party-state")
        campaign_id = self.active_campaign
        if not campaign_id:
            return self._error(
                "get-party-state",
                "campaign_not_configured",
                "get-party-state requires an active campaign.",
            )
        row = self._connection.execute(
            "SELECT system_state FROM campaigns WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        if row is None:
            return self._error(
                "get-party-state",
                "campaign_not_found",
                "Active campaign was not found.",
                data={"campaign": campaign_id},
            )
        state = json.loads(row["system_state"])
        party = state.get("party") if isinstance(state, dict) else None
        return self._ok(
            "get-party-state",
            {"campaign_id": campaign_id, "party": party if party is not None else []},
        )

    def get_open_threads(self) -> dict[str, Any]:
        """Return open threads recorded on the active campaign system state."""
        if self._connection is None:
            return self._storage_required("get-open-threads")
        campaign_id = self.active_campaign
        if not campaign_id:
            return self._error(
                "get-open-threads",
                "campaign_not_configured",
                "get-open-threads requires an active campaign.",
            )
        row = self._connection.execute(
            "SELECT system_state FROM campaigns WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        if row is None:
            return self._error(
                "get-open-threads",
                "campaign_not_found",
                "Active campaign was not found.",
                data={"campaign": campaign_id},
            )
        state = json.loads(row["system_state"])
        threads = state.get("open_threads") if isinstance(state, dict) else None
        return self._ok(
            "get-open-threads",
            {
                "campaign_id": campaign_id,
                "open_threads": threads if threads is not None else [],
            },
        )

    def mutate_quest(self, quest: str) -> dict[str, Any]:
        """Create or update a quest entry on campaign system state."""
        if self._connection is None:
            return self._storage_required("mutate-quest")
        campaign_id = self.active_campaign
        if not campaign_id:
            return self._error(
                "mutate-quest",
                "campaign_not_configured",
                "mutate-quest requires an active campaign.",
            )
        try:
            payload = json.loads(quest) if quest.strip() else {}
        except json.JSONDecodeError as exc:
            return self._error("mutate-quest", "invalid_quest", str(exc))
        if not isinstance(payload, dict):
            return self._error(
                "mutate-quest",
                "invalid_quest",
                "mutate-quest expects a JSON object.",
            )
        quest_id = str(payload.get("quest_id", "")).strip()
        if not quest_id:
            return self._error(
                "mutate-quest",
                "invalid_quest",
                "mutate-quest requires quest_id.",
            )
        row = self._connection.execute(
            "SELECT system_state FROM campaigns WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        if row is None:
            return self._error(
                "mutate-quest",
                "campaign_not_found",
                "Active campaign was not found.",
                data={"campaign": campaign_id},
            )
        state = json.loads(row["system_state"])
        if not isinstance(state, dict):
            state = {}
        quests = state.get("quests")
        if not isinstance(quests, dict):
            quests = {}
        quests[quest_id] = payload
        state["quests"] = quests
        self._connection.execute(
            "UPDATE campaigns SET system_state = ? WHERE campaign_id = ?",
            (json.dumps(state, separators=(",", ":")), campaign_id),
        )
        self._connection.commit()
        return self._ok("mutate-quest", {"campaign_id": campaign_id, "quest": payload})

    def read_campaign_secret(self, secret_id: str) -> dict[str, Any]:
        """Read one GM-visibility fact treated as a campaign secret (task 10)."""
        if self._connection is None:
            return self._storage_required("read-campaign-secret")
        key = secret_id.strip()
        if not key:
            return self._error(
                "read-campaign-secret",
                "invalid_secret_id",
                "read-campaign-secret requires a non-empty secret id.",
            )
        row = self._connection.execute(
            "SELECT fact_id, campaign_id, subject_id, predicate, value, visibility, "
            "canon_state, knowledge_state FROM facts "
            "WHERE fact_id = ? AND fact_scope = 'campaign' AND visibility = 'GM'",
            (key,),
        ).fetchone()
        if row is None:
            return self._error(
                "read-campaign-secret",
                "secret_not_found",
                "Campaign secret was not found.",
                data={"secret_id": key},
            )
        return self._ok("read-campaign-secret", {"secret": dict(row)})

    def _storage_required(self, operation: str) -> dict[str, Any]:
        return self._error(
            operation,
            "storage_not_configured",
            f"{operation} requires a configured campaign database connection.",
        )

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
