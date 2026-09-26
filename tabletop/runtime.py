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
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from tabletop.api.errors import (
    DiceExpressionError,
    FactInvariantError,
    GameSystemError,
    InvalidActionError,
    InvalidResolutionError,
    StorageError,
)
from tabletop.api.actions import (
    ActionProposal,
    MechanicalParameter,
    ParameterSource,
    parse_action_proposal,
)
from tabletop.api.events import GameEvent
from tabletop.api.resolution import StateChange, StateOperation
from tabletop.api.visibility import Viewpoint, gm_viewpoint, parse_scope
from tabletop.api.workspace import Workspace, parse_workspace, skill_registration_entries
from tabletop.campaign.event_store import (
    EventStore,
    EventType,
    close_scene_event,
    entity_entered_event,
    entity_exited_event,
    open_scene_event,
    promote_fact as promote_campaign_fact,
    reveal_fact as reveal_campaign_fact,
    scene_time_changed_event,
)
from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.models import (
    CanonState,
    Fact,
    FactScope,
    GameTime,
    KnowledgeState,
    PresenceType,
    Scene,
    SceneInvariantError,
    SceneMember,
)
from tabletop.campaign.relationships import resolve_relationship_overlay
from tabletop.campaign.rulings import Ruling, RulingStore, ruling_from_mapping
from tabletop.campaign.selection import read_active_campaign_file
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.sender_binding import (
    verify_startup_binding,
    verify_turn_sender,
)
from tabletop.campaign.setting_events import SettingEventStore, SettingEventType
from tabletop.campaign.store import CampaignStore
from tabletop.dice.roller import roll as roll_dice
from tabletop.orchestration.clarification import (
    RuleLookup,
    RuleSource,
    StateLookup,
    resolve_lookup,
)
from tabletop.orchestration.planner import Disposition, PlanContext, plan_resolution
from tabletop.orchestration.prompt_context import (
    PromptContextSnapshot,
    build_player_prompt_context_snapshot,
    build_prompt_context_snapshot,
)
from tabletop.orchestration.prompt_receipt import record_prompt_context_receipt
from tabletop.orchestration.session import SessionLifecycle
from tabletop.orchestration.turn import parse_game_action, play_turn
from tabletop.plugins.discovery import discover_plugins, load_plugin
from tabletop.plugins.registry import PluginRegistry
from tabletop.retrieval.lexical import LexicalRetriever, load_visible_chunk
from tabletop.retrieval.models import RetrievalFilters, RetrievalNamespace
from tabletop.storage.sqlite import connect as connect_database
from tabletop.storage.sqlite import migrate, transaction

logger = logging.getLogger(__name__)

