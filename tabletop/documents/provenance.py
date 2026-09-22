"""Provenance tracking and source-owned fact removal."""

from __future__ import annotations

import sqlite3

from tabletop.api.events import GameEvent
from tabletop.campaign.event_store import EventStore, EventType
from tabletop.storage.sqlite import transaction


def purge_facts_for_document_in_transaction(
    conn: sqlite3.Connection,
    document_id: str,
) -> list[str]:
    """Remove attached facts for a document inside the caller's transaction."""

    rows = conn.execute(
        "SELECT fact_id, campaign_id FROM facts "
        "WHERE source_document_id = ? AND source_ownership = 'attached' "
        "ORDER BY fact_id",
        (document_id,),
    ).fetchall()
    removed_ids = [row["fact_id"] for row in rows]
    if not removed_ids:
        return []

    conn.execute(
        "DELETE FROM facts "
        "WHERE source_document_id = ? AND source_ownership = 'attached'",
        (document_id,),
    )

    ids_by_campaign: dict[str, list[str]] = {}
    for row in rows:
        campaign_id = row["campaign_id"]
        if campaign_id is not None:
            ids_by_campaign.setdefault(campaign_id, []).append(row["fact_id"])

    event_store = EventStore(conn)
    for campaign_id, fact_ids in sorted(ids_by_campaign.items()):
        event_store.append_in_transaction(
            conn,
            campaign_id,
            GameEvent(
                event_type=EventType.PROVENANCE_PURGED.value,
                payload={"fact_ids": fact_ids},
            ),
        )

    return removed_ids


def purge_facts_for_document(
    conn: sqlite3.Connection,
    document_id: str,
) -> list[str]:
    """Remove attached facts for a document in one transaction."""

    with transaction(conn):
        return purge_facts_for_document_in_transaction(conn, document_id)
