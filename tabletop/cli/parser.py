"""Argparse front door for the operator CLI."""

from __future__ import annotations

import argparse
import sys
from typing import Any, Sequence

from tabletop.cli import handlers
from tabletop.cli.util import DATABASE_PATH_ENV_VAR, require_database_path

__all__ = [
    "DATABASE_PATH_ENV_VAR",
    "build_parser",
    "main",
    "require_database_path",
]


def build_parser(
    parser_class: type[argparse.ArgumentParser] = argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Build the CLI parser. The class is injectable for exit-code testing."""

    parser = parser_class(
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

    export_cmd = campaign_sub.add_parser(
        "export", help="Export a campaign as a native package."
    )
    export_cmd.add_argument("campaign_id")
    export_cmd.add_argument("--out", required=True, dest="output_dir")
    export_cmd.set_defaults(handler=handlers.cmd_campaign_export)

    restore_package = campaign_sub.add_parser(
        "restore-package",
        help="Restore a native campaign package into the database.",
    )
    restore_package.add_argument("package_dir")
    restore_package.set_defaults(handler=handlers.cmd_campaign_restore_package)

    fork_cmd = campaign_sub.add_parser(
        "fork",
        help="Fork a native package into a new campaign id.",
    )
    fork_cmd.add_argument("package_dir")
    fork_cmd.add_argument("--id", required=True, dest="campaign_id")
    fork_cmd.set_defaults(handler=handlers.cmd_campaign_fork)

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

    setup_cmd = campaign_sub.add_parser(
        "setup", help="Configure a campaign from a manifest, or interactively."
    )
    setup_cmd.add_argument(
        "--from", default=None, dest="manifest_path",
        help="Path to a campaign.setup.yaml",
    )
    setup_cmd.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Print the plan without writing anything",
    )
    setup_cmd.add_argument(
        "--yes", action="store_true", dest="assume_yes",
        help="Apply without an interactive confirmation",
    )
    setup_cmd.set_defaults(handler=handlers.cmd_campaign_setup)

    content = subparsers.add_parser(
        "content", help="Inspect, install, and list global content."
    )
    content_sub = content.add_subparsers(dest="content_command")
    content_inspect = content_sub.add_parser(
        "inspect", help="Classify a path without installing it."
    )
    content_inspect.add_argument("path")
    content_inspect.set_defaults(handler=handlers.cmd_content_inspect)
    content_install = content_sub.add_parser(
        "install", help="Install content into the global catalog."
    )
    content_install.add_argument("path")
    content_install.set_defaults(handler=handlers.cmd_content_install)
    content_list = content_sub.add_parser("list", help="List installed content.")
    content_list.set_defaults(handler=handlers.cmd_content_list)

    scene = campaign_sub.add_parser(
        "scene", help="Inspect and drive the authoritative scene."
    )
    scene_sub = scene.add_subparsers(dest="scene_command")
    scene_show = scene_sub.add_parser("show", help="Show the current scene.")
    scene_show.set_defaults(handler=handlers.cmd_scene_show)
    scene_open = scene_sub.add_parser("open", help="Open a new scene.")
    scene_open.add_argument("--scene-id", required=True, dest="scene_id")
    scene_open.add_argument("--name", required=True)
    scene_open.add_argument("--session-id", default=None, dest="session_id")
    scene_open.add_argument("--location", default=None, dest="location_entity_id")
    scene_open.set_defaults(handler=handlers.cmd_scene_open)
    scene_close = scene_sub.add_parser("close", help="Close the open scene.")
    scene_close.add_argument("--scene-id", default=None, dest="scene_id")
    scene_close.set_defaults(handler=handlers.cmd_scene_close)
    scene_transition = scene_sub.add_parser(
        "transition", help="Close the open scene and open another."
    )
    scene_transition.add_argument("--from", default=None, dest="from_scene_id")
    scene_transition.add_argument("--scene-id", required=True, dest="scene_id")
    scene_transition.add_argument("--name", required=True)
    scene_transition.add_argument("--session-id", default=None, dest="session_id")
    scene_transition.set_defaults(handler=handlers.cmd_scene_transition)
    scene_enter = scene_sub.add_parser("enter", help="Record a present entity.")
    scene_enter.add_argument("--entity-id", required=True, dest="entity_id")
    scene_enter.add_argument("--scene-id", default=None, dest="scene_id")
    scene_enter.add_argument(
        "--presence-type", required=True, dest="presence_type",
        choices=("pc", "npc", "summon", "prop"),
    )
    scene_enter.set_defaults(handler=handlers.cmd_scene_enter)
    scene_exit = scene_sub.add_parser("exit", help="End a present entity's presence.")
    scene_exit.add_argument("--entity-id", required=True, dest="entity_id")
    scene_exit.add_argument("--scene-id", default=None, dest="scene_id")
    scene_exit.set_defaults(handler=handlers.cmd_scene_exit)

    time_cmd = campaign_sub.add_parser(
        "time", help="Read and set the campaign in-world clock."
    )
    time_sub = time_cmd.add_subparsers(dest="time_command")
    time_show = time_sub.add_parser("show", help="Show the in-world clock.")
    time_show.set_defaults(handler=handlers.cmd_time_show)
    time_set = time_sub.add_parser("set", help="Set the in-world clock.")
    time_set.add_argument("--label", default=None, dest="in_world_label")
    time_set.add_argument(
        "--minutes", default=None, type=int, dest="in_world_minutes"
    )
    time_set.set_defaults(handler=handlers.cmd_time_set)

    campaign_content = campaign_sub.add_parser(
        "content", help="Attach and detach installed content packs."
    )
    campaign_content_sub = campaign_content.add_subparsers(dest="campaign_content_command")
    content_attach = campaign_content_sub.add_parser(
        "attach", help="Attach an installed pack to a campaign."
    )
    content_attach.add_argument("campaign")
    content_attach.add_argument("pack")
    content_attach.add_argument("--role", required=True)
    content_attach.set_defaults(handler=handlers.cmd_campaign_content_attach)
    content_detach = campaign_content_sub.add_parser(
        "detach", help="Detach a pack. The installed bytes stay."
    )
    content_detach.add_argument("campaign")
    content_detach.add_argument("pack")
    content_detach.set_defaults(handler=handlers.cmd_campaign_content_detach)
    content_enable = campaign_content_sub.add_parser(
        "set-enabled", help="Enable or disable an attached pack."
    )
    content_enable.add_argument("campaign")
    content_enable.add_argument("pack")
    content_enable.add_argument("--enabled", choices=("true", "false"), required=True)
    content_enable.set_defaults(handler=handlers.cmd_campaign_content_set_enabled)

    campaign_doc = campaign_sub.add_parser(
        "doc", help="Attach and detach installed documents."
    )
    doc_sub = campaign_doc.add_subparsers(dest="campaign_doc_command")
    doc_attach = doc_sub.add_parser(
        "attach", help="Attach an installed document to a campaign."
    )
    doc_attach.add_argument("campaign")
    doc_attach.add_argument("path")
    doc_attach.add_argument("--role", required=True)
    doc_attach.add_argument("--gm-only", action="store_true", dest="gm_only")
    doc_attach.set_defaults(handler=handlers.cmd_campaign_document_attach)
    doc_detach = doc_sub.add_parser(
        "detach", help="Detach a document. The installed bytes stay."
    )
    doc_detach.add_argument("campaign")
    doc_detach.add_argument("path")
    doc_detach.set_defaults(handler=handlers.cmd_campaign_document_detach)

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

    campaign_import = campaign_sub.add_parser(
        "import",
        help="Stage a structured external campaign or notes as proposals.",
    )
    campaign_import.add_argument("path")
    campaign_import.add_argument("--campaign", required=True)
    campaign_import.add_argument(
        "--format",
        choices=("auto", "json", "notes"),
        default="auto",
    )
    campaign_import.add_argument("--import-root", default=None)
    campaign_import.set_defaults(handler=handlers.cmd_campaign_import)

    import_status = campaign_sub.add_parser(
        "import-status",
        help="Show staged import batch status.",
    )
    import_status.add_argument("--import-id", required=True)
    import_status.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        dest="output_format",
    )
    import_status.set_defaults(handler=handlers.cmd_campaign_import_status)

    import_apply = campaign_sub.add_parser(
        "import-apply",
        help="Accept a staged import item into authoritative campaign state.",
    )
    import_apply.add_argument("item_id")
    import_apply.set_defaults(handler=handlers.cmd_campaign_import_apply)

    import_review = campaign_sub.add_parser(
        "import-review",
        help="Reject or mark a staged import item unapplyable.",
    )
    import_review.add_argument("item_id")
    review_group = import_review.add_mutually_exclusive_group(required=True)
    review_group.add_argument("--reject", action="store_true")
    review_group.add_argument("--mark-unapplyable", action="store_true")
    import_review.set_defaults(handler=handlers.cmd_campaign_import_review)

    resume = campaign_sub.add_parser(
        "resume",
        help="Show a read-only resume snapshot without inventing unknowns.",
    )
    resume.add_argument("campaign_id", nargs="?", default=None)
    resume.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        dest="output_format",
    )
    resume.set_defaults(handler=handlers.cmd_campaign_resume)

    validate = campaign_sub.add_parser(
        "validate",
        help="Report campaign readiness errors, warnings, and notices.",
    )
    validate.add_argument("--id", dest="campaign_id", default=None)
    validate.add_argument("--require-reviewed", action="store_true")
    validate.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        dest="output_format",
    )
    validate.add_argument(
        "--live", action="store_true",
        help="Run bounded environment probes in addition to static checks.",
    )
    validate.add_argument(
        "--channel-probe", action="store_true", dest="channel_probe",
        help="Also send a real test message. Off by default.",
    )
    validate.set_defaults(handler=handlers.cmd_campaign_validate)

    readiness = campaign_sub.add_parser(
        "readiness",
        help="Alias for campaign validate.",
    )
    readiness.add_argument("--id", dest="campaign_id", default=None)
    readiness.add_argument("--require-reviewed", action="store_true")
    readiness.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        dest="output_format",
    )
    readiness.set_defaults(handler=handlers.cmd_campaign_validate)

    start = campaign_sub.add_parser(
        "start",
        help="Validate then delegate launch to Compose.",
    )
    start.add_argument("campaign_id")
    start_group = start.add_mutually_exclusive_group(required=True)
    start_group.add_argument("--gm", action="store_true")
    start_group.add_argument("--participant", dest="participant_id", default=None)
    start.add_argument(
        "--channel",
        required=True,
        choices=tuple(handlers.CHANNELS),
        help="Authoritative externally reachable channel.",
    )
    start.set_defaults(handler=handlers.cmd_campaign_start)
    stop = campaign_sub.add_parser(
        "stop",
        help="Delegate stop to Compose.",
    )
    stop.add_argument("campaign_id")
    stop_group = stop.add_mutually_exclusive_group(required=True)
    stop_group.add_argument("--gm", action="store_true")
    stop_group.add_argument("--participant", dest="participant_id", default=None)
    stop.set_defaults(handler=handlers.cmd_campaign_stop)

    return parser


class _ArgumentError(Exception):
    """argparse reported an invalid invocation.

    Raised instead of exiting so `campaign validate` can return its own
    documented class, 3, rather than argparse's default 2.
    """


class _ExitCode(Exception):
    """argparse handled --help or --version; carry its exit code."""

    def __init__(self, code: int) -> None:
        self.code = code


class _Parser(argparse.ArgumentParser):
    """ArgumentParser that reports failures as exceptions instead of exiting."""

    def error(self, message: str) -> Any:
        raise _ArgumentError(message)

    def exit(self, status: int = 0, message: str | None = None) -> Any:
        if message:
            print(message)
        raise _ExitCode(status)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser(_Parser)
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except _ExitCode as exit_code:
        # --help still exits 0.
        return exit_code.code
    except _ArgumentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return handlers.EXIT_INVALID_INVOCATION
    handler = getattr(args, "handler", None)
    if handler is not None:
        return int(handler(args))
    if args.command is None:
        parser.print_help()
        return 0
    print(
        f"error: command {args.command!r} requires a subcommand", file=sys.stderr
    )
    return handlers.EXIT_INVALID_INVOCATION


if __name__ == "__main__":
    raise SystemExit(main())