PLUGIN_PATH_ENV_VAR = "TABLETOP_PLUGIN_PATH"
CAMPAIGN_PATHS_ENV_VAR = "TABLETOP_CAMPAIGN_PATHS"
CAMPAIGN_ENV_VAR = "TABLETOP_CAMPAIGN"
WORKSPACE_ENV_VAR = "TABLETOP_WORKSPACE"
DATABASE_PATH_ENV_VAR = "TABLETOP_DATABASE_PATH"
PARTICIPANT_ENV_VAR = "TABLETOP_PARTICIPANT"


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
        participant_id: str | None = None,
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
        self.participant_id = participant_id
        self._connection = connection
        self._campaigns: tuple[str, ...] = ()
        self._registry = PluginRegistry()
        self.refresh_discovery()
        self._load_plugins()

    @property
    def workspace(self) -> Workspace:
        """Active workspace capability surface. Fixed for this runtime instance."""

        return self._workspace

    def prompt_context_snapshot(self) -> PromptContextSnapshot:
        """Return the prompt snapshot for this process. Writes nothing."""

        if self._workspace is Workspace.PLAYER:
            return build_player_prompt_context_snapshot(
                self._connection,
                workspace=self._workspace,
                campaign_id=self.active_campaign,
                setting_id=self._owned_setting_id(),
                viewpoint=self._player_viewpoint(),
            )
        return build_prompt_context_snapshot(
            self._connection,
            workspace=self._workspace,
            campaign_id=self.active_campaign,
            setting_id=self._owned_setting_id(),
        )

    def _player_viewpoint(self) -> Viewpoint:
        if self._connection is None or not self.active_campaign or not self.participant_id:
            return Viewpoint(scope=parse_scope("PUBLIC"))
        character_ids = MembershipStore(self._connection).controlled_entity_ids(
            self.active_campaign,
            self.participant_id,
        )
        return Viewpoint(
            scope=parse_scope("PUBLIC"),
            character_ids=character_ids,
        )

    def _active_viewpoint(self) -> Viewpoint:
        if self._workspace is Workspace.PLAYER:
            return self._player_viewpoint()
        return gm_viewpoint()

    def prompt_context_snapshot_with_receipt(self) -> str:
        """Build one snapshot, store its receipt, and return the snapshot text.

        Receipt storage uses this runtime's connection. A receipt failure
        does not change the returned text.
        """

        snapshot = self.prompt_context_snapshot()
        if self._connection is not None:
            try:
                record_prompt_context_receipt(self._connection, snapshot)
            except Exception as exc:
                logger.warning(
                    "tabletop context receipt failed: %s",
                    type(exc).__name__,
                )
        return snapshot.text

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
        participant_id = env.get(PARTICIPANT_ENV_VAR) or None
        connection = None
        database_path_value = env.get(DATABASE_PATH_ENV_VAR)
        if database_path_value:
            database_path = Path(database_path_value).expanduser()
            database_path.parent.mkdir(parents=True, exist_ok=True)
            if not active_campaign:
                try:
                    active_campaign = read_active_campaign_file(database_path)
                except ValueError as exc:
                    raise StorageError(str(exc)) from exc
            connection = connect_database(database_path)
            try:
                migrate(connection)
            except BaseException:
                connection.close()
                raise
        try:
            runtime = cls(
                root,
                workspace=workspace,
                campaign_roots=campaign_roots,
                plugin_roots=plugin_roots,
                active_campaign=active_campaign,
                connection=connection,
                participant_id=participant_id,
            )
            if connection is not None and participant_id:
                verify_startup_binding(
                    connection,
                    workspace=workspace,
                    campaign_id=active_campaign,
                    participant_id=participant_id,
                    environ=env,
                )
            return runtime
        except BaseException:
            if connection is not None:
                connection.close()
            raise

    def authorize_channel_turn(
        self,
        authenticated_sender: str | None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """Return an error envelope when a channel turn must not run a skill."""

        if self._connection is None:
            return None
        code = verify_turn_sender(
            self._connection,
            workspace=self._workspace,
            campaign_id=self.active_campaign,
            participant_id=self.participant_id,
            authenticated_sender=authenticated_sender,
            environ=environ,
        )
        if code is None:
            return None
        return self._error(
            "authorize-turn",
            code,
            f"channel turn rejected: {code}",
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
        if self._connection is None:
            return self._storage_required("current-campaign")
        store = CampaignStore(self._connection)
        available = [row["campaign_id"] for row in store.list_campaigns()]
        if self.active_campaign:
            if store.get_campaign(self.active_campaign) is not None:
                return self._ok("current-campaign", {"campaign": self.active_campaign})
            return self._error(
                "current-campaign",
                "campaign_not_found",
                "Configured campaign was not found in SQLite.",
                data={"campaign": self.active_campaign, "available": available},
            )
        if len(available) == 1:
            return self._ok("current-campaign", {"campaign": available[0]})
        if not available:
            return self._error(
                "current-campaign",
                "campaign_not_configured",
                "No campaign is configured or stored.",
                data={"available": []},
            )
        return self._error(
            "current-campaign",
            "campaign_selection_required",
            "Multiple campaigns are stored and none is selected.",
            data={"available": available},
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

    # -- scene lifecycle -------------------------------------------------
    #
    # Every method below composes SceneStore, EventStore, and the existing
    # campaign archive check. There is deliberately no second lifecycle
    # service: these are the only writers of the scene tables, and each one
    # moves rows and events in one transaction.

    def open_scene(self, payload_json: str) -> dict[str, Any]:
        """Open one scene and record ``scene.opened`` atomically."""

        return self._scene_operation("open-scene", payload_json, self._do_open_scene)

    def close_scene(self, payload_json: str) -> dict[str, Any]:
        """Close one open scene, exiting everyone still present."""

        return self._scene_operation("close-scene", payload_json, self._do_close_scene)

    def transition_scene(self, payload_json: str) -> dict[str, Any]:
        """Close scene A and open scene B in one transaction.

        Either both happen or neither does: a campaign must never be left
        with no open scene because the second half of the move failed.
        """

        return self._scene_operation(
            "transition-scene", payload_json, self._do_transition_scene
        )

    def get_current_scene(self, payload_json: str = "{}") -> dict[str, Any]:
        """Return the campaign's open scene with its present entities."""

        return self._scene_operation("current-scene", payload_json, self._do_current_scene)

    def enter_scene(self, payload_json: str) -> dict[str, Any]:
        """Record one presence interval in an open scene."""

        return self._scene_operation("enter-scene", payload_json, self._do_enter_scene)

    def exit_scene(self, payload_json: str) -> dict[str, Any]:
        """End one presence interval in an open scene."""

        return self._scene_operation("exit-scene", payload_json, self._do_exit_scene)

    def get_game_time(self, payload_json: str = "{}") -> dict[str, Any]:
        """Return the campaign in-world clock, or None when never set."""

        return self._scene_operation("game-time", payload_json, self._do_get_game_time)

    def set_game_time(self, payload_json: str) -> dict[str, Any]:
        """Set the campaign in-world clock and record ``scene.time_changed``."""

        return self._scene_operation("set-game-time", payload_json, self._do_set_game_time)

    def _do_open_scene(
        self, payload: Mapping[str, Any], campaign_id: str
    ) -> dict[str, Any]:
        scene_id = _required_string(payload, "scene_id", "open-scene")
        name = _required_string(payload, "name", "open-scene")
        if scene_id is None or name is None:
            return self._error(
                "open-scene",
                "invalid_payload",
                "open-scene requires non-empty scene_id and name.",
            )
        with transaction(self._connection):
            scene = SceneStore(self._connection).open_scene_in_transaction(
                campaign_id,
                scene_id,
                name,
                session_id=payload.get("session_id"),
                location_entity_id=payload.get("location_entity_id"),
                in_world_started_at=payload.get("in_world_started_at"),
            )
            self._append_scene_event(
                campaign_id,
                open_scene_event(
                    scene_id=scene.scene_id,
                    name=scene.name,
                    started_at=scene.started_at,
                    session_id=scene.session_id,
                    location_entity_id=scene.location_entity_id,
                    in_world_started_at=scene.in_world_started_at,
                ),
                session_id=scene.session_id,
                scene_id=scene.scene_id,
                occurred_at=scene.started_at,
            )
        return self._ok("open-scene", {"scene": _scene_to_dict(scene)})

    def _do_close_scene(
        self, payload: Mapping[str, Any], campaign_id: str
    ) -> dict[str, Any]:
        scene_id = _required_string(payload, "scene_id", "close-scene")
        if scene_id is None:
            return self._error(
                "close-scene", "invalid_payload", "close-scene requires scene_id."
            )
        with transaction(self._connection):
            store = SceneStore(self._connection)
            present = store.get_present_entity_ids(campaign_id, scene_id)
            scene = store.close_scene_in_transaction(campaign_id, scene_id)
            self._append_scene_event(
                campaign_id,
                close_scene_event(
                    scene_id=scene.scene_id,
                    ended_at=scene.ended_at or "",
                    exited_entity_ids=present,
                    in_world_ended_at=scene.in_world_ended_at,
                ),
                session_id=scene.session_id,
                scene_id=scene.scene_id,
                occurred_at=scene.ended_at,
            )
        return self._ok("close-scene", {"scene": _scene_to_dict(scene)})

    def _do_transition_scene(
        self, payload: Mapping[str, Any], campaign_id: str
    ) -> dict[str, Any]:
        scene_id = _required_string(payload, "scene_id", "transition-scene")
        name = _required_string(payload, "name", "transition-scene")
        if scene_id is None or name is None:
            return self._error(
                "transition-scene",
                "invalid_payload",
                "transition-scene requires non-empty scene_id and name.",
            )
        from_scene_id = payload.get("from_scene_id")
        closed_id: str | None = None
        with transaction(self._connection):
            store = SceneStore(self._connection)
            current = store.get_open_scene(campaign_id)
            if isinstance(from_scene_id, str) and current is not None:
                if current.scene_id != from_scene_id:
                    raise SceneInvariantError(
                        f"open scene is {current.scene_id!r}, not {from_scene_id!r}"
                    )
            if current is not None:
                present = store.get_present_entity_ids(campaign_id, current.scene_id)
                closed = store.close_scene_in_transaction(campaign_id, current.scene_id)
                self._append_scene_event(
                    campaign_id,
                    close_scene_event(
                        scene_id=closed.scene_id,
                        ended_at=closed.ended_at or "",
                        exited_entity_ids=present,
                        in_world_ended_at=closed.in_world_ended_at,
                    ),
                    session_id=closed.session_id,
                    scene_id=closed.scene_id,
                    occurred_at=closed.ended_at,
                )
                closed_id = closed.scene_id
            opened = store.open_scene_in_transaction(
                campaign_id,
                scene_id,
                name,
                session_id=payload.get("session_id"),
                location_entity_id=payload.get("location_entity_id"),
                in_world_started_at=payload.get("in_world_started_at"),
            )
            self._append_scene_event(
                campaign_id,
                open_scene_event(
                    scene_id=opened.scene_id,
                    name=opened.name,
                    started_at=opened.started_at,
                    session_id=opened.session_id,
                    location_entity_id=opened.location_entity_id,
                    in_world_started_at=opened.in_world_started_at,
                ),
                session_id=opened.session_id,
                scene_id=opened.scene_id,
                occurred_at=opened.started_at,
            )
        return self._ok(
            "transition-scene",
            {
                "scene": _scene_to_dict(opened),
                "closed_scene_id": closed_id,
            },
        )

    def _do_current_scene(
        self, _payload: Mapping[str, Any], campaign_id: str
    ) -> dict[str, Any]:
        store = SceneStore(self._connection)
        scene = store.get_open_scene(campaign_id)
        return self._ok(
            "current-scene",
            {
                "scene": None if scene is None else _scene_to_dict(scene),
                "present_entity_ids": (
                    []
                    if scene is None
                    else list(store.get_present_entity_ids(campaign_id, scene.scene_id))
                ),
                "game_time": _game_time_to_dict(store.get_game_time(campaign_id)),
            },
        )

    def _do_enter_scene(
        self, payload: Mapping[str, Any], campaign_id: str
    ) -> dict[str, Any]:
        scene_id = _required_string(payload, "scene_id", "enter-scene")
        entity_id = _required_string(payload, "entity_id", "enter-scene")
        if scene_id is None or entity_id is None:
            return self._error(
                "enter-scene",
                "invalid_payload",
                "enter-scene requires scene_id and entity_id.",
            )
        raw_presence = payload.get("presence_type")
        try:
            presence_type = PresenceType(raw_presence)
        except ValueError:
            return self._error(
                "enter-scene",
                "invalid_presence_type",
                "presence_type must be one of "
                + ", ".join(item.value for item in PresenceType)
                + ".",
                data={"presence_type": raw_presence},
            )
        with transaction(self._connection):
            store = SceneStore(self._connection)
            member = store.enter_in_transaction(
                campaign_id, scene_id, entity_id, presence_type
            )
            self._append_scene_event(
                campaign_id,
                entity_entered_event(
                    scene_id=scene_id,
                    entity_id=entity_id,
                    presence_type=presence_type.value,
                    entered_at=member.entered_at,
                ),
                scene_id=scene_id,
                occurred_at=member.entered_at,
            )
        return self._ok("enter-scene", {"member": _member_to_dict(member)})

    def _do_exit_scene(
        self, payload: Mapping[str, Any], campaign_id: str
    ) -> dict[str, Any]:
        scene_id = _required_string(payload, "scene_id", "exit-scene")
        entity_id = _required_string(payload, "entity_id", "exit-scene")
        if scene_id is None or entity_id is None:
            return self._error(
                "exit-scene",
                "invalid_payload",
                "exit-scene requires scene_id and entity_id.",
            )
        with transaction(self._connection):
            store = SceneStore(self._connection)
            member = store.exit_in_transaction(campaign_id, scene_id, entity_id)
            self._append_scene_event(
                campaign_id,
                entity_exited_event(
                    scene_id=scene_id,
                    entity_id=entity_id,
                    exited_at=member.exited_at or "",
                ),
                scene_id=scene_id,
                occurred_at=member.exited_at,
            )
        return self._ok("exit-scene", {"member": _member_to_dict(member)})

    def _do_get_game_time(
        self, _payload: Mapping[str, Any], campaign_id: str
    ) -> dict[str, Any]:
        clock = SceneStore(self._connection).get_game_time(campaign_id)
        return self._ok("game-time", {"game_time": _game_time_to_dict(clock)})

    def _do_set_game_time(
        self, payload: Mapping[str, Any], campaign_id: str
    ) -> dict[str, Any]:
        raw_minutes = payload.get("in_world_minutes")
        if raw_minutes is not None and (
            isinstance(raw_minutes, bool) or not isinstance(raw_minutes, int)
        ):
            return self._error(
                "set-game-time",
                "invalid_game_time",
                "in_world_minutes must be an integer when provided.",
                data={"in_world_minutes": raw_minutes},
            )
        with transaction(self._connection):
            clock = SceneStore(self._connection).set_game_time_in_transaction(
                campaign_id,
                in_world_label=payload.get("in_world_label"),
                in_world_minutes=raw_minutes,
            )
            self._append_scene_event(
                campaign_id,
                scene_time_changed_event(
                    in_world_label=clock.in_world_label,
                    in_world_minutes=clock.in_world_minutes,
                    changed_at=clock.updated_at,
                ),
                occurred_at=clock.updated_at,
            )
        return self._ok("set-game-time", {"game_time": _game_time_to_dict(clock)})

    def _append_scene_event(
        self,
        campaign_id: str,
        event: GameEvent,
        *,
        session_id: str | None = None,
        scene_id: str | None = None,
        occurred_at: str | None = None,
    ) -> None:
        EventStore(self._connection).append_in_transaction(
            self._connection,
            campaign_id,
            event,
            session_id=session_id,
            scene_id=scene_id,
            occurred_at=occurred_at,
        )

    def _scene_operation(
        self,
        operation: str,
        payload_json: str,
        action: Callable[[Mapping[str, Any], str], dict[str, Any]],
    ) -> dict[str, Any]:
        """Resolve storage, campaign, and payload once, then run one action.

        Guard errors (missing storage, no campaign, archived campaign, bad
        JSON) are decided before any write, so a rejected call never opens a
        transaction.
        """

        if self._connection is None:
            return self._storage_required(operation)
        campaign_id = self.active_campaign
        if not campaign_id:
            return self._error(
                operation,
                "campaign_not_configured",
                f"{operation} requires an active campaign.",
            )
        try:
            payload = _scene_payload(payload_json)
        except ValueError as exc:
            return self._error(operation, "invalid_payload", str(exc))
        campaign = CampaignStore(self._connection).get_campaign(campaign_id)
        if campaign is None:
            return self._error(
                operation,
                "campaign_not_found",
                "Active campaign was not found.",
                data={"campaign": campaign_id},
            )
        if campaign.get("archived_at"):
            return self._error(
                operation,
                "campaign_archived",
                "Active campaign is archived.",
                data={"campaign": campaign_id},
            )
        try:
            return action(payload, campaign_id)
        except SceneInvariantError as exc:
            return self._error(
                operation,
                "scene_invalid",
                str(exc),
                data={"campaign_id": campaign_id},
            )

    def query_rules(
        self,
        query: str,
        *,
        content_pack_id: str | None = None,
        system_id: str | None = None,
        visibility: str | None = None,
    ) -> dict[str, Any]:
        """Search system rules with optional campaign/content-pack scoping."""
        if self._connection is None:
            return self._storage_required("query-rules")
        needle = query.strip()
        if not needle:
            return self._error(
                "query-rules",
                "invalid_query",
                "query-rules requires a non-empty query.",
            )
        if system_id is None and self.active_campaign:
            campaign = CampaignStore(self._connection).get_campaign(self.active_campaign)
            if campaign is not None:
                system_id = campaign.get("system_id")
        try:
            chunks = LexicalRetriever(self._connection).search(
                needle,
                RetrievalFilters(
                    namespace=RetrievalNamespace.SYSTEM,
                    content_pack_id=content_pack_id,
                    system_id=system_id,
                    visibility=visibility,
                ),
                limit=10,
            )
        except (GameSystemError, sqlite3.Error, ValueError) as exc:
            return self._error("query-rules", "rules_not_queried", str(exc))
        return self._ok(
            "query-rules",
            {
                "query": needle,
                "results": [
                    {
                        "text": chunk.text,
                        "score": chunk.score,
                        "namespace": chunk.namespace.value,
                        "source": {
                            "chunk_id": chunk.source.chunk_id,
                            "document_id": chunk.source.document_id,
                            "document_title": chunk.source.document_title,
                            "section": chunk.source.section,
                            "page": chunk.source.page,
                            "source_path": chunk.source.source_path,
                        },
                    }
                    for chunk in chunks
                ],
            },
        )

    def query_campaign(self, query: str) -> dict[str, Any]:
        """Substring search over campaign facts and entities for the active campaign."""
        if self._connection is None:
            return self._storage_required("query-campaign")
        campaign_id = self.active_campaign
        if not campaign_id:
            return self._error(
                "query-campaign",
                "campaign_not_configured",
                "query-campaign requires an active campaign.",
            )
        store = CampaignStore(self._connection)
        if store.get_campaign(campaign_id) is None:
            return self._error(
                "query-campaign",
                "campaign_not_found",
                "Active campaign was not found.",
                data={"campaign": campaign_id},
            )
        viewpoint = self._active_viewpoint()
        needle = query.strip().lower()
        facts = store.get_facts(campaign_id, viewpoint=viewpoint)
        if needle:
            facts = [
                fact
                for fact in facts
                if needle in fact.predicate.lower()
                or needle in fact.value.lower()
                or (fact.subject_id is not None and needle in fact.subject_id.lower())
            ]
        entity_rows = self._connection.execute(
            "SELECT entity_id, owner_scope, setting_id, campaign_id, overrides_id, "
            "entity_type, name, system_state, metadata FROM entities "
            "WHERE owner_scope = 'campaign' AND campaign_id = ? "
            "ORDER BY name, entity_id",
            (campaign_id,),
        ).fetchall()
        entities: list[dict[str, Any]] = []
        for row in entity_rows:
            if needle and needle not in str(row["name"]).lower() and needle not in str(
                row["entity_id"]
            ).lower():
                continue
            entity = dict(row)
            entity["system_state"] = json.loads(entity["system_state"])
            entity["metadata"] = json.loads(entity["metadata"])
            entities.append(entity)
        return self._ok(
            "query-campaign",
            {
                "campaign_id": campaign_id,
                "query": query.strip(),
                "facts": [
                    {
                        "fact_id": fact.fact_id,
                        "subject_id": fact.subject_id,
                        "predicate": fact.predicate,
                        "value": fact.value,
                        "visibility": fact.visibility,
                        "canon_state": fact.canon_state.value,
                        "knowledge_state": fact.knowledge_state.value,
                    }
                    for fact in facts
                ],
                "entities": entities,
            },
        )

    def resolve_action(self, action: str) -> dict[str, Any]:
        """Resolve one structured action through the play-turn guard."""
        if self._connection is None:
            return self._storage_required("resolve-action")
        campaign_id = self.active_campaign
        if not campaign_id:
            return self._error(
                "resolve-action",
                "campaign_not_configured",
                "resolve-action requires an active campaign.",
            )
        try:
            payload = json.loads(action) if action.strip() else {}
        except json.JSONDecodeError as exc:
            return self._error("resolve-action", "invalid_action", str(exc))
        if not isinstance(payload, dict):
            return self._error(
                "resolve-action",
                "invalid_action",
                "resolve-action expects a JSON object.",
            )
        campaign = CampaignStore(self._connection).get_campaign(campaign_id)
        if campaign is None:
            return self._error(
                "resolve-action",
                "campaign_not_found",
                "Active campaign was not found.",
                data={"campaign": campaign_id},
            )
        if campaign.get("archived_at"):
            return self._error(
                "resolve-action",
                "campaign_archived",
                "Active campaign is archived.",
                data={"campaign": campaign_id},
            )
        try:
            game_action = parse_game_action(payload)
        except (InvalidActionError, ValueError) as exc:
            return self._error("resolve-action", "invalid_action", str(exc))
        if self._workspace is Workspace.PLAYER:
            actor_id = game_action.actor.id
            controlled = self._player_viewpoint().character_ids
            if actor_id not in controlled:
                return self._error(
                    "resolve-action",
                    "actor_not_controlled",
                    "Player may only resolve actions for controlled characters.",
                    data={"actor": actor_id},
                )
        scene_id = payload.get("scene_id")
        if scene_id is not None and (
            not isinstance(scene_id, str) or not scene_id.strip()
        ):
            return self._error(
                "resolve-action",
                "invalid_action",
                "scene_id must be a non-empty string when provided.",
            )
        try:
            result = play_turn(
                self._registry,
                self._connection,
                game_action,
                campaign_id=campaign_id,
                system_id=campaign["system_id"],
                scene_id=scene_id,
            )
        except (InvalidActionError, InvalidResolutionError, LookupError, ValueError) as exc:
            return self._error("resolve-action", "action_not_resolved", str(exc))
        return self._ok("resolve-action", result.to_dict())

    def submit_action(self, proposal_json: str) -> dict[str, Any]:
        """Take one ``ActionProposal`` from a model to an authoritative outcome.

        This is the canonical path from natural language to mechanics. The
        proposal is planned first, authoritative parameters are acquired, and
        only then is a ``GameAction`` built and handed to the existing
        ``play_turn`` guard. A proposal that does not plan to ``RESOLVE``
        never reaches the plugin at all, so the plugin guard is unchanged
        and remains the only route to a roll or a state change.
        """

        if self._connection is None:
            return self._storage_required("submit-action")
        campaign_id = self.active_campaign
        if not campaign_id:
            return self._error(
                "submit-action",
                "campaign_not_configured",
                "submit-action requires an active campaign.",
            )
        try:
            decoded = json.loads(proposal_json) if proposal_json.strip() else {}
        except json.JSONDecodeError as exc:
            return self._error("submit-action", "invalid_proposal", str(exc))
        try:
            proposal = parse_action_proposal(decoded)
        except (InvalidActionError, ValueError) as exc:
            return self._error("submit-action", "invalid_proposal", str(exc))

        campaign = CampaignStore(self._connection).get_campaign(campaign_id)
        if campaign is None:
            return self._error(
                "submit-action",
                "campaign_not_found",
                "Active campaign was not found.",
                data={"campaign": campaign_id},
            )
        if campaign.get("archived_at"):
            return self._error(
                "submit-action",
                "campaign_archived",
                "Active campaign is archived.",
                data={"campaign": campaign_id},
            )

        system_id = str(campaign["system_id"])
        try:
            plugin = self._registry.get(system_id)
        except GameSystemError as exc:
            return self._error("submit-action", "plugin_unavailable", str(exc))

        plan = plan_resolution(
            proposal,
            plugin=plugin,
            context=self._proposal_plan_context(campaign_id, proposal.actor_id),
            parameters=self._model_proposed_parameters(proposal),
        )

        if plan.disposition in _LOOKUP_DISPOSITIONS:
            lookup = resolve_lookup(
                missing=plan.missing_parameters,
                rules=RuleLookup(self._rule_source(plugin, system_id)),
                rulings=RulingStore(self._connection),
                state=StateLookup(self._state_values(campaign_id, system_id)),
                campaign_id=campaign_id,
                query=proposal.intent,
                model_proposed=plan.parameters,
            )
            if lookup.disposition is not Disposition.RESOLVE:
                return self._ok(
                    "submit-action",
                    {
                        "disposition": lookup.disposition.value,
                        "reason": lookup.reason,
                        "action": None,
                        "resolution": None,
                        "missing_parameters": list(lookup.missing_parameters),
                        "clarification": None,
                    },
                )
            plan = plan_resolution(
                proposal,
                plugin=plugin,
                context=self._proposal_plan_context(campaign_id, proposal.actor_id),
                parameters=tuple(lookup.parameters.values()),
            )

        if not plan.is_resolvable or plan.action is None:
            return self._ok(
                "submit-action",
                {
                    "disposition": plan.disposition.value,
                    "reason": plan.reason,
                    "action": None,
                    "resolution": None,
                    "missing_parameters": list(plan.missing_parameters),
                    "clarification": (
                        None
                        if plan.clarification is None
                        else plan.clarification.to_dict()
                    ),
                },
            )

        try:
            result = play_turn(
                self._registry,
                self._connection,
                plan.action,
                campaign_id=campaign_id,
                system_id=system_id,
            )
        except (
            InvalidActionError,
            InvalidResolutionError,
            LookupError,
            ValueError,
        ) as exc:
            return self._error("submit-action", "action_not_resolved", str(exc))
        payload = result.to_dict()
        payload["disposition"] = Disposition.RESOLVE.value
        payload["reason"] = plan.reason
        return self._ok("submit-action", payload)

    def _proposal_plan_context(
        self, campaign_id: str, actor_id: str
    ) -> PlanContext:
        """Build the planner's view of who is present and who may act."""

        scene = SceneStore(self._connection).get_open_scene(campaign_id)
        present: tuple[str, ...] = ()
        if scene is not None:
            present = SceneStore(self._connection).get_present_entity_ids(
                campaign_id, scene.scene_id
            )
        controlled: tuple[str, ...] = ()
        actor_controls = True
        if self._workspace is Workspace.PLAYER:
            controlled = self._player_viewpoint().character_ids
            actor_controls = actor_id in controlled
        return PlanContext(
            present_entity_ids=present,
            actor_controls_actor=actor_controls,
            player_controlled_entity_ids=controlled,
        )

    def _model_proposed_parameters(
        self, proposal: ActionProposal
    ) -> tuple[MechanicalParameter, ...]:
        """Wrap the model's own numbers as proposals, never as authority."""

        return tuple(
            MechanicalParameter(
                name=name,
                value=value,
                source=ParameterSource.MODEL_PROPOSAL,
            )
            for name, value in proposal.parameters.items()
        )

    def _rule_source(self, plugin: GameSystemPlugin, system_id: str) -> RuleSource:
        return _PluginRuleSource(plugin, system_id)

    def _state_values(
        self, campaign_id: str, system_id: str
    ) -> dict[str, Any]:
        """Read rule-shaped values the active scene and campaign already hold."""

        values: dict[str, Any] = {}
        campaign = CampaignStore(self._connection).get_campaign(campaign_id)
        if campaign is not None:
            values.update(campaign.get("system_state") or {})
        scene = SceneStore(self._connection).get_open_scene(campaign_id)
        if scene is not None:
            values.update(scene.system_state)
        return values

    def roll(self, expression: str) -> dict[str, Any]:
        try:
            result = roll_dice(expression)
        except DiceExpressionError as exc:
            return self._error("roll", exc.code, str(exc))
        return self._ok("roll", result.to_dict())

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        """Return one campaign-owned entity for the active campaign."""
        if self._connection is None:
            return self._storage_required("get-entity")
        campaign_id = self.active_campaign
        if not campaign_id:
            return self._error(
                "get-entity",
                "campaign_not_configured",
                "get-entity requires an active campaign.",
            )
        entity_key = entity_id.strip()
        if not entity_key:
            return self._error(
                "get-entity",
                "invalid_entity_id",
                "get-entity requires a non-empty entity id.",
            )
        entity = CampaignStore(self._connection).get_entity(campaign_id, entity_key)
        if entity is None:
            return self._error(
                "get-entity",
                "entity_not_found",
                "Campaign entity was not found.",
                data={"entity_id": entity_key, "campaign_id": campaign_id},
            )
        return self._ok("get-entity", {"entity": entity})

    def get_chunk(self, chunk_id: str) -> dict[str, Any]:
        """Reload one library chunk visible to the GM viewpoint."""

        if self._connection is None:
            return self._storage_required("get-chunk")
        chunk_key = chunk_id.strip()
        if not chunk_key:
            return self._error(
                "get-chunk",
                "invalid_chunk_id",
                "get-chunk requires a non-empty chunk id.",
            )
        chunk = load_visible_chunk(
            self._connection,
            chunk_key,
            viewpoint=self._active_viewpoint(),
        )
        if chunk is None:
            return self._error(
                "get-chunk",
                "chunk_not_found",
                "Chunk was not found.",
                data={"chunk_id": chunk_key},
            )
        return self._ok("get-chunk", chunk)

    def get_fact(self, fact_id: str) -> dict[str, Any]:
        """Return one fact owned by the active campaign or its setting."""
        if self._connection is None:
            return self._storage_required("get-fact")
        campaign_id = self.active_campaign
        if not campaign_id:
            return self._error(
                "get-fact",
                "campaign_not_configured",
                "get-fact requires an active campaign.",
            )
        fact_key = fact_id.strip()
        if not fact_key:
            return self._error(
                "get-fact",
                "invalid_fact_id",
                "get-fact requires a non-empty fact id.",
            )
        setting_id = self._owned_setting_id()
        row = self._connection.execute(
            "SELECT fact_id, fact_scope, setting_id, campaign_id, subject_id, "
            "predicate, value, canon_state, knowledge_state, visibility, valid_from, "
            "valid_until, source_document_id, source_chunk_id, import_job_id, "
            "extraction_method, source_ownership, created_at FROM facts "
            "WHERE fact_id = ? AND ("
            "(fact_scope = 'campaign' AND campaign_id = ?) OR "
            "(fact_scope = 'setting' AND setting_id = ?)"
            ")",
            (fact_key, campaign_id, setting_id),
        ).fetchone()
        if row is None:
            return self._error(
                "get-fact",
                "fact_not_found",
                "Fact was not found in the active campaign scope.",
                data={"fact_id": fact_key, "campaign_id": campaign_id},
            )
        return self._ok("get-fact", {"fact": dict(row)})

    def get_relationships(self, entity_id: str) -> dict[str, Any]:
        """Return campaign relationship edges visible to the GM viewpoint."""
        if self._connection is None:
            return self._storage_required("get-relationships")
        campaign_id = self.active_campaign
        if not campaign_id:
            return self._error(
                "get-relationships",
                "campaign_not_configured",
                "get-relationships requires an active campaign.",
            )
        entity_key = entity_id.strip()
        if not entity_key:
            return self._error(
                "get-relationships",
                "invalid_entity_id",
                "get-relationships requires a non-empty entity id.",
            )
        as_of = datetime.now(timezone.utc).date().isoformat()
        try:
            edges = resolve_relationship_overlay(
                self._connection,
                campaign_id=campaign_id,
                setting_id=self._owned_setting_id(),
                entity_id=entity_key,
                as_of=as_of,
                viewpoint=self._active_viewpoint(),
            )
        except (LookupError, ValueError) as exc:
            return self._error("get-relationships", "relationships_not_queried", str(exc))
        return self._ok(
            "get-relationships",
            {
                "campaign_id": campaign_id,
                "entity_id": entity_key,
                "relationships": [
                    {
                        "relationship_id": edge.relationship_id,
                        "source_id": edge.source_id,
                        "relationship_type": edge.relationship_type,
                        "target_id": edge.target_id,
                        "metadata": dict(edge.metadata),
                        "visibility": edge.visibility,
                        "valid_from": edge.valid_from,
                        "valid_until": edge.valid_until,
                    }
                    for edge in edges
                ],
            },
        )

    def record_ruling(self, ruling: Ruling | str) -> dict[str, Any]:
        """Persist a complete ruling through the canon lifecycle."""
        parsed: Ruling
        if isinstance(ruling, Ruling):
            parsed = ruling
        elif isinstance(ruling, str):
            try:
                payload = json.loads(ruling) if ruling.strip() else {}
            except json.JSONDecodeError as exc:
                return self._error("record-ruling", "invalid_ruling", str(exc))
            if not isinstance(payload, dict):
                return self._error(
                    "record-ruling",
                    "invalid_ruling",
                    "record-ruling expects a JSON object.",
                )
            try:
                parsed = ruling_from_mapping(payload)
            except (GameSystemError, TypeError, ValueError) as exc:
                return self._error("record-ruling", "invalid_ruling", str(exc))
        else:
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
        if not self.active_campaign:
            return self._error(
                "record-ruling",
                "campaign_not_configured",
                "record-ruling requires an active campaign.",
            )
        if parsed.campaign_id != self.active_campaign:
            return self._error(
                "record-ruling",
                "campaign_mismatch",
                "record-ruling cannot write outside the active campaign.",
                data={
                    "campaign_id": parsed.campaign_id,
                    "active_campaign": self.active_campaign,
                },
            )
        try:
            stored = RulingStore(self._connection).record(parsed)
        except (GameSystemError, LookupError, sqlite3.IntegrityError, ValueError) as exc:
            return self._error("record-ruling", "ruling_not_recorded", str(exc))
        return self._ok("record-ruling", stored.to_dict())

    def promote_ruling(self, ruling_id: str) -> dict[str, Any]:
        """Confirm a ruling in the active campaign without revealing it."""

        if self._connection is None:
            return self._storage_required("promote-ruling")
        if not self.active_campaign:
            return self._error(
                "promote-ruling",
                "campaign_not_configured",
                "promote-ruling requires an active campaign.",
            )
        try:
            payload = json.loads(ruling_id) if ruling_id.strip().startswith("{") else {}
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict) and payload.get("ruling_id"):
            ruling_key = str(payload["ruling_id"]).strip()
        else:
            ruling_key = ruling_id.strip().strip('"')
        if not ruling_key:
            return self._error(
                "promote-ruling",
                "invalid_ruling_id",
                "promote-ruling requires a ruling id.",
            )
        store = RulingStore(self._connection)
        existing = store.get(ruling_key)
        if existing is None or existing.campaign_id != self.active_campaign:
            return self._error(
                "promote-ruling",
                "campaign_mismatch" if existing is not None else "ruling_not_found",
                "promote-ruling cannot write outside the active campaign.",
                data={"ruling_id": ruling_key, "active_campaign": self.active_campaign},
            )
        try:
            promoted = store.promote(ruling_key)
        except (LookupError, ValueError) as exc:
            return self._error("promote-ruling", "ruling_not_promoted", str(exc))
        return self._ok("promote-ruling", promoted.to_dict())

    def get_ruling(self, ruling_id: str) -> dict[str, Any]:
        """Return one ruling owned by the active campaign."""

        if self._connection is None:
            return self._storage_required("get-ruling")
        if not self.active_campaign:
            return self._error(
                "get-ruling",
                "campaign_not_configured",
                "get-ruling requires an active campaign.",
            )
        ruling_key = ruling_id.strip().strip('"')
        ruling = RulingStore(self._connection).get(ruling_key)
        if ruling is None or ruling.campaign_id != self.active_campaign:
            return self._error(
                "get-ruling",
                "ruling_not_found",
                "Ruling was not found in the active campaign.",
                data={"ruling_id": ruling_key},
            )
        return self._ok("get-ruling", ruling.to_dict())

    def promote_fact(self, fact_id: str) -> dict[str, Any]:
        """Confirm a campaign fact. Payload fields cannot choose the axis."""

        return self._change_campaign_fact(fact_id, operation="promote-fact")

    def reveal_fact_skill(self, fact_id: str) -> dict[str, Any]:
        """Reveal a campaign fact. Payload fields cannot choose the axis."""

        return self._change_campaign_fact(fact_id, operation="reveal-fact")

    def _change_campaign_fact(self, fact_id: str, *, operation: str) -> dict[str, Any]:
        if self._connection is None:
            return self._storage_required(operation)
        if not self.active_campaign:
            return self._error(
                operation,
                "campaign_not_configured",
                f"{operation} requires an active campaign.",
            )
        fact_key = _identifier_arg(fact_id)
        if not fact_key:
            return self._error(operation, "invalid_fact_id", f"{operation} requires a fact id.")
        row = self._connection.execute(
            "SELECT fact_id, fact_scope, setting_id, campaign_id, subject_id, "
            "predicate, value, canon_state, knowledge_state, visibility, valid_from, "
            "valid_until, source_document_id, source_chunk_id, import_job_id, "
            "extraction_method, source_ownership, created_at FROM facts "
            "WHERE fact_id = ?",
            (fact_key,),
        ).fetchone()
        if row is None or row["campaign_id"] != self.active_campaign:
            return self._error(
                operation,
                "campaign_mismatch",
                f"{operation} cannot write outside the active campaign.",
                data={"fact_id": fact_key, "active_campaign": self.active_campaign},
            )
        fact = _fact_from_runtime_row(row)
        try:
            if operation == "promote-fact":
                changed = promote_campaign_fact(self._connection, fact)
            else:
                changed = reveal_campaign_fact(self._connection, fact)
        except (LookupError, ValueError) as exc:
            return self._error(operation, "fact_not_changed", str(exc))
        return self._ok(
            operation,
            {
                "fact_id": changed.fact_id,
                "canon_state": changed.canon_state.value,
                "knowledge_state": changed.knowledge_state.value,
            },
        )

    def promote_world_fact(self, fact_id: str) -> dict[str, Any]:
        return self._change_world_fact(fact_id, operation="promote-world-fact")

    def reveal_world_fact(self, fact_id: str) -> dict[str, Any]:
        return self._change_world_fact(fact_id, operation="reveal-world-fact")

    def _change_world_fact(self, fact_id: str, *, operation: str) -> dict[str, Any]:
        if self._connection is None:
            return self._storage_required(operation)
        fact_key = _identifier_arg(fact_id)
        if not fact_key:
            return self._error(operation, "invalid_fact_id", f"{operation} requires a fact id.")
        owned = self._owned_setting_id()
        if owned is None:
            return self._error(
                operation,
                "setting_not_configured",
                f"{operation} requires an owned setting.",
            )
        row = self._connection.execute(
            "SELECT fact_id, setting_id, canon_state, knowledge_state FROM facts "
            "WHERE fact_id = ? AND fact_scope = 'setting'",
            (fact_key,),
        ).fetchone()
        if row is None or row["setting_id"] != owned:
            return self._error(
                operation,
                "setting_mismatch",
                f"{operation} cannot write outside the owned setting.",
                data={"fact_id": fact_key, "owned_setting_id": owned},
            )
        if operation == "reveal-world-fact" and row["canon_state"] != "confirmed":
            return self._error(
                operation,
                "fact_not_changed",
                "reveal-world-fact requires a confirmed fact.",
            )
        column = "canon_state" if operation == "promote-world-fact" else "knowledge_state"
        value = "confirmed" if column == "canon_state" else "known"
        event_type = (
            SettingEventType.WORLD_FACT_PROMOTED
            if column == "canon_state"
            else SettingEventType.WORLD_FACT_REVEALED
        )
        try:
            with transaction(self._connection):
                cursor = self._connection.execute(
                    f"UPDATE facts SET {column} = ? WHERE fact_id = ?",
                    (value, fact_key),
                )
                if cursor.rowcount != 1:
                    raise LookupError(fact_key)
                SettingEventStore(self._connection).append_in_transaction(
                    self._connection,
                    owned,
                    event_type,
                    {"fact_id": fact_key},
                )
        except (LookupError, sqlite3.Error) as exc:
            return self._error(operation, "fact_not_changed", str(exc))
        updated = self._connection.execute(
            "SELECT canon_state, knowledge_state FROM facts WHERE fact_id = ?",
            (fact_key,),
        ).fetchone()
        return self._ok(
            operation,
            {
                "fact_id": fact_key,
                "canon_state": updated["canon_state"],
                "knowledge_state": updated["knowledge_state"],
            },
        )

    def start_session(self, session: str) -> dict[str, Any]:
        """Open one session for the active campaign and append ``session.started``."""

        if self._connection is None:
            return self._storage_required("start-session")
        campaign_id = self.active_campaign
        if not campaign_id:
            return self._error(
                "start-session",
                "campaign_not_configured",
                "start-session requires an active campaign.",
            )
        try:
            payload = json.loads(session) if session.strip() else {}
        except json.JSONDecodeError as exc:
            return self._error("start-session", "invalid_session", str(exc))
        if not isinstance(payload, dict):
            return self._error(
                "start-session",
                "invalid_session",
                "start-session expects a JSON object.",
            )
        session_id = str(payload.get("session_id", "")).strip()
        if not session_id:
            return self._error(
                "start-session",
                "invalid_session",
                "start-session requires session_id.",
            )
        if CampaignStore(self._connection).get_campaign(campaign_id) is None:
            return self._error(
                "start-session",
                "campaign_not_found",
                "Active campaign was not found.",
                data={"campaign": campaign_id},
            )
        campaign = CampaignStore(self._connection).get_campaign(campaign_id)
        assert campaign is not None
        if campaign.get("archived_at"):
            return self._error(
                "start-session",
                "campaign_archived",
                "Active campaign is archived.",
                data={"campaign": campaign_id},
            )
        started_at = str(
            payload.get("started_at") or datetime.now(timezone.utc).isoformat()
        )
        try:
            with transaction(self._connection):
                self._connection.execute(
                    "INSERT INTO sessions "
                    "(session_id, campaign_id, started_at, participants, "
                    "important_facts, open_threads, checklist_step) "
                    "VALUES (?, ?, ?, '[]', '[]', '[]', 0)",
                    (session_id, campaign_id, started_at),
                )
                EventStore(self._connection).append_in_transaction(
                    self._connection,
                    campaign_id,
                    GameEvent(
                        event_type=EventType.SESSION_STARTED.value,
                        payload={
                            "session_id": session_id,
                            "started_at": started_at,
                        },
                    ),
                    session_id=session_id,
                    occurred_at=started_at,
                )
        except sqlite3.IntegrityError as exc:
            return self._error(
                "start-session",
                "session_not_started",
                str(exc),
                data={"campaign_id": campaign_id, "session_id": session_id},
            )
        return self._ok(
            "start-session",
            {
                "session_id": session_id,
                "campaign_id": campaign_id,
                "started_at": started_at,
            },
        )

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
        if not self.campaign_roots:
            return self._error(
                "end-session",
                "campaign_roots_not_configured",
                "end-session requires at least one configured campaign root.",
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
        try:
            with transaction(self._connection):
                existing = self._connection.execute(
                    "SELECT setting_id FROM settings WHERE setting_id = ?",
                    (setting_id,),
                ).fetchone()
                if existing is None:
                    self._connection.execute(
                        "INSERT INTO settings (setting_id, name, created_at) "
                        "VALUES (?, ?, ?)",
                        (setting_id, name, created_at),
                    )
                else:
                    self._connection.execute(
                        "UPDATE settings SET name = ? WHERE setting_id = ?",
                        (name, setting_id),
                    )
                SettingEventStore(self._connection).append_in_transaction(
                    self._connection,
                    setting_id,
                    SettingEventType.SETTING_EDITED,
                    {
                        "setting_id": setting_id,
                        "name": name,
                        "created_at": created_at,
                    },
                    occurred_at=created_at,
                )
        except sqlite3.IntegrityError as exc:
            return self._error("edit-setting", "setting_not_saved", str(exc))
        row = self._connection.execute(
            "SELECT setting_id, name, created_at FROM settings WHERE setting_id = ?",
            (setting_id,),
        ).fetchone()
        return self._ok("edit-setting", {"setting": dict(row)})

    def get_world_entity(self, entity_id: str) -> dict[str, Any]:
        """Return one setting-owned entity for the workspace-owned setting."""
        if self._connection is None:
            return self._storage_required("get-world-entity")
        entity_key = entity_id.strip()
        if not entity_key:
            return self._error(
                "get-world-entity",
                "invalid_entity_id",
                "get-world-entity requires a non-empty entity id.",
            )
        setting_id = self._owned_setting_id()
        if setting_id is None:
            return self._error(
                "get-world-entity",
                "setting_not_configured",
                "get-world-entity requires an active campaign with a setting "
                "or a single owned setting.",
            )
        row = self._connection.execute(
            "SELECT entity_id, owner_scope, setting_id, campaign_id, overrides_id, "
            "entity_type, name, system_state, metadata FROM entities "
            "WHERE owner_scope = 'setting' AND setting_id = ? AND entity_id = ?",
            (setting_id, entity_key),
        ).fetchone()
        if row is None:
            return self._error(
                "get-world-entity",
                "entity_not_found",
                "Setting-owned entity was not found.",
                data={"entity_id": entity_key, "setting_id": setting_id},
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
        owned_setting = self._owned_setting_id()
        if owned_setting is None:
            return self._error(
                "upsert-world-entity",
                "setting_not_configured",
                "upsert-world-entity requires an active campaign with a setting "
                "or a single owned setting.",
            )
        if owned_setting != setting_id:
            return self._error(
                "upsert-world-entity",
                "setting_mismatch",
                "upsert-world-entity cannot write outside the owned setting.",
                data={"setting_id": setting_id, "owned_setting_id": owned_setting},
            )
        entity_type = payload.get("entity_type")
        system_state = json.dumps(payload.get("system_state") or {}, separators=(",", ":"))
        metadata = json.dumps(payload.get("metadata") or {}, separators=(",", ":"))
        overrides_id = payload.get("overrides_id")
        try:
            with transaction(self._connection):
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
                SettingEventStore(self._connection).append_in_transaction(
                    self._connection,
                    setting_id,
                    SettingEventType.WORLD_ENTITY_UPSERTED,
                    {
                        "entity_id": entity_id,
                        "setting_id": setting_id,
                        "name": name,
                        "entity_type": entity_type,
                        "system_state": json.loads(system_state),
                        "metadata": json.loads(metadata),
                        "overrides_id": overrides_id,
                    },
                )
        except sqlite3.IntegrityError as exc:
            return self._error("upsert-world-entity", "entity_not_saved", str(exc))
        row = self._connection.execute(
            "SELECT entity_id, owner_scope, setting_id, campaign_id, overrides_id, "
            "entity_type, name, system_state, metadata FROM entities "
            "WHERE owner_scope = 'setting' AND setting_id = ? AND entity_id = ?",
            (setting_id, entity_id),
        ).fetchone()
        result = dict(row)
        result["system_state"] = json.loads(result["system_state"])
        result["metadata"] = json.loads(result["metadata"])
        return self._ok("upsert-world-entity", {"entity": result})

    def query_world_history(self, query: str) -> dict[str, Any]:
        """Query setting-scoped facts as world history (tasks 10 and 12)."""
        if self._connection is None:
            return self._storage_required("query-world-history")
        needle = query.strip()
        setting_id = self._owned_setting_id()
        if setting_id is None:
            return self._error(
                "query-world-history",
                "setting_not_configured",
                "query-world-history requires an owned setting.",
            )
        if not needle:
            rows = self._connection.execute(
                "SELECT fact_id, setting_id, subject_id, predicate, value, "
                "canon_state, knowledge_state, created_at FROM facts "
                "WHERE fact_scope = 'setting' AND setting_id = ? "
                "ORDER BY created_at, fact_id LIMIT 50",
                (setting_id,),
            ).fetchall()
        else:
            like = f"%{needle}%"
            rows = self._connection.execute(
                "SELECT fact_id, setting_id, subject_id, predicate, value, "
                "canon_state, knowledge_state, created_at FROM facts "
                "WHERE fact_scope = 'setting' AND setting_id = ? AND "
                "(subject_id LIKE ? OR predicate LIKE ? OR value LIKE ?) "
                "ORDER BY created_at, fact_id LIMIT 50",
                (setting_id, like, like, like),
            ).fetchall()
        return self._ok(
            "query-world-history",
            {"facts": [dict(row) for row in rows], "query": needle},
        )

    def record_world_history(self, entry: str) -> dict[str, Any]:
        """Append a setting-scoped proposed fact used as world history."""
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
        setting_id = str(payload["setting_id"]).strip()
        owned_setting = self._owned_setting_id()
        if owned_setting is None:
            return self._error(
                "record-world-history",
                "setting_not_configured",
                "record-world-history requires an active campaign with a setting "
                "or a single owned setting.",
            )
        if owned_setting != setting_id:
            return self._error(
                "record-world-history",
                "setting_mismatch",
                "record-world-history cannot write outside the owned setting.",
                data={"setting_id": setting_id, "owned_setting_id": owned_setting},
            )
        created_at = str(
            payload.get("created_at") or datetime.now(timezone.utc).isoformat()
        )
        fact = Fact(
            fact_id=str(payload["fact_id"]).strip(),
            fact_scope=FactScope.SETTING,
            setting_id=setting_id,
            campaign_id=None,
            subject_id=str(payload["subject_id"]).strip(),
            predicate=str(payload["predicate"]).strip(),
            value=str(payload["value"]).strip(),
            canon_state=CanonState.PROPOSED,
            knowledge_state=KnowledgeState.UNREVEALED,
            visibility=str(payload.get("visibility") or "GM").strip() or "GM",
            created_at=created_at,
        )
        store = CampaignStore(self._connection)
        try:
            with transaction(self._connection):
                store.add_fact_in_transaction(fact)
                SettingEventStore(self._connection).append_in_transaction(
                    self._connection,
                    setting_id,
                    SettingEventType.WORLD_FACT_RECORDED,
                    {
                        "fact_id": fact.fact_id,
                        "setting_id": setting_id,
                        "subject_id": fact.subject_id,
                        "predicate": fact.predicate,
                        "value": fact.value,
                        "canon_state": fact.canon_state.value,
                        "knowledge_state": fact.knowledge_state.value,
                        "visibility": fact.visibility,
                        "created_at": fact.created_at,
                    },
                    occurred_at=fact.created_at,
                )
        except (FactInvariantError, StorageError, sqlite3.IntegrityError, ValueError) as exc:
            return self._error("record-world-history", "history_not_recorded", str(exc))
        return self._ok(
            "record-world-history",
            {
                "fact_id": fact.fact_id,
                "setting_id": setting_id,
                "canon_state": fact.canon_state.value,
                "knowledge_state": fact.knowledge_state.value,
                "visibility": fact.visibility,
            },
        )

    # -- Campaign-only surface helpers ----------------------------------------

    def read_session(self, session_id: str) -> dict[str, Any]:
        """Read one session row from the active campaign store."""
        if self._connection is None:
            return self._storage_required("read-session")
        campaign_id = self.active_campaign
        if not campaign_id:
            return self._error(
                "read-session",
                "campaign_not_configured",
                "read-session requires an active campaign.",
            )
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
            "FROM sessions WHERE session_id = ? AND campaign_id = ?",
            (key, campaign_id),
        ).fetchone()
        if row is None:
            return self._error(
                "read-session",
                "session_not_found",
                "Session was not found.",
                data={"session_id": key, "campaign_id": campaign_id},
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
        store = CampaignStore(self._connection)
        if store.get_campaign(campaign_id) is None:
            return self._error(
                "mutate-quest",
                "campaign_not_found",
                "Active campaign was not found.",
                data={"campaign": campaign_id},
            )
        try:
            with transaction(self._connection):
                store.apply_state_changes_in_transaction(
                    campaign_id,
                    (
                        StateChange(
                            operation=StateOperation.SET,
                            path=("campaign", "system", "quests", quest_id),
                            value=payload,
                        ),
                    ),
                )
                EventStore(self._connection).append_in_transaction(
                    self._connection,
                    campaign_id,
                    GameEvent(
                        event_type=EventType.QUEST_MUTATED.value,
                        payload={"quest_id": quest_id, "quest": payload},
                    ),
                )
        except (GameSystemError, sqlite3.Error) as exc:
            return self._error("mutate-quest", "quest_not_mutated", str(exc))
        return self._ok("mutate-quest", {"campaign_id": campaign_id, "quest": payload})

    def read_campaign_secret(self, secret_id: str) -> dict[str, Any]:
        """Read one GM-visibility fact treated as a campaign secret."""
        if self._connection is None:
            return self._storage_required("read-campaign-secret")
        campaign_id = self.active_campaign
        if not campaign_id:
            return self._error(
                "read-campaign-secret",
                "campaign_not_configured",
                "read-campaign-secret requires an active campaign.",
            )
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
            "WHERE fact_id = ? AND fact_scope = 'campaign' AND campaign_id = ? "
            "AND visibility = 'GM'",
            (key, campaign_id),
        ).fetchone()
        if row is None:
            return self._error(
                "read-campaign-secret",
                "secret_not_found",
                "Campaign secret was not found.",
                data={"secret_id": key, "campaign_id": campaign_id},
            )
        return self._ok("read-campaign-secret", {"secret": dict(row)})

    def _owned_setting_id(self) -> str | None:
        """Return the setting owned by the active campaign, or the sole setting."""

        if self._connection is None:
            return None
        if self.active_campaign:
            campaign = CampaignStore(self._connection).get_campaign(self.active_campaign)
            if campaign is not None:
                setting_id = campaign.get("setting_id")
                if isinstance(setting_id, str) and setting_id.strip():
                    return setting_id
        rows = self._connection.execute(
            "SELECT setting_id FROM settings ORDER BY created_at, setting_id"
        ).fetchall()
        if len(rows) == 1:
            return str(rows[0]["setting_id"])
        return None

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


#: Dispositions whose missing parameters are worth one lookup attempt before
#: the turn is escalated to the GM.
_LOOKUP_DISPOSITIONS = frozenset(
    {Disposition.RULE_LOOKUP, Disposition.STATE_LOOKUP}
)


class _PluginRuleSource:
    """Read rules-authoritative defaults declared by the active plugin.

    A plugin may state a value its system always uses. That is the rules
    speaking, not the model, so it can satisfy a declared requirement.
    """

    def __init__(self, plugin: GameSystemPlugin, system_id: str) -> None:
        self._plugin = plugin
        self._system_id = system_id

    def lookup(
        self, names: tuple[str, ...], campaign_id: str
    ) -> dict[str, Any]:
        defaults = self._plugin.default_parameters()
        return {name: defaults[name] for name in names if name in defaults}



def _scene_payload(payload_json: str) -> dict[str, Any]:
    """Parse a scene operation payload into a plain mapping."""

    text = payload_json.strip()
    if not text:
        return {}
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"payload is not valid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise ValueError("payload must be a JSON object")
    return decoded


def _required_string(
    payload: Mapping[str, Any], key: str, operation: str
) -> str | None:
    """Return a non-empty string field, or None when it is absent or wrong."""

    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _scene_to_dict(scene: Scene) -> dict[str, Any]:
    return {
        "scene_id": scene.scene_id,
        "name": scene.name,
        "status": scene.status.value,
        "session_id": scene.session_id,
        "location_entity_id": scene.location_entity_id,
        "started_at": scene.started_at,
        "ended_at": scene.ended_at,
        "in_world_started_at": scene.in_world_started_at,
        "in_world_ended_at": scene.in_world_ended_at,
    }


def _member_to_dict(member: SceneMember) -> dict[str, Any]:
    return {
        "scene_id": member.scene_id,
        "entity_id": member.entity_id,
        "presence_type": member.presence_type.value,
        "entered_at": member.entered_at,
        "exited_at": member.exited_at,
    }


def _game_time_to_dict(clock: GameTime | None) -> dict[str, Any] | None:
    if clock is None:
        return None
    return {
        "in_world_label": clock.in_world_label,
        "in_world_minutes": clock.in_world_minutes,
        "updated_at": clock.updated_at,
    }

def _identifier_arg(raw: str) -> str:
    """Read an id from a bare string or a JSON object, ignoring lifecycle fields."""

    text = raw.strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text.strip('"')
        if isinstance(payload, dict):
            value = payload.get("fact_id") or payload.get("ruling_id") or ""
            return str(value).strip()
    return text.strip('"')


def _fact_from_runtime_row(row: sqlite3.Row) -> Fact:
    return Fact(
        fact_id=row["fact_id"],
        fact_scope=FactScope(row["fact_scope"]),
        setting_id=row["setting_id"],
        campaign_id=row["campaign_id"],
        subject_id=row["subject_id"],
        predicate=row["predicate"],
        value=row["value"],
        canon_state=CanonState(row["canon_state"]),
        knowledge_state=KnowledgeState(row["knowledge_state"]),
        visibility=row["visibility"],
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        source_document_id=row["source_document_id"],
        source_chunk_id=row["source_chunk_id"],
        import_job_id=row["import_job_id"],
        extraction_method=row["extraction_method"],
        source_ownership=row["source_ownership"],
        created_at=row["created_at"],
    )
