"""Command handlers for the operator CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

import yaml

from tabletop.api.errors import ContentPackError, PluginNotFoundError
from tabletop.api.events import GameEvent
from tabletop.api.plugin import GameSystemPlugin, is_compatible_api_version
from tabletop.api.workspace import Workspace
from tabletop.campaign import sender_binding
from tabletop.campaign.event_store import EventStore, EventType
from tabletop.campaign.membership import MembershipStore, validate_participant_id
from tabletop.campaign.readiness import readiness_report
from tabletop.campaign.resume import resume_snapshot
from tabletop.campaign.selection import (
    clear_active_campaign_file,
    write_active_campaign_file,
)
from tabletop.campaign.store import CampaignStore
from tabletop.cli.player_service_spec import player_compose_definition
from tabletop.cli.runtime_factory import open_operator_runtime, resolve_campaign_id
from tabletop.cli.util import (
    load_plugin_registry,
    migrations_dir,
    open_database,
    repo_root,
    require_database_path,
    validate_campaign_id,
)
from tabletop.documents.content_pack import load_content_pack
from tabletop.documents.ingest import IngestContext
from tabletop.documents.markdown import MarkdownIngestor
from tabletop.export.manifest import PackageError
from tabletop.export.package import export_campaign, fork_package, restore_package
from tabletop.importing.apply import ApplyBlockedError, apply_item, review_item
from tabletop.importing.json_adapter import JsonCampaignImporter
from tabletop.importing.notes_adapter import stage_notes
from tabletop.importing.store import (
    ImportStore,
    import_status_report,
)
from tabletop.storage.sqlite import transaction


_REPO_ROOT = repo_root()


def _runtime_env_file() -> Path:
    runtime_file = _REPO_ROOT / ".env"
    return runtime_file if runtime_file.is_file() else _REPO_ROOT / ".env.example"


RUNTIME_ENV_FILE = _runtime_env_file()
COMPOSE_FILE = _REPO_ROOT / "docker-compose.yml"
DEFAULT_RUNTIME_COMPOSE_DIR = Path(tempfile.gettempdir()) / "gamemaster-compose"


@dataclass(frozen=True)
class ChannelSpec:
    credential_slots: tuple[str, ...]
    required_slots: tuple[str, ...]
    source_prefixes: tuple[str, ...]
    authenticated_sender_source: str = "OMEGA_EXPECTED_SENDER"
    participant_unique: bool = True
    uses_ws_token: bool = False


CHANNELS: dict[str, ChannelSpec] = {
    "telegram": ChannelSpec(
        credential_slots=("TELEGRAM_TOKEN", "TG_BOT_TOKEN"),
        required_slots=("TELEGRAM_TOKEN",),
        source_prefixes=("TELEGRAM_TOKEN", "TG_BOT_TOKEN"),
    ),
    "slack": ChannelSpec(
        credential_slots=("SLACK_TOKEN", "SL_BOT_TOKEN"),
        required_slots=("SLACK_TOKEN",),
        source_prefixes=("SLACK_TOKEN", "SL_BOT_TOKEN"),
    ),
    "mattermost": ChannelSpec(
        credential_slots=("MATTERMOST_TOKEN", "MM_BOT_TOKEN"),
        required_slots=("MATTERMOST_TOKEN",),
        source_prefixes=("MATTERMOST_TOKEN", "MM_BOT_TOKEN"),
    ),
    "irc": ChannelSpec(
        credential_slots=("IRC_TOKEN",),
        required_slots=("OMEGA_AUTH_SECRET",),
        source_prefixes=("IRC_TOKEN",),
    ),
    "websocket": ChannelSpec(
        credential_slots=("WS_TOKEN",),
        required_slots=("WS_TOKEN",),
        source_prefixes=("WS_TOKEN",),
        uses_ws_token=True,
    ),
}

_PARTICIPANT_CREDENTIAL_PREFIXES = tuple(
    sorted(
        {
            "ASI_API_KEY",
            "IRC_TOKEN",
            "MATTERMOST_TOKEN",
            "MM_BOT_TOKEN",
            "OMEGA_AUTH_SECRET",
            "SLACK_TOKEN",
            "SL_BOT_TOKEN",
            "TELEGRAM_TOKEN",
            "TG_BOT_TOKEN",
            "WS_TOKEN",
        }
    )
)
_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INTERPOLATION_PATTERN = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-+?]))?"
)


@dataclass(frozen=True)
class LaunchContext:
    action: Literal["start", "stop"]
    service_name: str
    env: dict[str, str]
    host_database_path: Path | None
    container_database_path: str
    runtime_compose_dir: Path
    participant_id: str | None
    workspace: Workspace | None


def _strip_env_comment(value: str) -> str:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote == '"':
            escaped = True
            continue
        if character in {"'", '"'}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
            continue
        if character == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.rstrip()


def _unescape_env_value(value: str, *, double_quoted: bool) -> str:
    if not double_quoted:
        return value
    escaped: list[str] = []
    replacements = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"', "$": "$"}
    index = 0
    while index < len(value):
        if value[index] != "\\" or index + 1 >= len(value):
            escaped.append(value[index])
            index += 1
            continue
        next_character = value[index + 1]
        escaped.append(replacements.get(next_character, next_character))
        index += 2
    return "".join(escaped)


def _expand_env_value(value: str, env: Mapping[str, str]) -> str:
    def find_closing_brace(start: int) -> int | None:
        depth = 1
        index = start
        while index < len(value):
            if value.startswith("${", index):
                depth += 1
                index += 2
                continue
            if value[index] == "}":
                depth -= 1
                if depth == 0:
                    return index
            index += 1
        return None

    expanded: list[str] = []
    index = 0
    while index < len(value):
        start = value.find("${", index)
        if start < 0:
            expanded.append(value[index:])
            break
        expanded.append(value[index:start])
        match = _INTERPOLATION_PATTERN.match(value, start)
        if match is None:
            expanded.append("${")
            index = start + 2
            continue
        name, operator = match.groups()
        if operator is None and value[match.end() : match.end() + 1] != "}":
            expanded.append("${")
            index = start + 2
            continue
        closing = find_closing_brace(match.end())
        if closing is None:
            expanded.append(value[start:])
            break

        argument = value[match.end() : closing]
        current = env.get(name)
        present = current is not None
        nonempty = present and bool(str(current).strip())
        if operator in {":-", "-"}:
            if (operator == ":-" and not nonempty) or (operator == "-" and not present):
                expanded.append(_expand_env_value(argument, env))
            else:
                expanded.append(str(current or ""))
        elif operator in {":?", "?"}:
            missing = not nonempty if operator == ":?" else not present
            if missing:
                raise ValueError(_expand_env_value(argument, env) or f"{name} is required")
            expanded.append(str(current or ""))
        elif operator == ":+":
            expanded.append(_expand_env_value(argument, env) if nonempty else "")
        elif operator == "+":
            expanded.append(_expand_env_value(argument, env) if present else "")
        else:
            expanded.append(str(current or ""))
        index = closing + 1
    return "".join(expanded)


def parse_runtime_env_file(
    path: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Parse the Compose-compatible subset used by the runtime env file."""

    inherited = dict(os.environ if environ is None else environ)
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return values
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, raw_value = line.partition("=")
        name = name.strip()
        if not _ENV_NAME_PATTERN.fullmatch(name):
            continue
        if not separator:
            raw_value = ""
        raw_value = raw_value.strip()
        quote = raw_value[:1]
        if quote in {"'", '"'}:
            closing = 1
            escaped = False
            while closing < len(raw_value):
                character = raw_value[closing]
                if escaped:
                    escaped = False
                elif character == "\\" and quote == '"':
                    escaped = True
                elif character == quote:
                    break
                closing += 1
            if closing >= len(raw_value):
                raise ValueError(f"unterminated quoted value for {name}")
            trailing = raw_value[closing + 1 :].strip()
            if trailing and not trailing.startswith("#"):
                raise ValueError(f"unexpected text after quoted value for {name}")
            value = _unescape_env_value(
                raw_value[1:closing], double_quoted=quote == '"'
            )
        else:
            value = _strip_env_comment(raw_value).strip()
        values[name] = _expand_env_value(value, {**inherited, **values})
    return values


