"""Argparse front door for the operator CLI."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Sequence

from tabletop.api.errors import PluginNotFoundError
from tabletop.api.plugin import is_compatible_api_version
from tabletop.campaign.store import CampaignStore
from tabletop.cli.util import (
    DATABASE_PATH_ENV_VAR,
    load_plugin_registry,
    open_database,
    require_database_path,
    validate_campaign_id,
)

__all__ = [
    "DATABASE_PATH_ENV_VAR",
    "build_parser",
    "main",
    "require_database_path",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gamemaster",
        description="Operator CLI for Gamemaster campaign lifecycle.",
    )
    subparsers = parser.add_subparsers(dest="command")

    campaign = subparsers.add_parser(
        "campaign",
        help="Create, list, inspect, select, and manage campaigns.",
    )
    campaign_sub = campaign.add_subparsers(dest="campaign_command")

    create = campaign_sub.add_parser(
        "create",
        help="Create a campaign with an explicit id and installed system plugin.",
    )
    create.add_argument("--id", required=True, dest="campaign_id")
    create.add_argument("--name", required=True)
    create.add_argument("--system", required=True, dest="system_id")
    create.set_defaults(handler=_cmd_campaign_create)

    list_cmd = campaign_sub.add_parser(
        "list",
        help="List campaigns stored in SQLite.",
    )
    list_cmd.set_defaults(handler=_cmd_campaign_list)

    inspect_cmd = campaign_sub.add_parser(
        "inspect",
        help="Inspect one campaign from SQLite.",
    )
    inspect_cmd.add_argument("campaign_id")
    inspect_cmd.set_defaults(handler=_cmd_campaign_inspect)

    system = subparsers.add_parser(
        "system",
        help="List and inspect installed game-system plugins.",
    )
    system_sub = system.add_subparsers(dest="system_command")

    system_list = system_sub.add_parser("list", help="List installed system plugins.")
    system_list.set_defaults(handler=_cmd_system_list)

    system_inspect = system_sub.add_parser(
        "inspect",
        help="Inspect one installed system plugin.",
    )
    system_inspect.add_argument("system_id")
    system_inspect.add_argument(
        "--state",
        dest="state_path",
        help="Optional JSON file validated with validate_state.",
    )
    system_inspect.set_defaults(handler=_cmd_system_inspect)

    return parser


def _cmd_campaign_create(args: argparse.Namespace) -> int:
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


def _cmd_campaign_list(_args: argparse.Namespace) -> int:
    conn = open_database()
    try:
        campaigns = CampaignStore(conn).list_campaigns()
    finally:
        conn.close()
    if not campaigns:
        print("no campaigns")
        return 0
    for campaign in campaigns:
        print(
            f"{campaign['campaign_id']}\t{campaign['name']}\t"
            f"{campaign['system_id']}"
        )
    return 0


def _cmd_campaign_inspect(args: argparse.Namespace) -> int:
    conn = open_database()
    try:
        campaign = CampaignStore(conn).get_campaign(args.campaign_id)
    finally:
        conn.close()
    if campaign is None:
        raise SystemExit(f"campaign {args.campaign_id!r} not found")
    for key in (
        "campaign_id",
        "name",
        "system_id",
        "system_version",
        "setting_id",
        "created_at",
    ):
        print(f"{key}: {campaign.get(key)}")
    return 0


def _cmd_system_list(_args: argparse.Namespace) -> int:
    registry = load_plugin_registry()
    for plugin in registry.list():
        version = plugin.info.version or "-"
        print(f"{plugin.info.id}\t{plugin.info.name}\t{version}")
    return 0


def _cmd_system_inspect(args: argparse.Namespace) -> int:
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    handler = getattr(args, "handler", None)
    if handler is not None:
        return int(handler(args))
    if args.command is None:
        parser.print_help()
        return 0
    parser.error(f"command {args.command!r} requires a subcommand")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
