"""Argparse front door for the operator CLI."""

from __future__ import annotations

import argparse
from typing import Sequence

from tabletop.cli import handlers
from tabletop.cli.util import DATABASE_PATH_ENV_VAR, require_database_path

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
    create.set_defaults(handler=handlers.cmd_campaign_create)

    list_cmd = campaign_sub.add_parser("list", help="List campaigns stored in SQLite.")
    list_cmd.add_argument(
        "--all",
        action="store_true",
        help="Include archived campaigns.",
    )
    list_cmd.set_defaults(handler=handlers.cmd_campaign_list)

    inspect_cmd = campaign_sub.add_parser(
        "inspect",
        help="Inspect one campaign from SQLite.",
    )
    inspect_cmd.add_argument("campaign_id")
    inspect_cmd.set_defaults(handler=handlers.cmd_campaign_inspect)

    select_cmd = campaign_sub.add_parser(
        "select",
        help="Select the active campaign for the next process start.",
    )
    select_cmd.add_argument("campaign_id")
    select_cmd.set_defaults(handler=handlers.cmd_campaign_select)

    archive = campaign_sub.add_parser("archive", help="Archive a campaign (ADR 0010).")
    archive.add_argument("--id", dest="campaign_id", default=None)
    archive.set_defaults(handler=handlers.cmd_campaign_archive)

    restore = campaign_sub.add_parser("restore", help="Restore an archived campaign.")
    restore.add_argument("--id", dest="campaign_id", default=None)
    restore.set_defaults(handler=handlers.cmd_campaign_restore)

    session = campaign_sub.add_parser("session", help="Start, inspect, and end sessions.")
    session_sub = session.add_subparsers(dest="session_command")
    session_start = session_sub.add_parser("start", help="Start a session.")
    session_start.add_argument("--session-id", required=True)
    session_start.set_defaults(handler=handlers.cmd_session_start)
    session_inspect = session_sub.add_parser("inspect", help="Inspect a session.")
    session_inspect.add_argument("--session-id", default=None)
    session_inspect.set_defaults(handler=handlers.cmd_session_inspect)
    session_end = session_sub.add_parser("end", help="End the open session.")
    session_end.set_defaults(handler=handlers.cmd_session_end)

    entity = campaign_sub.add_parser("entity", help="Create or update campaign entities.")
    entity_sub = entity.add_subparsers(dest="entity_command")
    entity_create = entity_sub.add_parser("create", help="Create a campaign entity.")
    entity_create.add_argument("--id", required=True, dest="entity_id")
    entity_create.add_argument("--kind", required=True)
    entity_create.add_argument("--name", required=True)
    entity_create.add_argument("--state", default=None)
    entity_create.add_argument("--replace", action="store_true")
    entity_create.add_argument("--campaign", default=None)
    entity_create.set_defaults(handler=handlers.cmd_entity_create)
    entity_update = entity_sub.add_parser("update", help="Update a campaign entity.")
    entity_update.add_argument("--id", required=True, dest="entity_id")
    entity_update.add_argument("--kind", default=None)
    entity_update.add_argument("--name", default=None)
    entity_update.add_argument("--state", default=None)
    entity_update.add_argument("--campaign", default=None)
    entity_update.set_defaults(handler=handlers.cmd_entity_update)

    participant = campaign_sub.add_parser(
        "participant",
        help="Add and bind campaign participants.",
    )
    participant_sub = participant.add_subparsers(dest="participant_command")
    participant_add = participant_sub.add_parser("add", help="Add a participant.")
    participant_add.add_argument("--id", required=True, dest="participant_id")
    participant_add.add_argument("--name", required=True)
    participant_add.add_argument("--role", required=True, choices=("gm", "player"))
    participant_add.add_argument("--campaign", default=None)
    participant_add.set_defaults(handler=handlers.cmd_participant_add)
    participant_bind = participant_sub.add_parser("bind", help="Bind a channel principal.")
    participant_bind.add_argument("--participant", required=True)
    participant_bind.add_argument("--channel", required=True)
    participant_bind.add_argument("--external-id", required=True)
    participant_bind.add_argument("--campaign", default=None)
    participant_bind.set_defaults(handler=handlers.cmd_participant_bind)
    participant_unbind = participant_sub.add_parser(
        "unbind",
        help="Remove a channel principal binding.",
    )
    participant_unbind.add_argument("--participant", required=True)
    participant_unbind.add_argument("--channel", required=True)
    participant_unbind.add_argument("--campaign", default=None)
    participant_unbind.set_defaults(handler=handlers.cmd_participant_unbind)

    character = campaign_sub.add_parser(
        "character",
        help="Grant and revoke character control.",
    )
    character_sub = character.add_subparsers(dest="character_command")
    character_grant = character_sub.add_parser("grant", help="Grant character control.")
    character_grant.add_argument("--participant", required=True)
    character_grant.add_argument("--entity", required=True)
    character_grant.add_argument(
        "--control",
        required=True,
        choices=("owner", "shared", "gm", "temporary"),
    )
    character_grant.add_argument("--campaign", default=None)
    character_grant.set_defaults(handler=handlers.cmd_character_grant)
    character_revoke = character_sub.add_parser("revoke", help="Revoke character control.")
    character_revoke.add_argument("--control-id", required=True)
    character_revoke.add_argument("--campaign", default=None)
    character_revoke.set_defaults(handler=handlers.cmd_character_revoke)

    state = campaign_sub.add_parser("state", help="Validate or apply campaign system state.")
    state_sub = state.add_subparsers(dest="state_command")
    state_validate = state_sub.add_parser("validate", help="Validate opaque system state.")
    state_validate.add_argument("--state", required=True)
    state_validate.add_argument("--campaign", default=None)
    state_validate.set_defaults(handler=handlers.cmd_state_validate)
    state_apply = state_sub.add_parser("apply", help="Apply opaque system state.")
    state_apply.add_argument("--state", required=True)
    state_apply.add_argument("--campaign", default=None)
    state_apply.set_defaults(handler=handlers.cmd_state_apply)

    document = campaign_sub.add_parser("document", help="Add documents to a campaign.")
    document_sub = document.add_subparsers(dest="document_command")
    document_add = document_sub.add_parser("add", help="Ingest a markdown document.")
    document_add.add_argument("path")
    document_add.add_argument("--campaign", default=None)
    document_add.set_defaults(handler=handlers.cmd_document_add)

    system = subparsers.add_parser(
        "system",
        help="List and inspect installed game-system plugins.",
    )
    system_sub = system.add_subparsers(dest="system_command")
    system_list = system_sub.add_parser("list", help="List installed system plugins.")
    system_list.set_defaults(handler=handlers.cmd_system_list)
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
    system_inspect.set_defaults(handler=handlers.cmd_system_inspect)

    library = subparsers.add_parser("library", help="Ingest library documents.")
    library_sub = library.add_subparsers(dest="library_command")
    library_ingest = library_sub.add_parser("ingest", help="Ingest a markdown document.")
    library_ingest.add_argument("path")
    library_ingest.add_argument("--campaign", required=True)
    library_ingest.set_defaults(handler=handlers.cmd_library_ingest)

    content_pack = subparsers.add_parser(
        "content-pack",
        help="Validate, list, and ingest content packs.",
    )
    content_pack_sub = content_pack.add_subparsers(dest="content_pack_command")
    pack_validate = content_pack_sub.add_parser("validate", help="Validate a content pack.")
    pack_validate.add_argument("directory")
    pack_validate.set_defaults(handler=handlers.cmd_content_pack_validate)
    pack_list = content_pack_sub.add_parser("list", help="List discovered content packs.")
    pack_list.add_argument("--root", default=None)
    pack_list.set_defaults(handler=handlers.cmd_content_pack_list)
    pack_ingest = content_pack_sub.add_parser("ingest", help="Ingest a content pack.")
    pack_ingest.add_argument("directory")
    pack_ingest.add_argument("--campaign", required=True)
    pack_ingest.set_defaults(handler=handlers.cmd_content_pack_ingest)

    return parser


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