def validate_channel(channel: str) -> str:
    if channel not in CHANNELS:
        raise ValueError(f"unsupported channel {channel!r}; choose one of {sorted(CHANNELS)}")
    return channel


def credential_suffix(participant_id: str) -> str:
    return participant_id.replace("-", "_").upper()


def all_credential_slot_names() -> frozenset[str]:
    return frozenset(
        {
            "ASI_API_KEY",
            "OMEGA_AUTH_SECRET",
            "IRC_TOKEN",
            "WS_TOKEN",
            *CHANNELS["telegram"].credential_slots,
            *CHANNELS["slack"].credential_slots,
            *CHANNELS["mattermost"].credential_slots,
        }
    )


def credential_slot_names(channel: str, role: str) -> tuple[str, ...]:
    if role not in {"gm", "player"}:
        raise ValueError(f"unsupported participant role {role!r}")
    selected = set(CHANNELS[validate_channel(channel)].credential_slots)
    selected.update({"ASI_API_KEY", "OMEGA_AUTH_SECRET"})
    return tuple(sorted(selected))


def all_participant_credential_source_names(config: Mapping[str, str]) -> frozenset[str]:
    return frozenset(
        name
        for name in config
        if any(name.startswith(f"{prefix}_") for prefix in _PARTICIPANT_CREDENTIAL_PREFIXES)
    )


def sanitize_compose_process_env(base: Mapping[str, str]) -> dict[str, str]:
    sanitized = dict(base)
    sanitized.pop("TABLETOP_LOCAL_OPERATOR", None)
    return sanitized


def _source_value(
    base: Mapping[str, str],
    prefixes: Sequence[str],
    participant_id: str,
    role: str,
    *,
    allow_global: bool = True,
) -> str | None:
    suffix = credential_suffix(participant_id if role == "player" else "GM")
    candidates = [f"{prefix}_{suffix}" for prefix in prefixes]
    if role == "gm":
        candidates.extend(f"{prefix}_GM" for prefix in prefixes)
    if allow_global:
        candidates.extend(prefixes)
    for candidate in candidates:
        value = str(base.get(candidate, "")).strip()
        if value:
            return value
    return None


def resolve_channel_credentials(
    base: Mapping[str, str],
    participant_id: str,
    role: str,
    channel: str,
    expected_sender: str,
) -> dict[str, str]:
    """Resolve participant-scoped source values into generic service slots."""

    channel = validate_channel(channel)
    if role not in {"gm", "player"}:
        raise ValueError(f"unsupported participant role {role!r}")
    if not expected_sender.strip():
        raise ValueError("expected sender is required for start")
    credentials: dict[str, str] = {}
    selected_source_prefixes = set(CHANNELS[channel].source_prefixes)
    spec = CHANNELS[channel]
    source_groups: list[tuple[str, tuple[str, ...]]] = [
        ("OMEGA_AUTH_SECRET", ("OMEGA_AUTH_SECRET",)),
        ("ASI_API_KEY", ("ASI_API_KEY",)),
        ("TELEGRAM_TOKEN", ("TELEGRAM_TOKEN", "TG_BOT_TOKEN")),
        ("TG_BOT_TOKEN", ("TG_BOT_TOKEN", "TELEGRAM_TOKEN")),
        ("SLACK_TOKEN", ("SLACK_TOKEN", "SL_BOT_TOKEN")),
        ("SL_BOT_TOKEN", ("SL_BOT_TOKEN", "SLACK_TOKEN")),
        ("MATTERMOST_TOKEN", ("MATTERMOST_TOKEN", "MM_BOT_TOKEN")),
        ("MM_BOT_TOKEN", ("MM_BOT_TOKEN", "MATTERMOST_TOKEN")),
        ("IRC_TOKEN", ("IRC_TOKEN",)),
        ("WS_TOKEN", ("WS_TOKEN",)),
    ]
    for prefix, slots in source_groups:
        if prefix not in {"OMEGA_AUTH_SECRET", "ASI_API_KEY"} and prefix not in selected_source_prefixes:
            continue
        value = _source_value(
            base,
            (prefix,),
            participant_id,
            role,
            allow_global=(
                prefix in {"OMEGA_AUTH_SECRET", "ASI_API_KEY"}
                or (role == "gm" and not spec.participant_unique)
            ),
        )
        if value:
            for slot in slots:
                credentials[slot] = value
    for required in CHANNELS[channel].required_slots:
        if not str(credentials.get(required, "")).strip():
            raise ValueError(f"missing required {channel} credential {required}")
    return credentials


