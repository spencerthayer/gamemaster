"""Command handlers for the operator CLI."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from tabletop.api.errors import ContentPackError, PluginNotFoundError
from tabletop.api.events import GameEvent
from tabletop.api.plugin import GameSystemPlugin, is_compatible_api_version
from tabletop.campaign.event_store import EventStore, EventType
from tabletop.campaign.membership import MembershipStore, validate_participant_id
from tabletop.campaign.readiness import readiness_report
from tabletop.campaign.resume import resume_snapshot
from tabletop.campaign.selection import (
    clear_active_campaign_file,
    write_active_campaign_file,
)
from tabletop.campaign.store import CampaignStore
from tabletop.cli.runtime_factory import open_operator_runtime, resolve_campaign_id
from tabletop.cli.util import (
    load_plugin_registry,
    migrations_dir,
    open_database,
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


def _compose_service_name(*, gm: bool, participant_id: str | None) -> str:
    if gm and participant_id:
        raise SystemExit("pass either --gm or --participant, not both")
    if gm:
        return "omega"
    if not participant_id:
        raise SystemExit("pass --gm or --participant <id>")
    return f"omega-player-{participant_id}"


def _run_compose(service: str, action: str, campaign_id: str) -> int:
    env = os.environ.copy()
    env["TABLETOP_CAMPAIGN"] = campaign_id
    command = [
        "docker",
        "compose",
        "--env-file",
        ".env.example",
        "-f",
        "docker-compose.yml",
        action,
    ]
    if action == "up":
        command.extend(["-d", "--no-deps", service])
    else:
        command.append(service)
    completed = subprocess.run(command, check=False)
    return int(completed.returncode)


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
    conn = open_database()
    try:
        report = readiness_report(conn, campaign_id, require_reviewed=True)
    finally:
        conn.close()
    if report["exit_nonzero"]:
        print("start refused: readiness errors")
        for item in report["errors"]:
            print(f"error: {item}")
        return 1
    participant_id = getattr(args, "participant_id", None)
    if participant_id:
        participant_id = validate_participant_id(participant_id)
    service = _compose_service_name(gm=bool(args.gm), participant_id=participant_id)
    code = _run_compose(service, "up", campaign_id)
    if code == 0:
        print(f"started {service} for campaign {campaign_id}")
    return code


def cmd_campaign_stop(args: argparse.Namespace) -> int:
    campaign_id = validate_campaign_id(args.campaign_id)
    participant_id = getattr(args, "participant_id", None)
    if participant_id:
        participant_id = validate_participant_id(participant_id)
    service = _compose_service_name(gm=bool(args.gm), participant_id=participant_id)
    code = _run_compose(service, "stop", campaign_id)
    if code == 0:
        print(f"stopped {service} for campaign {campaign_id}")
    return code
