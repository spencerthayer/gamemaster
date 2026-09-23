"""Reviewed apply for staged import items."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from tabletop.campaign.contradictions import Claim, detect_claim_conflict
from tabletop.campaign.event_store import promote_fact_in_transaction
from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState
from tabletop.campaign.store import CampaignStore
from tabletop.documents.extraction import (
    ProposedExtraction,
    ProposedFact,
)
from tabletop.documents.importer import import_extraction_in_transaction
from tabletop.export.manifest import canonical_json
from tabletop.storage.sqlite import transaction

ErrorPayload = dict[str, Any]


class ApplyBlockedError(RuntimeError):
    """Apply requires operator resolution before authoritative writes."""

    def __init__(self, message: str, *, review: dict[str, Any]) -> None:
        super().__init__(message)
        self.review = review


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_item(conn: sqlite3.Connection, item_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM import_items WHERE item_id = ?", (item_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"import item {item_id!r} not found")
    return dict(row)


def _record_staging_error(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    error: ErrorPayload,
    review: dict[str, Any] | None = None,
) -> None:
    payload = canonical_json(dict(error))
    with transaction(conn):
        if review is None:
            conn.execute(
                "UPDATE import_items SET error_json = ? WHERE item_id = ?",
                (payload, item_id),
            )
        else:
            conn.execute(
                "UPDATE import_items SET error_json = ?, review_json = ? "
                "WHERE item_id = ?",
                (payload, canonical_json(review), item_id),
            )


def _ensure_provenance(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    campaign_id: str,
    provenance: dict[str, Any],
    value: str,
) -> tuple[str, str, str]:
    """Return (job_id, document_id, chunk_id), creating rows if needed."""

    source_label = str(
        provenance.get("source_path")
        or provenance.get("source_label")
        or f"import-item:{item_id}"
    )
    content = value.encode("utf-8")
    content_hash = hashlib.sha256(content).hexdigest()
    job_id = f"import-job-{item_id}"
    document_id = f"import-doc-{item_id}"
    chunk_id = f"import-chunk-{item_id}"
    now = _utc_now()

    existing_job = conn.execute(
        "SELECT job_id FROM ingest_jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    if existing_job is None:
        try:
            conn.execute(
                "INSERT INTO ingest_jobs "
                "(job_id, document_hash, parser_version, slice_strategy_version, "
                "status, total_slices, completed_slices, failed_slices, "
                "estimated_cost, actual_cost, started_at, updated_at) "
                "VALUES (?, ?, '1', '1', 'completed', 1, 1, 0, NULL, NULL, ?, ?)",
                (job_id, content_hash, now, now),
            )
        except sqlite3.IntegrityError:
            # Content-addressed uniqueness: reuse the existing job for this hash.
            row = conn.execute(
                "SELECT job_id FROM ingest_jobs WHERE document_hash = ?",
                (content_hash,),
            ).fetchone()
            if row is None:
                raise
            job_id = row["job_id"]

    existing_doc = conn.execute(
        "SELECT document_id FROM documents WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    if existing_doc is None:
        try:
            conn.execute(
                "INSERT INTO documents "
                "(document_id, content_hash, source_path, title, document_shape, "
                "content_pack_id, system_id, visibility, ingested_at) "
                "VALUES (?, ?, ?, ?, 'prose', NULL, NULL, 'GM', ?)",
                (document_id, content_hash, source_label, source_label, now),
            )
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT document_id FROM documents WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
            if row is None:
                raise
            document_id = row["document_id"]
            chunk_row = conn.execute(
                "SELECT chunk_id FROM document_chunks WHERE document_id = ? "
                "ORDER BY ordinal LIMIT 1",
                (document_id,),
            ).fetchone()
            if chunk_row is not None:
                return job_id, document_id, chunk_row["chunk_id"]

    existing_chunk = conn.execute(
        "SELECT chunk_id FROM document_chunks WHERE chunk_id = ?",
        (chunk_id,),
    ).fetchone()
    if existing_chunk is None:
        conn.execute(
            "INSERT INTO document_chunks "
            "(chunk_id, document_id, ordinal, heading_path, page, text, "
            "content_hash, content_pack_id, system_id, visibility) "
            "VALUES (?, ?, 0, '[]', NULL, ?, ?, NULL, NULL, 'GM')",
            (chunk_id, document_id, value, content_hash),
        )
    return job_id, document_id, chunk_id


def _apply_fact_in_transaction(
    conn: sqlite3.Connection,
    *,
    item: dict[str, Any],
    payload: dict[str, Any],
) -> str:
    campaign_id = item["campaign_id"]
    fact_id = str(payload.get("fact_id") or item["item_id"])
    subject_id = str(payload.get("subject_id") or "")
    predicate = str(payload.get("predicate") or "")
    value = payload.get("value")
    value_text = value if isinstance(value, str) else canonical_json(value)
    claim = Claim(
        campaign_id=campaign_id,
        subject_id=subject_id,
        predicate=predicate,
        value=value_text,
    )
    conflict = detect_claim_conflict(conn, claim)
    if conflict is not None:
        raise ApplyBlockedError(
            "authoritative conflict requires operator resolution",
            review={
                "conflicts": [
                    {
                        "type": "apply_vs_canon",
                        "existing_fact_id": conflict.existing_fact.fact_id,
                        "reason": conflict.reason,
                    }
                ]
            },
        )

    provenance = json.loads(item["provenance_json"] or "{}")
    job_id, document_id, chunk_id = _ensure_provenance(
        conn,
        item_id=item["item_id"],
        campaign_id=campaign_id,
        provenance=provenance,
        value=value_text,
    )
    extraction = ProposedExtraction(
        job_id=job_id,
        slice_index=0,
        extractor_version="extractor-1",
        facts=(
            ProposedFact(
                fact_id=fact_id,
                fact_scope="campaign",
                setting_id=None,
                campaign_id=campaign_id,
                subject_id=subject_id or None,
                predicate=predicate,
                value=value,
                visibility=str(payload.get("visibility") or "GM"),
                source_document_id=document_id,
                source_chunk_id=chunk_id,
            ),
        ),
    )
    report = import_extraction_in_transaction(conn, extraction)
    if fact_id not in report.accepted_ids:
        reasons = ", ".join(
            f"{item.proposal_id}:{item.reason}" for item in report.rejected
        )
        raise ValueError(f"import_extraction rejected fact {fact_id}: {reasons}")
    row = conn.execute(
        "SELECT fact_id, fact_scope, setting_id, campaign_id, subject_id, "
        "predicate, value, canon_state, knowledge_state, visibility, "
        "valid_from, valid_until, source_document_id, source_chunk_id, "
        "import_job_id, extraction_method, source_ownership, created_at "
        "FROM facts WHERE fact_id = ?",
        (fact_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"fact {fact_id!r} missing after import_extraction")
    fact = Fact(
        fact_id=row["fact_id"],
        fact_scope=FactScope(row["fact_scope"]),
        setting_id=row["setting_id"],
        campaign_id=row["campaign_id"],
        subject_id=row["subject_id"],
        predicate=row["predicate"],
        value=row["value"],
        canon_state=CanonState(row["canon_state"]),
        knowledge_state=KnowledgeState(row["knowledge_state"]),
        visibility=row["visibility"],
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        source_document_id=row["source_document_id"],
        source_chunk_id=row["source_chunk_id"],
        import_job_id=row["import_job_id"],
        extraction_method=row["extraction_method"],
        source_ownership=row["source_ownership"],
        created_at=row["created_at"],
    )
    promote_fact_in_transaction(conn, fact)
    return fact_id


def apply_item(conn: sqlite3.Connection, item_id: str) -> dict[str, Any]:
    item = _load_item(conn, item_id)
    if item["review_state"] == "applied":
        return {
            "status": "already_applied",
            "item_id": item_id,
            "applied_target_id": item.get("applied_target_id"),
        }
    if item["review_state"] != "pending_review":
        raise ValueError(f"item {item_id!r} is {item['review_state']}")

    payload = json.loads(item["payload_json"])
    campaign_id = item["campaign_id"]
    kind = item["kind"]
    target_id: str | None = None

    try:
        with transaction(conn):
            if kind == "entity":
                CampaignStore(conn).upsert_entity_in_transaction(
                    campaign_id,
                    payload["entity_id"],
                    payload["name"],
                    entity_type=payload.get("entity_type"),
                    system_state=payload.get("system_state"),
                )
                target_id = payload["entity_id"]
            elif kind == "participant":
                MembershipStore(conn).add_participant_in_transaction(
                    campaign_id,
                    payload["participant_id"],
                    payload["display_name"],
                    payload.get("role", "player"),
                )
                target_id = payload["participant_id"]
            elif kind == "control":
                target_id = MembershipStore(conn).grant_control_in_transaction(
                    campaign_id,
                    payload["participant_id"],
                    payload["entity_id"],
                    payload.get("control", "owner"),
                    control_id=payload.get("control_id"),
                )
            elif kind == "fact":
                target_id = _apply_fact_in_transaction(
                    conn, item=item, payload=payload
                )
            else:
                raise ValueError(f"unsupported import kind {kind!r}")
            conn.execute(
                "UPDATE import_items SET review_state = 'applied', "
                "applied_target_id = ?, error_json = '{}' WHERE item_id = ?",
                (target_id, item_id),
            )
    except ApplyBlockedError as exc:
        _record_staging_error(
            conn,
            item_id,
            error={"code": "apply_blocked", "message": str(exc)},
            review=exc.review,
        )
        raise
    except Exception as exc:
        _record_staging_error(
            conn,
            item_id,
            error={"code": "apply_failed", "message": str(exc)},
        )
        raise

    return {"status": "applied", "item_id": item_id, "applied_target_id": target_id}


def review_item(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    reject: bool = False,
    mark_unapplyable: bool = False,
) -> None:
    if reject == mark_unapplyable:
        raise ValueError("pass exactly one of reject or mark_unapplyable")
    state = "rejected" if reject else "unapplyable"
    with transaction(conn):
        cursor = conn.execute(
            "UPDATE import_items SET review_state = ? "
            "WHERE item_id = ? AND review_state = 'pending_review'",
            (state, item_id),
        )
        if cursor.rowcount != 1:
            current = _load_item(conn, item_id)
            if current["review_state"] == state:
                return
            raise LookupError(f"item {item_id!r} cannot be reviewed")
