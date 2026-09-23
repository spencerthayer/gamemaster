"""Read-only campaign resume snapshot for operators."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from tabletop.campaign.store import CampaignStore
from tabletop.importing.store import derived_batch_status


def resume_snapshot(conn: sqlite3.Connection, campaign_id: str) -> dict[str, Any]:
    campaign = CampaignStore(conn).get_campaign(campaign_id)
    if campaign is None:
        raise LookupError(f"campaign {campaign_id!r} not found")

    session = conn.execute(
        "SELECT session_id, started_at, ended_at FROM sessions "
        "WHERE campaign_id = ? ORDER BY started_at DESC LIMIT 1",
        (campaign_id,),
    ).fetchone()

    unresolved = int(
        conn.execute(
            "SELECT COUNT(*) FROM events "
            "WHERE campaign_id = ? AND event_type = ?",
            (campaign_id, "canon.contradiction_detected"),
        ).fetchone()[0]
    )

    pending_imports = 0
    pending_import_conflicts = 0
    try:
        items = conn.execute(
            "SELECT review_state, review_json FROM import_items "
            "WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        items = []
    for item in items:
        if item["review_state"] == "pending_review":
            pending_imports += 1
            review = json.loads(item["review_json"] or "{}")
            if review.get("conflicts"):
                pending_import_conflicts += 1

    batches_pending = 0
    try:
        batches = conn.execute(
            "SELECT import_id FROM import_batches WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        batches = []
    for row in batches:
        if derived_batch_status(conn, row["import_id"]) in {
            "pending_review",
            "partially_applied",
        }:
            batches_pending += 1

    return {
        "campaign_id": campaign_id,
        "name": campaign.get("name"),
        "system_id": campaign.get("system_id"),
        "latest_session": dict(session) if session is not None else None,
        "in_world_date": "unknown",
        "scene": "unknown",
        "authoritative_contradictions": unresolved,
        "pending_imports": pending_imports,
        "pending_import_conflicts": pending_import_conflicts,
        "pending_import_batches": batches_pending,
    }