def build_launch_env(
    *,
    campaign_id: str,
    participant_id: str | None,
    role: str | None,
    channel: str | None,
    expected_sender: str | None,
    action: Literal["start", "stop"],
    container_database_path: str,
) -> dict[str, str]:
    """Build the process environment passed to Compose, without database access."""

    if action == "start":
        assert participant_id is not None
        assert role is not None
        assert channel is not None
        assert expected_sender is not None
        channel = validate_channel(channel)
        base = parse_runtime_env_file(RUNTIME_ENV_FILE)
        base.update(os.environ)
        credentials = resolve_channel_credentials(
            base, participant_id, role, channel, expected_sender
        )
        launch_env = sanitize_compose_process_env(base)
        for name in all_participant_credential_source_names(base):
            launch_env.pop(name, None)
        for name in all_credential_slot_names():
            launch_env.pop(name, None)
        launch_env.update(credentials)
        launch_env["TABLETOP_PARTICIPANT"] = participant_id
        launch_env[CHANNELS[channel].authenticated_sender_source] = expected_sender
        launch_env["OMEGA_COMMCHANNEL"] = channel
    elif action == "stop":
        launch_env = {
            "PATH": os.environ.get("PATH", ""),
            "TABLETOP_CAMPAIGN": campaign_id,
            "TABLETOP_DATABASE_PATH": container_database_path,
        }
        if participant_id is not None:
            launch_env["TABLETOP_PARTICIPANT"] = participant_id
    else:
        raise ValueError(f"unsupported Compose action: {action}")
    launch_env["TABLETOP_CAMPAIGN"] = campaign_id
    launch_env["TABLETOP_DATABASE_PATH"] = container_database_path
    launch_env.pop("TABLETOP_LOCAL_OPERATOR", None)
    if action == "stop":
        launch_env.pop("OMEGA_EXPECTED_SENDER", None)
        launch_env.pop("OMEGA_COMMCHANNEL", None)
        for name in all_credential_slot_names():
            launch_env.pop(name, None)
    return launch_env


def _runtime_configuration(environ: Mapping[str, str] | None = None) -> tuple[str, Path]:
    env = dict(os.environ if environ is None else environ)
    file_config = parse_runtime_env_file(RUNTIME_ENV_FILE, environ=env)
    container_path = env.get("TABLETOP_CONTAINER_DATABASE_PATH")
    if not container_path:
        container_path = file_config.get("TABLETOP_CONTAINER_DATABASE_PATH")
    if not container_path:
        container_path = file_config.get("TABLETOP_DATABASE_PATH")
    if not container_path:
        raise SystemExit("TABLETOP_CONTAINER_DATABASE_PATH is required for Compose launch")
    compose_dir = env.get("TABLETOP_COMPOSE_DIR") or file_config.get("TABLETOP_COMPOSE_DIR")
    compose_path = Path(compose_dir).expanduser() if compose_dir else DEFAULT_RUNTIME_COMPOSE_DIR
    if not compose_path.is_absolute():
        compose_path = _REPO_ROOT / compose_path
    return str(container_path), compose_path.resolve()


def _compose_base_command(
    *,
    compose_files: Sequence[Path],
    runtime_env_file: Path | None = None,
) -> list[str]:
    runtime_env_file = RUNTIME_ENV_FILE if runtime_env_file is None else runtime_env_file
    command = [
        "docker",
        "compose",
        "--project-directory",
        str(_REPO_ROOT),
        "--env-file",
        str(Path(runtime_env_file).resolve()),
        "-f",
        str(COMPOSE_FILE),
    ]
    command.extend(
        item
        for compose_file in compose_files
        for item in ("-f", str(Path(compose_file).resolve()))
    )
    return command


def _compose_execution_command(
    service: str,
    action: Literal["start", "stop"],
    *,
    compose_files: Sequence[Path],
    runtime_env_file: Path,
) -> list[str]:
    command = _compose_base_command(
        compose_files=compose_files,
        runtime_env_file=runtime_env_file,
    )
    if action == "start":
        return [*command, "up", "-d", service]
    if action == "stop":
        return [*command, "stop", service]
    raise ValueError(f"unsupported Compose action: {action}")


def _run_compose(
    service: str,
    action: Literal["start", "stop"],
    *,
    env: Mapping[str, str],
    compose_files: Sequence[Path] = (),
) -> int:
    command = _compose_execution_command(
        service,
        action,
        compose_files=compose_files,
        runtime_env_file=RUNTIME_ENV_FILE,
    )
    completed = subprocess.run(command, cwd=_REPO_ROOT, env=env, check=False)
    return int(completed.returncode)


def _plugin_for_campaign(system_id: str, campaign_id: str) -> GameSystemPlugin:
    try:
        return load_plugin_registry().get(system_id)
    except PluginNotFoundError as exc:
        raise SystemExit(
            f"system plugin for campaign {campaign_id!r} is not installed"
        ) from exc


def _reject_archived(campaign: Mapping[str, Any], campaign_id: str) -> None:
    if campaign.get("archived_at"):
        raise SystemExit(
            f"campaign {campaign_id!r} is archived and cannot be modified"
        )


def _print_runtime_error(result: Mapping[str, Any]) -> int:
    error = result.get("error") or {}
    message = error.get("message") or error.get("code") or "command failed"
    print(message, flush=True)
    return 1


