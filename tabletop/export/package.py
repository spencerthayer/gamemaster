"""Native campaign package export, restore, and fork."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

from tabletop.api.events import GameEvent
from tabletop.campaign.event_store import EventStore, EventType, CURRENT_EVENT_SCHEMA_VERSION
from tabletop.campaign.store import CampaignStore
from tabletop.export.manifest import (
    FORMAT,
    PackageError,
    canonical_json,
    compute_package_digest,
    sha256_bytes,
    sha256_text,
    validate_package_directory,
)
from tabletop.storage.sqlite import transaction

_CAMPAIGN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Tables exported under tables/*.jsonl for one campaign (plus setting siblings).
CAMPAIGN_EXPORT_TABLES: tuple[str, ...] = (
    "campaigns",
    "entities",
    "facts",
    "relationships",
    "sessions",
    "scenes",
    "rulings",
    "participants",
    "participant_principals",
    "character_controls",
)

# Every campaign-scoped table that fork may copy maps to exactly one disposition.
# preserve: campaign-scoped natural ids stay; campaign_id is rewritten.
# regenerate: globally unique primary keys are remapped.
# omit: rows are not copied into the fork.
FORK_IDENTITY_POLICY: dict[str, str] = {
    "campaigns": "preserve",
    "entities": "preserve",
    "participants": "preserve",
    "character_controls": "preserve",
    "facts": "regenerate",
    "relationships": "regenerate",
    "sessions": "regenerate",
    "scenes": "regenerate",
    "rulings": "regenerate",
    "participant_principals": "omit",
}

_REGENERATE_ID_COLUMNS: dict[str, str] = {
    "facts": "fact_id",
    "relationships": "relationship_id",
    "sessions": "session_id",
    "scenes": "scene_id",
    "rulings": "ruling_id",
}

_PRESERVED_GLOBAL_ID_CHECKS: tuple[tuple[str, str], ...] = (
    ("facts", "fact_id"),
    ("relationships", "relationship_id"),
    ("rulings", "ruling_id"),
    ("sessions", "session_id"),
    ("scenes", "scene_id"),
    ("participant_principals", "principal_id"),
)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [canonical_json(dict(row)) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _insert_row(conn: sqlite3.Connection, table: str, row: Mapping[str, Any]) -> None:
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    conn.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
        tuple(row.values()),
    )


def _fetch_campaign_table(
    conn: sqlite3.Connection, table: str, campaign_id: str
) -> list[dict[str, Any]]:
    if table == "campaigns":
        sql = "SELECT * FROM campaigns WHERE campaign_id = ?"
        order = ""
    elif table == "entities":
        sql = (
            "SELECT * FROM entities WHERE owner_scope = 'campaign' "
            "AND campaign_id = ?"
        )
        order = " ORDER BY entity_id"
    elif table == "facts":
        sql = (
            "SELECT * FROM facts WHERE fact_scope = 'campaign' AND campaign_id = ?"
        )
        order = " ORDER BY fact_id"
    elif table == "relationships":
        sql = (
            "SELECT * FROM relationships WHERE owner_scope = 'campaign' "
            "AND campaign_id = ?"
        )
        order = " ORDER BY relationship_id"
    else:
        sql = f"SELECT * FROM {table} WHERE campaign_id = ?"
        order = " ORDER BY rowid"
    return [dict(row) for row in conn.execute(sql + order, (campaign_id,)).fetchall()]


def _compute_setting_digest(
    setting_row: Mapping[str, Any] | None,
    entities: list[Mapping[str, Any]],
    facts: list[Mapping[str, Any]],
    setting_events: list[Mapping[str, Any]],
) -> str:
    return sha256_text(
        canonical_json(
            {
                "setting": setting_row,
                "entities": entities,
                "facts": facts,
                "setting_events": setting_events,
            }
        )
    )


def export_campaign(
    conn: sqlite3.Connection,
    campaign_id: str,
    output_dir: Path,
    *,
    migrations_dir: Path,
) -> dict[str, Any]:
    store = CampaignStore(conn)
    campaign = store.get_campaign(campaign_id)
    if campaign is None:
        raise PackageError(f"campaign {campaign_id!r} not found")
    root = output_dir.resolve()
    if root.exists() and any(root.iterdir()):
        raise PackageError(f"export directory {root} is not empty")
    root.mkdir(parents=True, exist_ok=True)
    tables_dir = root / "tables"
    tables_dir.mkdir(exist_ok=True)

    for table in CAMPAIGN_EXPORT_TABLES:
        if table == "participant_principals":
            rows = _fetch_campaign_table(conn, table, campaign_id)
        else:
            rows = _fetch_campaign_table(conn, table, campaign_id)
        _write_jsonl(tables_dir / f"{table}.jsonl", rows)

    events = []
    for event in EventStore(conn).read(campaign_id):
        events.append(
            {
                "campaign_id": event.campaign_id,
                "sequence": event.sequence,
                "event_type": event.event_type,
                "payload": event.payload,
                "event_schema_version": event.event_schema_version,
                "session_id": event.session_id,
                "scene_id": event.scene_id,
                "occurred_at": event.occurred_at,
            }
        )
    _write_jsonl(root / "events.jsonl", events)

    setting_id = campaign.get("setting_id")
    setting_digest = None
    if setting_id:
        setting_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM settings WHERE setting_id = ?", (setting_id,)
            ).fetchall()
        ]
        setting_entities = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM entities WHERE owner_scope = 'setting' "
                "AND setting_id = ? ORDER BY entity_id",
                (setting_id,),
            ).fetchall()
        ]
        setting_facts = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM facts WHERE fact_scope = 'setting' "
                "AND setting_id = ? ORDER BY fact_id",
                (setting_id,),
            ).fetchall()
        ]
        setting_events = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM setting_events WHERE setting_id = ? ORDER BY sequence",
                (setting_id,),
            ).fetchall()
        ]
        _write_jsonl(tables_dir / "settings.jsonl", setting_rows)
        _write_jsonl(tables_dir / "setting_entities.jsonl", setting_entities)
        _write_jsonl(tables_dir / "setting_facts.jsonl", setting_facts)
        _write_jsonl(root / "setting-events.jsonl", setting_events)
        setting_digest = _compute_setting_digest(
            setting_rows[0] if setting_rows else None,
            setting_entities,
            setting_facts,
            setting_events,
        )

    migrations = [
        {"filename": path.name, "checksum": sha256_bytes(path.read_bytes())}
        for path in sorted(migrations_dir.glob("*.sql"))
    ]

    manifest: dict[str, Any] = {
        "format": FORMAT,
        "schema_migrations": migrations,
        "event_schema_version": CURRENT_EVENT_SCHEMA_VERSION,
        "system_id": campaign["system_id"],
        "system_version": campaign.get("system_version"),
        "api_version": "tabletop/v1",
        "content_pack_ids": [],
        "document_hashes": {},
        "campaign_id": campaign_id,
        "setting_id": setting_id,
        "setting_digest": setting_digest,
    }
    file_hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            file_hashes[path.relative_to(root).as_posix()] = sha256_bytes(
                path.read_bytes()
            )
    manifest["package_digest"] = compute_package_digest(manifest, file_hashes)
    (root / "manifest.json").write_text(
        canonical_json(manifest) + "\n", encoding="utf-8"
    )
    return manifest


def _destination_migration_checksums(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        row["filename"]: row["checksum"]
        for row in conn.execute(
            "SELECT filename, checksum FROM schema_migrations"
        ).fetchall()
    }


def _check_package_schema(
    conn: sqlite3.Connection, manifest: Mapping[str, Any]
) -> None:
    package_event_version = int(manifest.get("event_schema_version") or 1)
    if package_event_version > CURRENT_EVENT_SCHEMA_VERSION:
        raise PackageError("event_schema_too_new")
    destination = _destination_migration_checksums(conn)
    for entry in manifest.get("schema_migrations") or []:
        filename = entry["filename"]
        checksum = entry["checksum"]
        if filename not in destination:
            raise PackageError("package_schema_too_new")
        if destination[filename] != checksum:
            raise PackageError(
                f"migration checksum mismatch for {filename}"
            )


def _campaign_has_authoritative_state(
    conn: sqlite3.Connection, campaign_id: str
) -> bool:
    checks = (
        "SELECT COUNT(*) FROM entities WHERE campaign_id = ?",
        "SELECT COUNT(*) FROM facts WHERE campaign_id = ?",
        "SELECT COUNT(*) FROM relationships WHERE campaign_id = ?",
        "SELECT COUNT(*) FROM sessions WHERE campaign_id = ?",
        "SELECT COUNT(*) FROM scenes WHERE campaign_id = ?",
        "SELECT COUNT(*) FROM rulings WHERE campaign_id = ?",
        "SELECT COUNT(*) FROM participants WHERE campaign_id = ?",
        "SELECT COUNT(*) FROM character_controls WHERE campaign_id = ?",
        "SELECT COUNT(*) FROM participant_principals WHERE campaign_id = ?",
        "SELECT COUNT(*) FROM events WHERE campaign_id = ?",
    )
    for sql in checks:
        if conn.execute(sql, (campaign_id,)).fetchone()[0]:
            return True
    return False


def _assert_no_global_id_collisions(
    conn: sqlite3.Connection,
    package_dir: Path,
    campaign_id: str,
) -> None:
    root = package_dir.resolve()
    for table, column in _PRESERVED_GLOBAL_ID_CHECKS:
        path = root / "tables" / f"{table}.jsonl"
        for row in _read_jsonl(path):
            value = row.get(column)
            if value is None:
                continue
            existing = conn.execute(
                f"SELECT campaign_id FROM {table} WHERE {column} = ?",
                (value,),
            ).fetchone()
            if existing is None:
                continue
            owner = existing["campaign_id"] if "campaign_id" in existing.keys() else None
            if owner is not None and owner != campaign_id:
                raise PackageError(
                    f"preserved id {column}={value!r} belongs to another campaign"
                )
            if owner is None:
                # facts/relationships may be setting-scoped without campaign_id
                raise PackageError(
                    f"preserved id {column}={value!r} already exists"
                )


def _restore_setting(
    conn: sqlite3.Connection, root: Path, manifest: Mapping[str, Any]
) -> None:
    setting_id = manifest.get("setting_id")
    if not setting_id:
        return
    package_digest = manifest.get("setting_digest")
    existing = conn.execute(
        "SELECT * FROM settings WHERE setting_id = ?", (setting_id,)
    ).fetchone()
    setting_rows = _read_jsonl(root / "tables" / "settings.jsonl")
    setting_entities = _read_jsonl(root / "tables" / "setting_entities.jsonl")
    setting_facts = _read_jsonl(root / "tables" / "setting_facts.jsonl")
    setting_events = _read_jsonl(root / "setting-events.jsonl")
    if existing is not None:
        dest_entities = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM entities WHERE owner_scope = 'setting' "
                "AND setting_id = ? ORDER BY entity_id",
                (setting_id,),
            ).fetchall()
        ]
        dest_facts = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM facts WHERE fact_scope = 'setting' "
                "AND setting_id = ? ORDER BY fact_id",
                (setting_id,),
            ).fetchall()
        ]
        dest_events = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM setting_events WHERE setting_id = ? ORDER BY sequence",
                (setting_id,),
            ).fetchall()
        ]
        dest_digest = _compute_setting_digest(
            dict(existing), dest_entities, dest_facts, dest_events
        )
        if dest_digest != package_digest:
            raise PackageError(
                f"setting {setting_id!r} digest differs from package"
            )
        return
    for row in setting_rows:
        _insert_row(conn, "settings", row)
    for row in setting_entities:
        _insert_row(conn, "entities", row)
    for row in setting_facts:
        _insert_row(conn, "facts", row)
    for row in setting_events:
        _insert_row(conn, "setting_events", row)


def restore_package(conn: sqlite3.Connection, package_dir: Path) -> dict[str, Any]:
    manifest = validate_package_directory(package_dir)
    root = package_dir.resolve()
    campaign_id = manifest["campaign_id"]
    _check_package_schema(conn, manifest)

    existing = CampaignStore(conn).get_campaign(campaign_id)
    if existing is not None and _campaign_has_authoritative_state(conn, campaign_id):
        raise PackageError(
            f"campaign {campaign_id!r} already has authoritative state"
        )

    _assert_no_global_id_collisions(conn, root, campaign_id)

    with transaction(conn):
        if existing is not None:
            conn.execute("DELETE FROM campaigns WHERE campaign_id = ?", (campaign_id,))
        # Setting first: campaign facts may reference setting_id.
        _restore_setting(conn, root, manifest)
        for table in CAMPAIGN_EXPORT_TABLES:
            for row in _read_jsonl(root / "tables" / f"{table}.jsonl"):
                _insert_row(conn, table, row)
        for row in _read_jsonl(root / "events.jsonl"):
            conn.execute(
                "INSERT INTO events "
                "(campaign_id, sequence, event_type, payload, "
                "event_schema_version, session_id, scene_id, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["campaign_id"],
                    row["sequence"],
                    row["event_type"],
                    canonical_json(row["payload"]),
                    row.get("event_schema_version", 1),
                    row.get("session_id"),
                    row.get("scene_id"),
                    row.get("occurred_at"),
                ),
            )
    return dict(manifest)


def _new_id() -> str:
    return str(uuid.uuid4())


def _remap_fork_rows(
    table: str,
    rows: list[dict[str, Any]],
    new_campaign_id: str,
    id_maps: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    policy = FORK_IDENTITY_POLICY[table]
    if policy == "omit":
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        row = dict(row)
        if "campaign_id" in row:
            row["campaign_id"] = new_campaign_id
        if policy == "regenerate":
            column = _REGENERATE_ID_COLUMNS[table]
            old = row[column]
            new = _new_id()
            id_maps.setdefault(column, {})[old] = new
            row[column] = new
        out.append(row)
    return out


def _rewrite_fork_references(
    rows_by_table: dict[str, list[dict[str, Any]]],
    id_maps: dict[str, dict[str, str]],
) -> None:
    session_map = id_maps.get("session_id", {})
    scene_map = id_maps.get("scene_id", {})
    ruling_map = id_maps.get("ruling_id", {})
    for row in rows_by_table.get("scenes", []):
        if row.get("session_id") in session_map:
            row["session_id"] = session_map[row["session_id"]]
    for row in rows_by_table.get("rulings", []):
        if row.get("session_id") in session_map:
            row["session_id"] = session_map[row["session_id"]]
        if row.get("supersedes") in ruling_map:
            row["supersedes"] = ruling_map[row["supersedes"]]
    # No fact/relationship FK rewrites among regenerated ids required for subject_id
    # (those reference preserved entity_ids).


def fork_package(
    conn: sqlite3.Connection,
    package_dir: Path,
    new_campaign_id: str,
) -> dict[str, Any]:
    if not _CAMPAIGN_ID_PATTERN.fullmatch(new_campaign_id):
        raise PackageError(f"invalid campaign id {new_campaign_id!r}")
    if CampaignStore(conn).get_campaign(new_campaign_id) is not None:
        raise PackageError(f"campaign {new_campaign_id!r} already exists")
    missing = set(CAMPAIGN_EXPORT_TABLES) - set(FORK_IDENTITY_POLICY)
    if missing:
        raise PackageError(
            f"fork identity policy missing tables: {sorted(missing)}"
        )
    manifest = validate_package_directory(package_dir)
    _check_package_schema(conn, manifest)
    root = package_dir.resolve()
    id_maps: dict[str, dict[str, str]] = {}
    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    for table in CAMPAIGN_EXPORT_TABLES:
        rows = _read_jsonl(root / "tables" / f"{table}.jsonl")
        rows_by_table[table] = _remap_fork_rows(
            table, rows, new_campaign_id, id_maps
        )
    _rewrite_fork_references(rows_by_table, id_maps)

    with transaction(conn):
        _restore_setting(conn, root, manifest)
        insert_order = (
            "campaigns",
            "entities",
            "participants",
            "facts",
            "relationships",
            "sessions",
            "scenes",
            "rulings",
            "character_controls",
        )
        for table in insert_order:
            for row in rows_by_table.get(table, []):
                _insert_row(conn, table, row)
        EventStore(conn).append_in_transaction(
            conn,
            new_campaign_id,
            GameEvent(
                event_type=EventType.CAMPAIGN_FORKED.value,
                payload={
                    "forked_from_campaign_id": manifest["campaign_id"],
                    "setting_id": manifest.get("setting_id"),
                    "package_digest": manifest.get("package_digest"),
                },
            ),
        )
    return {"campaign_id": new_campaign_id, "forked_from": manifest["campaign_id"]}
