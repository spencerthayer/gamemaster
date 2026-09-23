"""Persist import batches without mutating campaign-authoritative tables."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from tabletop.export.manifest import canonical_json, sha256_text
from tabletop.importing.interface import ImportBatch
from tabletop.storage.sqlite import transaction

_AUTHORITATIVE_TABLES = (
    "campaigns",
    "sessions",
    "scenes",
    "entities",
    "facts",
    "relationships",
    "rulings",
    "participants",
    "participant_principals",
    "character_controls",
    "events",
    "settings",
    "setting_events",
)


def authoritative_state_digest(
    conn: sqlite3.Connection, campaign_id: str
) -> str:
    payload: dict[str, Any] = {}
    for table in _AUTHORITATIVE_TABLES:
        if table in {"settings", "setting_events"}:
            setting_id = conn.execute(
                "SELECT setting_id FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            if setting_id is None or setting_id[0] is None:
                payload[table] = []
                continue
            sid = setting_id[0]
            if table == "settings":
                rows = conn.execute(
                    "SELECT * FROM settings WHERE setting_id = ?", (sid,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM setting_events WHERE setting_id = ? "
                    "ORDER BY sequence",
                    (sid,),
                ).fetchall()
        elif table == "entities":
            rows = conn.execute(
                "SELECT * FROM entities WHERE campaign_id = ? ORDER BY entity_id",
                (campaign_id,),
            ).fetchall()
        elif table == "facts":
            rows = conn.execute(
                "SELECT * FROM facts WHERE campaign_id = ? ORDER BY fact_id",
                (campaign_id,),
            ).fetchall()
        elif table == "events":
            rows = conn.execute(
                "SELECT * FROM events WHERE campaign_id = ? ORDER BY sequence",
                (campaign_id,),
            ).fetchall()
        else:
            try:
                rows = conn.execute(
                    f"SELECT * FROM {table} WHERE campaign_id = ? ORDER BY rowid",
                    (campaign_id,),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        payload[table] = [dict(row) for row in rows]
    return sha256_text(canonical_json(payload))


def derived_batch_status(conn: sqlite3.Connection, import_id: str) -> str:
    rows = conn.execute(
        "SELECT review_state, COUNT(*) AS n FROM import_items "
        "WHERE import_id = ? GROUP BY review_state",
        (import_id,),
    ).fetchall()
    counts = {row["review_state"]: int(row["n"]) for row in rows}
    total = sum(counts.values())
    if total == 0:
        return "pending_review"
    if counts.get("pending_review", 0) == total:
        return "pending_review"
    if counts.get("applied", 0) == total:
        return "complete"
    if counts.get("pending_review", 0):
        return "partially_applied"
    return "complete"


def item_state_counts(conn: sqlite3.Connection, import_id: str) -> dict[str, int]:
    rows = conn.execute(
        "SELECT review_state, COUNT(*) AS n FROM import_items "
        "WHERE import_id = ? GROUP BY review_state",
        (import_id,),
    ).fetchall()
    counts = {row["review_state"]: int(row["n"]) for row in rows}
    return {
        "pending_review": counts.get("pending_review", 0),
        "applied": counts.get("applied", 0),
        "rejected": counts.get("rejected", 0),
        "unapplyable": counts.get("unapplyable", 0),
    }


def import_status_report(conn: sqlite3.Connection, import_id: str) -> dict[str, Any]:
    items = ImportStore(conn).list_items(import_id)
    kind_counts: dict[str, int] = {}
    staged_conflicts = 0
    for item in items:
        kind_counts[item["kind"]] = kind_counts.get(item["kind"], 0) + 1
        review = json.loads(item.get("review_json") or "{}")
        conflicts = review.get("conflicts") or []
        if conflicts:
            staged_conflicts += 1
    campaign_id = None
    batch = conn.execute(
        "SELECT campaign_id FROM import_batches WHERE import_id = ?",
        (import_id,),
    ).fetchone()
    if batch is not None:
        campaign_id = batch["campaign_id"]
    unresolved = 0
    if campaign_id is not None:
        unresolved = int(
            conn.execute(
                "SELECT COUNT(*) FROM events "
                "WHERE campaign_id = ? AND event_type = ?",
                (campaign_id, "canon.contradiction_detected"),
            ).fetchone()[0]
        )
    return {
        "import_id": import_id,
        "status": derived_batch_status(conn, import_id),
        "counts": item_state_counts(conn, import_id),
        "kinds": kind_counts,
        "staging": {"conflicts": staged_conflicts},
        "campaign": {"unresolved_contradictions": unresolved},
    }


class ImportStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def stage_batch(
        self,
        campaign_id: str,
        batch: ImportBatch,
        *,
        import_id: str | None = None,
    ) -> str:
        if import_id is None:
            import_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO import_batches "
                "(import_id, campaign_id, format_id, source_label, created_at, report_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    import_id,
                    campaign_id,
                    batch.format_id,
                    batch.source_label,
                    created_at,
                    canonical_json(dict(batch.report)),
                ),
            )
            for item in batch.items:
                self.conn.execute(
                    "INSERT INTO import_items "
                    "(item_id, import_id, campaign_id, kind, proposed_key, "
                    "payload_json, provenance_json, review_json, review_state, "
                    "error_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending_review', '{}', ?)",
                    (
                        str(uuid.uuid4()),
                        import_id,
                        campaign_id,
                        item.kind,
                        item.proposed_key,
                        canonical_json(dict(item.payload)),
                        canonical_json(dict(item.provenance)),
                        canonical_json(dict(item.review)),
                        created_at,
                    ),
                )
        return import_id

    def list_items(self, import_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM import_items WHERE import_id = ? ORDER BY created_at, item_id",
            (import_id,),
        ).fetchall()
        return [dict(row) for row in rows]
