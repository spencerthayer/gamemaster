"""Command handlers for the operator CLI."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from tabletop.api.errors import ContentPackError, PluginNotFoundError
from tabletop.api.events import GameEvent
from tabletop.api.plugin import is_compatible_api_version
from tabletop.campaign.event_store import EventStore, EventType
from tabletop.campaign.selection import write_active_campaign_file
from tabletop.campaign.store import CampaignStore
from tabletop.cli.runtime_factory import open_operator_runtime, resolve_campaign_id
from tabletop.cli.util import (
    load_plugin_registry,
    open_database,
    require_database_path,
    validate_campaign_id,
)
from tabletop.documents.content_pack import load_content_pack
from tabletop.documents.ingest import IngestContext
from tabletop.documents.markdown import MarkdownIngestor
from tabletop.storage.sqlite import transaction


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
        "archived_at",
    ):
        print(f"{key}: {campaign.get(key)}")
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
        registry = load_plugin_registry()
        plugin = registry.get(campaign["system_id"])
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
        registry = load_plugin_registry()
        plugin = registry.get(campaign["system_id"])
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
    plugin = load_plugin_registry().get(campaign["system_id"])
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
        plugin = load_plugin_registry().get(campaign["system_id"])
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