def cmd_campaign_create(args: argparse.Namespace) -> int:
    campaign_id = validate_campaign_id(args.campaign_id)
    registry = load_plugin_registry()
    try:
        plugin = registry.get(args.system_id)
    except PluginNotFoundError as exc:
        raise SystemExit(f"unknown system plugin {args.system_id!r}") from exc
    if not is_compatible_api_version(plugin.info.api_version):
        raise SystemExit(
            f"system plugin {args.system_id!r} api_version "
            f"{plugin.info.api_version!r} is incompatible"
        )

    conn = open_database()
    try:
        store = CampaignStore(conn)
        if store.get_campaign(campaign_id) is not None:
            raise SystemExit(f"campaign {campaign_id!r} already exists")
        try:
            store.create_campaign(
                campaign_id=campaign_id,
                name=args.name,
                system_id=plugin.info.id,
                system_version=plugin.info.version,
            )
        except sqlite3.IntegrityError as exc:
            raise SystemExit(f"campaign {campaign_id!r} already exists") from exc
    finally:
        conn.close()
    print(f"created campaign {campaign_id}")
    return 0


def cmd_campaign_list(args: argparse.Namespace) -> int:
    conn = open_database()
    try:
        campaigns = CampaignStore(conn).list_campaigns(
            include_archived=bool(args.all)
        )
    finally:
        conn.close()
    if not campaigns:
        print("no campaigns")
        return 0
    for campaign in campaigns:
        archived = campaign.get("archived_at")
        suffix = "\tarchived" if archived else ""
        print(
            f"{campaign['campaign_id']}\t{campaign['name']}\t"
            f"{campaign['system_id']}{suffix}"
        )
    return 0


def cmd_campaign_inspect(args: argparse.Namespace) -> int:
    conn = open_database()
    try:
        campaign = CampaignStore(conn).get_campaign(args.campaign_id)
        if campaign is None:
            raise SystemExit(f"campaign {args.campaign_id!r} not found")
        membership = MembershipStore(conn)
        participants = membership.list_participants(args.campaign_id)
        principals = membership.list_principals(args.campaign_id)
        controls = membership.list_controls(args.campaign_id)
    finally:
        conn.close()
    for key in (
        "campaign_id",
        "name",
        "system_id",
        "system_version",
        "setting_id",
        "created_at",
        "archived_at",
    ):
        print(f"{key}: {campaign.get(key)}")
    print(f"participants: {len(participants)}")
    for row in participants:
        print(f"  {row['participant_id']}\t{row['role']}\t{row['display_name']}")
    print(f"principals: {len(principals)}")
    for row in principals:
        print(f"  {row['participant_id']}\t{row['channel']}\t{row['external_id']}")
    print(f"controls: {len(controls)}")
    for row in controls:
        print(
            f"  {row['control_id']}\t{row['participant_id']}\t"
            f"{row['entity_id']}\t{row['control']}"
        )
    return 0


def cmd_campaign_select(args: argparse.Namespace) -> int:
    campaign_id = validate_campaign_id(args.campaign_id)
    database_path = require_database_path()
    conn = open_database()
    try:
        campaign = CampaignStore(conn).get_campaign(campaign_id)
    finally:
        conn.close()
    if campaign is None:
        raise SystemExit(f"campaign {campaign_id!r} not found")
    if campaign.get("archived_at"):
        raise SystemExit(
            f"campaign {campaign_id!r} is archived and cannot be selected"
        )
    write_active_campaign_file(database_path, campaign_id)
    print(f"selected campaign {campaign_id}")
    return 0


def cmd_session_start(args: argparse.Namespace) -> int:
    runtime = open_operator_runtime()
    try:
        result = runtime.start_session(
            json.dumps({"session_id": args.session_id})
        )
    finally:
        runtime.shutdown()
        if runtime._connection is not None:
            runtime._connection.close()
    if not result.get("ok"):
        return _print_runtime_error(result)
    print(f"started session {args.session_id}")
    return 0


def cmd_session_inspect(args: argparse.Namespace) -> int:
    runtime = open_operator_runtime()
    try:
        session_id = args.session_id
        if not session_id:
            assert runtime._connection is not None
            campaign_id = runtime.active_campaign
            row = runtime._connection.execute(
                "SELECT session_id FROM sessions "
                "WHERE campaign_id = ? AND ended_at IS NULL "
                "ORDER BY started_at DESC LIMIT 1",
                (campaign_id,),
            ).fetchone()
            if row is None:
                raise SystemExit("no open session")
            session_id = row["session_id"]
        result = runtime.read_session(session_id)
    finally:
        runtime.shutdown()
        if runtime._connection is not None:
            runtime._connection.close()
    if not result.get("ok"):
        return _print_runtime_error(result)
    data = result["data"]["session"]
    print(f"session_id: {data['session_id']}")
    print(f"started_at: {data.get('started_at')}")
    print(f"ended_at: {data.get('ended_at')}")
    return 0


def cmd_session_end(_args: argparse.Namespace) -> int:
    runtime = open_operator_runtime()
    try:
        result = runtime.end_session()
    finally:
        runtime.shutdown()
        if runtime._connection is not None:
            runtime._connection.close()
    if not result.get("ok"):
        return _print_runtime_error(result)
    print("ended session")
    return 0


def cmd_campaign_archive(args: argparse.Namespace) -> int:
    campaign_id = resolve_campaign_id(campaign_id=args.campaign_id)
    conn = open_database()
    try:
        store = CampaignStore(conn)
        campaign = store.get_campaign(campaign_id)
        if campaign is None:
            raise SystemExit(f"campaign {campaign_id!r} not found")
        if campaign.get("archived_at"):
            raise SystemExit(f"campaign {campaign_id!r} is already archived")
        if store.has_open_session(campaign_id):
            raise SystemExit("open_session")
        archived_at = datetime.now(timezone.utc).isoformat()
        try:
            with transaction(conn):
                store.archive_campaign(campaign_id, archived_at=archived_at)
                EventStore(conn).append_in_transaction(
                    conn,
                    campaign_id,
                    GameEvent(
                        event_type=EventType.CAMPAIGN_ARCHIVED.value,
                        payload={"archived_at": archived_at},
                    ),
                    occurred_at=archived_at,
                )
        except LookupError as exc:
            raise SystemExit(str(exc)) from exc
    finally:
        conn.close()
    clear_active_campaign_file(require_database_path(), campaign_id)
    print(f"archived campaign {campaign_id}")
    return 0


