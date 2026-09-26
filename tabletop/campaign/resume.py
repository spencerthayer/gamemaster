"""Read-only campaign resume snapshot for operators."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from tabletop.campaign.store import CampaignStore
from tabletop.importing.store import derived_batch_status

from tabletop.api.visibility import gm_viewpoint
from tabletop.campaign.scene_snapshot import build_scene_snapshot


def resume_snapshot(conn: sqlite3.Connection, campaign_id: str) -> dict[str, Any]:
    campaign = CampaignStore(conn).get_campaign(campaign_id)
    if campaign is None:
        raise LookupError(f"campaign {campaign_id!r} not found")


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

    scene_snapshot = build_scene_snapshot(
        conn, campaign_id, viewpoint=gm_viewpoint()
    )
    scene = scene_snapshot["scene"]
    clock = scene_snapshot["game_time"]

    return {
        "campaign_id": campaign_id,
        "name": campaign.get("name"),
        "system_id": campaign.get("system_id"),
        "latest_session": scene_snapshot["session"],
        "in_world_date": None if clock is None else clock["in_world_label"],
        "scene": scene,
        "open_scene_id": scene_snapshot["open_scene_id"],
        "present_entities": scene_snapshot["present_entities"],
        "game_time": clock,
        "recent_events": scene_snapshot["recent_events"],
        "active_rulings": scene_snapshot["active_rulings"],
        "authoritative_contradictions": scene_snapshot["pending_contradictions"],
        "pending_imports": pending_imports,
        "pending_import_conflicts": pending_import_conflicts,
        "pending_import_batches": batches_pending,
    }
