"""Advisory contradiction detection for established canon."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from tabletop.campaign.event_store import record_contradiction
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState


@dataclass(frozen=True, slots=True, kw_only=True)
class Claim:
    """A proposed statement to compare with established campaign canon."""

    campaign_id: str
    subject_id: str
    predicate: str
    value: str
    session_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class FactSource:
    """Provenance attached to an existing fact."""

    document_id: str | None
    chunk_id: str | None
    import_job_id: str | None
    extraction_method: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ConflictCandidate:
    """A GM-only advisory that does not resolve or rewrite canon."""

    existing_fact: Fact
    source: FactSource
    session: str | None
    reason: str
    visibility: str = "GM"
    resolved: bool = False

    @property
    def session_id(self) -> str | None:
        """Expose the session value with its storage-oriented name."""

        return self.session


def check_claim(
    conn: sqlite3.Connection,
    claim: Claim,
) -> ConflictCandidate | None:
    """Return and record a predicate-level conflict for an established subject."""

    if not _subject_exists(conn, claim.campaign_id, claim.subject_id):
        return None

    rows = conn.execute(
        "SELECT f.fact_id, f.fact_scope, f.setting_id, f.campaign_id, "
        "f.subject_id, f.predicate, f.value, f.canon_state, f.knowledge_state, "
        "f.visibility, f.valid_from, f.valid_until, f.source_document_id, "
        "f.source_chunk_id, f.import_job_id, f.extraction_method, "
        "f.source_ownership, f.created_at "
        "FROM facts AS f "
        "JOIN campaigns AS c ON c.campaign_id = ? "
        "WHERE f.subject_id = ? AND f.predicate = ? "
        "AND f.canon_state = ? "
        "AND (f.campaign_id = c.campaign_id OR f.setting_id = c.setting_id) "
        "ORDER BY CASE WHEN f.campaign_id = c.campaign_id THEN 0 ELSE 1 END, "
        "f.created_at DESC, f.fact_id",
        (
            claim.campaign_id,
            claim.subject_id,
            claim.predicate,
            CanonState.CONFIRMED.value,
        ),
    ).fetchall()
    conflicting_row = next((row for row in rows if row["value"] != claim.value), None)
    if conflicting_row is None:
        return None

    existing_fact = _fact_from_row(conflicting_row)
    existing_session_id = _fact_session_id(
        conn, claim.campaign_id, existing_fact.fact_id
    )
    reason = (
        f"Established canon has {claim.predicate}={existing_fact.value!r}; "
        f"the claim proposes {claim.value!r}."
    )
    candidate = ConflictCandidate(
        existing_fact=existing_fact,
        source=FactSource(
            document_id=existing_fact.source_document_id,
            chunk_id=existing_fact.source_chunk_id,
            import_job_id=existing_fact.import_job_id,
            extraction_method=existing_fact.extraction_method,
        ),
        session=existing_session_id,
        reason=reason,
    )
    record_contradiction(
        conn,
        claim.campaign_id,
        {
            "claim": {
                "subject_id": claim.subject_id,
                "predicate": claim.predicate,
                "value": claim.value,
            },
            "existing_fact_id": existing_fact.fact_id,
            "existing_value": existing_fact.value,
            "reason": reason,
            "visibility": candidate.visibility,
            "resolved": candidate.resolved,
            "claim_session_id": claim.session_id,
            "existing_session_id": existing_session_id,
        },
    )
    return candidate


def _subject_exists(
    conn: sqlite3.Connection,
    campaign_id: str,
    subject_id: str,
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM entities AS e "
        "JOIN campaigns AS c ON c.campaign_id = ? "
        "WHERE e.entity_id = ? "
        "AND ((e.owner_scope = 'campaign' AND e.campaign_id = c.campaign_id) "
        "OR (e.owner_scope = 'setting' AND e.setting_id = c.setting_id)) "
        "LIMIT 1",
        (campaign_id, subject_id),
    ).fetchone()
    return row is not None


def _fact_session_id(
    conn: sqlite3.Connection,
    campaign_id: str,
    fact_id: str,
) -> str | None:
    rows = conn.execute(
        "SELECT session_id, payload FROM events "
        "WHERE campaign_id = ? AND session_id IS NOT NULL ORDER BY sequence",
        (campaign_id,),
    ).fetchall()
    for row in rows:
        payload = json.loads(row["payload"])
        if payload.get("fact_id") == fact_id:
            return row["session_id"]
    return None


def _fact_from_row(row: sqlite3.Row) -> Fact:
    return Fact(
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