def cmd_campaign_restore(args: argparse.Namespace) -> int:
    campaign_id = resolve_campaign_id(campaign_id=args.campaign_id)
    conn = open_database()
    try:
        store = CampaignStore(conn)
        campaign = store.get_campaign(campaign_id)
        if campaign is None:
            raise SystemExit(f"campaign {campaign_id!r} not found")
        if not campaign.get("archived_at"):
            raise SystemExit(f"campaign {campaign_id!r} is not archived")
        restored_at = datetime.now(timezone.utc).isoformat()
        try:
            with transaction(conn):
                store.restore_campaign(campaign_id)
                EventStore(conn).append_in_transaction(
                    conn,
                    campaign_id,
                    GameEvent(
                        event_type=EventType.CAMPAIGN_RESTORED.value,
                        payload={"restored_at": restored_at},
                    ),
                    occurred_at=restored_at,
                )
        except LookupError as exc:
            raise SystemExit(str(exc)) from exc
    finally:
        conn.close()
    print(f"restored campaign {campaign_id}")
    return 0


def cmd_campaign_export(args: argparse.Namespace) -> int:
    campaign_id = validate_campaign_id(args.campaign_id)
    output_dir = Path(args.output_dir).expanduser()
    conn = open_database()
    try:
        try:
            manifest = export_campaign(
                conn,
                campaign_id,
                output_dir,
                migrations_dir=migrations_dir(),
            )
        except PackageError as exc:
            raise SystemExit(str(exc)) from exc
    finally:
        conn.close()
    print(f"exported campaign {campaign_id} digest={manifest['package_digest']}")
    return 0


def cmd_campaign_restore_package(args: argparse.Namespace) -> int:
    package_dir = Path(args.package_dir).expanduser()
    conn = open_database()
    try:
        try:
            manifest = restore_package(conn, package_dir)
        except PackageError as exc:
            raise SystemExit(str(exc)) from exc
    finally:
        conn.close()
    print(f"restored package campaign {manifest['campaign_id']}")
    return 0


def cmd_campaign_fork(args: argparse.Namespace) -> int:
    new_id = validate_campaign_id(args.campaign_id)
    package_dir = Path(args.package_dir).expanduser()
    conn = open_database()
    try:
        try:
            result = fork_package(conn, package_dir, new_id)
        except PackageError as exc:
            raise SystemExit(str(exc)) from exc
    finally:
        conn.close()
    print(
        f"forked campaign {result['forked_from']} -> {result['campaign_id']}"
    )
    return 0


def _generate_compose_override(*, participant_id: str, runtime_compose_dir: Path) -> Path:
    participant_id = validate_participant_id(participant_id)
    runtime_compose_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(runtime_compose_dir, 0o700)
    destination = runtime_compose_dir / f"docker-compose.override-{participant_id}.yml"
    content = yaml.safe_dump(
        player_compose_definition(participant_id),
        sort_keys=True,
        allow_unicode=True,
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.chmod(temporary_path, 0o600)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return destination


def _compose_service_name(*, gm: bool, participant_id: str | None) -> str:
    if gm and participant_id is not None:
        raise ValueError("pass either --gm or --participant, not both")
    if gm:
        return "omega"
    if not participant_id:
        raise ValueError("pass --gm or --participant <id>")
    return f"omega-player-{validate_participant_id(participant_id)}"


def _readiness_failure(report: Mapping[str, Any]) -> None:
    if report["exit_nonzero"]:
        raise ValueError("readiness errors: " + "; ".join(report["errors"]))


def _resolve_launch_context(
    action: Literal["start", "stop"],
    *,
    campaign_id: str,
    container_database_path: str,
    runtime_compose_dir: Path,
    participant_id: str | None = None,
    gm: bool = False,
    channel: str | None = None,
    expected_sender: str | None = None,
    role: str | None = None,
    host_database_path: Path | None = None,
) -> LaunchContext:
    if action == "stop":
        if gm and participant_id is not None:
            raise ValueError("pass either --gm or --participant, not both")
        if not gm and participant_id is None:
            raise ValueError("pass --gm or --participant <id>")
        normalized_participant = None if gm else validate_participant_id(participant_id or "")
        env = build_launch_env(
            campaign_id=campaign_id,
            participant_id=normalized_participant,
            role=None,
            channel=None,
            expected_sender=None,
            action="stop",
            container_database_path=container_database_path,
        )
        return LaunchContext(
            action="stop",
            service_name=_compose_service_name(gm=gm, participant_id=normalized_participant),
            env=env,
            host_database_path=None,
            container_database_path=container_database_path,
            runtime_compose_dir=runtime_compose_dir,
            participant_id=normalized_participant,
            workspace=None,
        )
    if action != "start":
        raise ValueError(f"unsupported launch action: {action}")
    if host_database_path is None:
        raise ValueError("host database path is required for start")
    host_database_path = Path(host_database_path)
    channel = validate_channel(channel or "")
    if gm and participant_id is not None:
        raise ValueError("pass either --gm or --participant, not both")
    if not gm and participant_id is None:
        raise ValueError("pass --gm or --participant <id>")
    requested_participant = None if gm else validate_participant_id(participant_id or "")
    conn = open_database({"TABLETOP_DATABASE_PATH": str(host_database_path)})
    try:
        _readiness_failure(
            readiness_report(
                conn,
                campaign_id,
                environ={},
                require_reviewed=True,
                check_environment=False,
            )
        )
        participants = MembershipStore(conn).list_participants(campaign_id)
        if gm:
            gm_rows = [row for row in participants if row["role"] == "gm"]
            if len(gm_rows) != 1:
                raise ValueError(
                    f"campaign requires exactly one GM participant, found {len(gm_rows)}"
                )
            participant = gm_rows[0]
        else:
            participant = next(
                (
                    row
                    for row in participants
                    if row["participant_id"] == requested_participant
                    and row["role"] == "player"
                ),
                None,
            )
            if participant is None:
                raise ValueError(
                    f"player participant {requested_participant!r} is not in campaign {campaign_id!r}"
                )
        resolved_participant = str(participant["participant_id"])
        resolved_role = str(participant["role"])
        if role is not None and role != resolved_role:
            raise ValueError("requested participant role does not match campaign data")
        bound_sender = sender_binding.bound_external_id(
            conn, campaign_id, resolved_participant, channel
        )
        if bound_sender is None:
            raise ValueError(
                f"no principal binding for {resolved_participant!r} on {channel}"
            )
        env = build_launch_env(
            campaign_id=campaign_id,
            participant_id=resolved_participant,
            role=resolved_role,
            channel=channel,
            expected_sender=bound_sender,
            action="start",
            container_database_path=container_database_path,
        )
        _readiness_failure(
            readiness_report(
                conn,
                campaign_id,
                environ=env,
                require_reviewed=True,
                check_persisted=False,
            )
        )
        workspace = Workspace.CAMPAIGN if gm else Workspace.PLAYER
        sender_binding.verify_startup_binding(
            conn,
            workspace=workspace,
            campaign_id=campaign_id,
            participant_id=resolved_participant,
            environ=env,
        )
    finally:
        conn.close()
    return LaunchContext(
        action="start",
        service_name=_compose_service_name(
            gm=gm,
            participant_id=None if gm else resolved_participant,
        ),
        env=env,
        host_database_path=host_database_path,
        container_database_path=container_database_path,
        runtime_compose_dir=runtime_compose_dir,
        participant_id=resolved_participant,
        workspace=workspace,
    )


def _resolve_inside_cwd(path_arg: str) -> Path:
    cwd = Path.cwd().resolve()
    resolved = Path(path_arg).expanduser().resolve()
    try:
        resolved.relative_to(cwd)
    except ValueError as exc:
        raise SystemExit(f"path {path_arg!r} escapes the working directory") from exc
    return resolved


def _load_state_json(path_arg: str) -> dict[str, Any]:
    path = _resolve_inside_cwd(path_arg)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("state JSON must be an object")
    return payload


def cmd_entity_create(args: argparse.Namespace) -> int:
    campaign_id = resolve_campaign_id(campaign_id=getattr(args, "campaign", None))
    state = _load_state_json(args.state) if args.state else {}
    conn = open_database()
    try:
        store = CampaignStore(conn)
        campaign = store.get_campaign(campaign_id)
        if campaign is None:
            raise SystemExit(f"campaign {campaign_id!r} not found")
        _reject_archived(campaign, campaign_id)
        plugin = _plugin_for_campaign(campaign["system_id"], campaign_id)
        validation = plugin.validate_entity_state(args.kind, state)
        if not validation.valid:
            for issue in validation.issues:
                print(f"{issue.code}: {issue.message}")
            return 1
        existing = store.get_entity(campaign_id, args.entity_id)
        if existing is not None and not args.replace:
            raise SystemExit(f"entity {args.entity_id!r} already exists")
        store.upsert_entity(
            campaign_id,
            args.entity_id,
            args.name,
            entity_type=args.kind,
            system_state=state,
        )
    finally:
        conn.close()
    print(f"upserted entity {args.entity_id}")
    return 0


def cmd_entity_update(args: argparse.Namespace) -> int:
    campaign_id = resolve_campaign_id(campaign_id=getattr(args, "campaign", None))
    state = _load_state_json(args.state) if args.state else None
    conn = open_database()
    try:
        store = CampaignStore(conn)
        campaign = store.get_campaign(campaign_id)
        if campaign is None:
            raise SystemExit(f"campaign {campaign_id!r} not found")
        existing = store.get_entity(campaign_id, args.entity_id)
        if existing is None:
            raise SystemExit(f"entity {args.entity_id!r} not found")
        _reject_archived(campaign, campaign_id)
        plugin = _plugin_for_campaign(campaign["system_id"], campaign_id)
        payload = state if state is not None else existing["system_state"]
        kind = args.kind or existing.get("entity_type") or "character"
        validation = plugin.validate_entity_state(kind, payload)
        if not validation.valid:
            for issue in validation.issues:
                print(f"{issue.code}: {issue.message}")
            return 1
        store.upsert_entity(
            campaign_id,
            args.entity_id,
            args.name or existing["name"],
            entity_type=kind,
            system_state=payload,
            metadata=existing.get("metadata"),
            overrides_id=existing.get("overrides_id"),
        )
    finally:
        conn.close()
    print(f"updated entity {args.entity_id}")
    return 0


def cmd_state_validate(args: argparse.Namespace) -> int:
    campaign_id = resolve_campaign_id(campaign_id=getattr(args, "campaign", None))
    payload = _load_state_json(args.state)
    conn = open_database()
    try:
        campaign = CampaignStore(conn).get_campaign(campaign_id)
    finally:
        conn.close()
    if campaign is None:
        raise SystemExit(f"campaign {campaign_id!r} not found")
    plugin = _plugin_for_campaign(campaign["system_id"], campaign_id)
    result = plugin.validate_state(payload)
    if not result.valid:
        for issue in result.issues:
            print(f"{issue.code}: {issue.message}")
        return 1
    print("state: valid")
    return 0


def cmd_state_apply(args: argparse.Namespace) -> int:
    campaign_id = resolve_campaign_id(campaign_id=getattr(args, "campaign", None))
    payload = _load_state_json(args.state)
    conn = open_database()
    try:
        store = CampaignStore(conn)
        campaign = store.get_campaign(campaign_id)
        if campaign is None:
            raise SystemExit(f"campaign {campaign_id!r} not found")
        _reject_archived(campaign, campaign_id)
        plugin = _plugin_for_campaign(campaign["system_id"], campaign_id)
        result = plugin.validate_state(payload)
        if not result.valid:
            for issue in result.issues:
                print(f"{issue.code}: {issue.message}")
            return 1
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        with transaction(conn):
            conn.execute(
                "UPDATE campaigns SET system_state = ? WHERE campaign_id = ?",
                (encoded, campaign_id),
            )
    finally:
        conn.close()
    print(f"applied state for {campaign_id}")
    return 0


def _ingest_markdown(path: Path, *, campaign_id: str, content_pack_id: str | None) -> str:
    conn = open_database()
    try:
        campaign = CampaignStore(conn).get_campaign(campaign_id)
        if campaign is None:
            raise SystemExit(f"campaign {campaign_id!r} not found")
        _reject_archived(campaign, campaign_id)
        context = IngestContext(
            connection=conn,
            content_pack_id=content_pack_id,
            system_id=campaign["system_id"],
            visibility="GM",
        )
        document_id = MarkdownIngestor().ingest(path, context)
    finally:
        conn.close()
    return document_id


def cmd_library_ingest(args: argparse.Namespace) -> int:
    campaign_id = resolve_campaign_id(campaign_id=args.campaign)
    path = _resolve_inside_cwd(args.path)
    document_id = _ingest_markdown(path, campaign_id=campaign_id, content_pack_id=None)
    print(f"ingested document {document_id}")
    return 0


def cmd_document_add(args: argparse.Namespace) -> int:
    campaign_id = resolve_campaign_id(campaign_id=getattr(args, "campaign", None))
    path = _resolve_inside_cwd(args.path)
    document_id = _ingest_markdown(path, campaign_id=campaign_id, content_pack_id=None)
    print(f"ingested document {document_id}")
    return 0


def cmd_content_pack_validate(args: argparse.Namespace) -> int:
    directory = Path(args.directory).expanduser().resolve()
    try:
        manifest = load_content_pack(directory)
    except ContentPackError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"valid content pack {manifest.id}")
    return 0


def cmd_content_pack_list(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve() if args.root else Path.cwd() / "library"
    if not root.is_dir():
        print("no content packs")
        return 0
    found = False
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            manifest = load_content_pack(child)
        except ContentPackError:
            continue
        found = True
        print(f"{manifest.id}\t{manifest.name}\t{manifest.version}")
    if not found:
        print("no content packs")
    return 0


def cmd_content_pack_ingest(args: argparse.Namespace) -> int:
    campaign_id = resolve_campaign_id(campaign_id=args.campaign)
    directory = Path(args.directory).expanduser().resolve()
    try:
        manifest = load_content_pack(directory)
    except ContentPackError as exc:
        raise SystemExit(str(exc)) from exc
    ingested = 0
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "content-pack.yaml":
            continue
        if path.suffix.lower() not in {".md", ".txt", ".markdown"}:
            continue
        _ingest_markdown(
            path,
            campaign_id=campaign_id,
            content_pack_id=manifest.id,
        )
        ingested += 1
    print(f"ingested {ingested} files from content pack {manifest.id}")
    return 0


def cmd_participant_add(args: argparse.Namespace) -> int:
    campaign_id = resolve_campaign_id(campaign_id=getattr(args, "campaign", None))
    participant_id = validate_participant_id(args.participant_id)
    conn = open_database()
    try:
        MembershipStore(conn).add_participant(
            campaign_id,
            participant_id,
            args.name,
            args.role,
        )
    except (sqlite3.IntegrityError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        conn.close()
    print(f"added participant {participant_id}")
    return 0


def cmd_participant_bind(args: argparse.Namespace) -> int:
    campaign_id = resolve_campaign_id(campaign_id=getattr(args, "campaign", None))
    conn = open_database()
    try:
        MembershipStore(conn).bind_principal(
            campaign_id,
            args.participant,
            args.channel,
            args.external_id,
        )
    except sqlite3.IntegrityError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        conn.close()
    print(f"bound {args.participant} on {args.channel}")
    return 0


def cmd_participant_unbind(args: argparse.Namespace) -> int:
    campaign_id = resolve_campaign_id(campaign_id=getattr(args, "campaign", None))
    conn = open_database()
    try:
        MembershipStore(conn).unbind_principal(
            campaign_id,
            args.participant,
            args.channel,
        )
    except LookupError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        conn.close()
    print(f"unbound {args.participant} on {args.channel}")
    return 0


def cmd_character_grant(args: argparse.Namespace) -> int:
    campaign_id = resolve_campaign_id(campaign_id=getattr(args, "campaign", None))
    conn = open_database()
    try:
        control_id = MembershipStore(conn).grant_control(
            campaign_id,
            args.participant,
            args.entity,
            args.control,
        )
    except (sqlite3.IntegrityError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        conn.close()
    print(f"granted control {control_id}")
    return 0


def cmd_character_revoke(args: argparse.Namespace) -> int:
    campaign_id = resolve_campaign_id(campaign_id=getattr(args, "campaign", None))
    conn = open_database()
    try:
        MembershipStore(conn).revoke_control(campaign_id, args.control_id)
    except LookupError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        conn.close()
    print(f"revoked control {args.control_id}")
    return 0


def cmd_system_list(_args: argparse.Namespace) -> int:
    registry = load_plugin_registry()
    for plugin in registry.list():
        version = plugin.info.version or "-"
        print(f"{plugin.info.id}\t{plugin.info.name}\t{version}")
    return 0


def cmd_system_inspect(args: argparse.Namespace) -> int:
    registry = load_plugin_registry()
    try:
        plugin = registry.get(args.system_id)
    except PluginNotFoundError as exc:
        raise SystemExit(f"unknown system plugin {args.system_id!r}") from exc

    print(f"id: {plugin.info.id}")
    print(f"name: {plugin.info.name}")
    print(f"version: {plugin.info.version}")
    print(f"api_version: {plugin.info.api_version}")
    print(f"description: {plugin.info.description}")
    caps = sorted(cap.value for cap in plugin.capabilities())
    print(f"capabilities: {', '.join(caps) if caps else '(none)'}")
    print(f"character_schema: {json.dumps(dict(plugin.character_schema()), sort_keys=True)}")
    print(f"state_schema: {json.dumps(dict(plugin.state_schema()), sort_keys=True)}")
    print("entity_validation: available")
    if args.state_path:
        path = Path(args.state_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = plugin.validate_state(payload)
        if not result.valid:
            for issue in result.issues:
                print(f"state_issue: {issue.code}: {issue.message}")
            return 1
        print("state: valid")
    return 0


def cmd_campaign_import(args: argparse.Namespace) -> int:
    campaign_id = validate_campaign_id(args.campaign)
    path = Path(args.path).expanduser()
    fmt = args.format
    if fmt == "auto":
        suffix = path.suffix.lower()
        if suffix == ".json":
            fmt = "json"
        else:
            fmt = "notes"
    conn = open_database()
    try:
        if CampaignStore(conn).get_campaign(campaign_id) is None:
            raise SystemExit(f"campaign {campaign_id!r} not found")
        if fmt == "json":
            batch = JsonCampaignImporter().load(str(path))
            import_id = ImportStore(conn).stage_batch(campaign_id, batch)
            count = len(batch.items)
        else:
            root = Path(args.import_root).expanduser() if args.import_root else path.parent
            import_id = stage_notes(
                conn, campaign_id, str(path), import_root=root
            )
            count = len(ImportStore(conn).list_items(import_id))
    finally:
        conn.close()
    print(f"staged import {import_id} with {count} items")
    return 0


def cmd_campaign_import_status(args: argparse.Namespace) -> int:
    conn = open_database()
    try:
        report = import_status_report(conn, args.import_id)
        items = ImportStore(conn).list_items(args.import_id)
    finally:
        conn.close()
    if getattr(args, "output_format", "text") == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    print(f"status: {report['status']}")
    print(f"items: {len(items)}")
    print(f"Staging: {report['staging']['conflicts']} staged conflicts")
    print(
        f"Campaign: {report['campaign']['unresolved_contradictions']} "
        "unresolved contradiction"
    )
    counts = report["counts"]
    print(
        "counts: "
        f"pending={counts['pending_review']} applied={counts['applied']} "
        f"rejected={counts['rejected']} unapplyable={counts['unapplyable']}"
    )
    for item in items:
        print(
            f"  {item['item_id']}\t{item['kind']}\t{item['review_state']}\t"
            f"{item.get('proposed_key') or '-'}"
        )
    return 0


def cmd_campaign_import_apply(args: argparse.Namespace) -> int:
    conn = open_database()
    try:
        result = apply_item(conn, args.item_id)
    except ApplyBlockedError as exc:
        print(f"apply blocked: {exc}")
        return 1
    except (LookupError, ValueError) as exc:
        print(f"apply failed: {exc}")
        return 1
    finally:
        conn.close()
    print(
        f"status: {result['status']} item={result['item_id']} "
        f"target={result.get('applied_target_id')}"
    )
    return 0


def cmd_campaign_import_review(args: argparse.Namespace) -> int:
    conn = open_database()
    try:
        review_item(
            conn,
            args.item_id,
            reject=bool(args.reject),
            mark_unapplyable=bool(args.mark_unapplyable),
        )
    except (LookupError, ValueError) as exc:
        print(f"review failed: {exc}")
        return 1
    finally:
        conn.close()
    state = "rejected" if args.reject else "unapplyable"
    print(f"reviewed {args.item_id} -> {state}")
    return 0


def cmd_campaign_resume(args: argparse.Namespace) -> int:
    campaign_id = resolve_campaign_id(campaign_id=args.campaign_id)
    conn = open_database()
    try:
        snap = resume_snapshot(conn, campaign_id)
    finally:
        conn.close()
    if getattr(args, "output_format", "text") == "json":
        print(json.dumps(snap, indent=2, sort_keys=True))
        return 0
    print(f"campaign: {snap['campaign_id']}")
    print(f"in_world_date: {snap['in_world_date']}")
    print(f"scene: {snap['scene']}")
    print(f"authoritative_contradictions: {snap['authoritative_contradictions']}")
    print(f"pending_imports: {snap['pending_imports']}")
    print(f"pending_import_conflicts: {snap['pending_import_conflicts']}")
    if snap.get("latest_session"):
        print(f"latest_session: {snap['latest_session']['session_id']}")
    return 0


def cmd_campaign_validate(args: argparse.Namespace) -> int:
    campaign_id = resolve_campaign_id(campaign_id=args.campaign_id)
    conn = open_database()
    try:
        report = readiness_report(
            conn,
            campaign_id,
            require_reviewed=bool(getattr(args, "require_reviewed", False)),
        )
    finally:
        conn.close()
    if getattr(args, "output_format", "text") == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"campaign: {report['campaign_id']}")
        print(f"ok: {report['ok']}")
        for label in ("errors", "warnings", "notices"):
            for item in report[label]:
                print(f"{label[:-1]}: {item}")
    return 1 if report["exit_nonzero"] else 0


def cmd_campaign_start(args: argparse.Namespace) -> int:
    campaign_id = validate_campaign_id(args.campaign_id)
    host_database_path = require_database_path()
    container_database_path, runtime_compose_dir = _runtime_configuration()
    try:
        launch = _resolve_launch_context(
            "start",
            campaign_id=campaign_id,
            participant_id=getattr(args, "participant_id", None),
            gm=bool(getattr(args, "gm", False)),
            channel=getattr(args, "channel", None),
            host_database_path=host_database_path,
            container_database_path=container_database_path,
            runtime_compose_dir=runtime_compose_dir,
        )
        compose_files: tuple[Path, ...] = ()
        if launch.participant_id is not None and not bool(getattr(args, "gm", False)):
            compose_files = (
                _generate_compose_override(
                    participant_id=launch.participant_id,
                    runtime_compose_dir=runtime_compose_dir,
                ),
            )
        code = _run_compose(
            launch.service_name,
            "start",
            env=launch.env,
            compose_files=compose_files,
        )
    except (ValueError, sender_binding.SenderBindingError) as exc:
        print(f"start refused: {exc}")
        return 1
    if code == 0:
        print(f"started {launch.service_name} for campaign {campaign_id}")
    return code


def cmd_campaign_stop(args: argparse.Namespace) -> int:
    campaign_id = validate_campaign_id(args.campaign_id)
    container_database_path, runtime_compose_dir = _runtime_configuration()
    participant_id = getattr(args, "participant_id", None)
    try:
        launch = _resolve_launch_context(
            "stop",
            campaign_id=campaign_id,
            participant_id=participant_id,
            gm=bool(getattr(args, "gm", False)),
            container_database_path=container_database_path,
            runtime_compose_dir=runtime_compose_dir,
        )
        compose_files: tuple[Path, ...] = ()
        if launch.participant_id is not None:
            compose_files = (
                _generate_compose_override(
                    participant_id=launch.participant_id,
                    runtime_compose_dir=runtime_compose_dir,
                ),
            )
        code = _run_compose(
            launch.service_name,
            "stop",
            env=launch.env,
            compose_files=compose_files,
        )
    except ValueError as exc:
        print(f"stop refused: {exc}")
        return 1
    if code == 0:
        print(f"stopped {launch.service_name} for campaign {campaign_id}")
    return code
