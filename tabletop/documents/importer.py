"""Deterministic import gate for untrusted extraction proposals."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from tabletop.campaign.invariants import check_fact_invariants
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState
from tabletop.documents.extraction import (
    InvalidProposedExtractionError,
    KNOWN_EXTRACTOR_VERSIONS,
    ProposedEntity,
    ProposedExtraction,
    ProposedFact,
)
from tabletop.storage.sqlite import transaction

_REFUSED_SUBJECT_PATTERN = re.compile(
    r"^(unknown|n/?a|none|null|placeholder|tbd)$",
    re.IGNORECASE,
)
_FRAGMENT_NAME_PATTERN = re.compile(r"(\.\.\.|…)$")
_ABSURD_NAME_PATTERN = re.compile(r"^\?+$")


class ImportEnvelopeError(ValueError):
    """Raised when an extraction envelope is invalid and nothing may be imported."""


@dataclass(frozen=True, kw_only=True)
class RejectedProposal:
    """One proposal rejected by deterministic import policy."""

    proposal_id: str
    reason: str


@dataclass(frozen=True, kw_only=True)
class ImportReport:
    """Accepted ids and per-proposal rejection reasons for one import."""

    accepted_ids: tuple[str, ...]
    rejected: tuple[RejectedProposal, ...]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_extraction(
    extraction: ProposedExtraction | Mapping[str, Any],
) -> ProposedExtraction:
    if isinstance(extraction, ProposedExtraction):
        return extraction
    if isinstance(extraction, Mapping):
        try:
            return ProposedExtraction.from_dict(extraction)
        except InvalidProposedExtractionError as exc:
            raise ImportEnvelopeError(str(exc)) from exc
    raise ImportEnvelopeError(
        f"extraction must be ProposedExtraction or mapping, got {type(extraction).__name__}"
    )


def _validate_envelope(conn: sqlite3.Connection, extraction: ProposedExtraction) -> None:
    if extraction.extractor_version not in KNOWN_EXTRACTOR_VERSIONS:
        raise ImportEnvelopeError(
            f"unknown extractor_version: {extraction.extractor_version!r}"
        )
    row = conn.execute(
        "SELECT 1 FROM ingest_jobs WHERE job_id = ?",
        (extraction.job_id,),
    ).fetchone()
    if row is None:
        raise ImportEnvelopeError(f"unknown ingest job identity: {extraction.job_id!r}")


def _entity_rejection_reason(entity: ProposedEntity) -> str | None:
    if _FRAGMENT_NAME_PATTERN.search(entity.name):
        return "fragment name"
    if _ABSURD_NAME_PATTERN.fullmatch(entity.name):
        return "absurd entity"
    return None


def _fact_rejection_reason(fact: ProposedFact) -> str | None:
    if fact.subject_id is None:
        return "empty subject"
    if _REFUSED_SUBJECT_PATTERN.fullmatch(fact.subject_id):
        return "refused subject pattern"
    if fact.source_document_id is None or fact.source_chunk_id is None:
        return "missing provenance"
    return None


def _encode_fact_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _insert_entity(conn: sqlite3.Connection, entity: ProposedEntity) -> None:
    encoded_state = json.dumps(
        dict(entity.system_state), separators=(",", ":"), ensure_ascii=False
    )
    encoded_metadata = json.dumps(
        dict(entity.metadata), separators=(",", ":"), ensure_ascii=False
    )
    if entity.owner_scope == "campaign":
        cursor = conn.execute(
            "UPDATE entities SET overrides_id = ?, entity_type = ?, name = ?, "
            "system_state = ?, metadata = ? "
            "WHERE owner_scope = 'campaign' AND campaign_id = ? AND entity_id = ?",
            (
                entity.overrides_id,
                entity.entity_type,
                entity.name,
                encoded_state,
                encoded_metadata,
                entity.campaign_id,
                entity.entity_id,
            ),
        )
        if cursor.rowcount == 0:
            conn.execute(
                "INSERT INTO entities "
                "(entity_id, owner_scope, setting_id, campaign_id, overrides_id, "
                "entity_type, name, system_state, metadata) "
                "VALUES (?, 'campaign', ?, ?, ?, ?, ?, ?, ?)",
                (
                    entity.entity_id,
                    entity.setting_id,
                    entity.campaign_id,
                    entity.overrides_id,
                    entity.entity_type,
                    entity.name,
                    encoded_state,
                    encoded_metadata,
                ),
            )
        return

    cursor = conn.execute(
        "UPDATE entities SET overrides_id = ?, entity_type = ?, name = ?, "
        "system_state = ?, metadata = ? "
        "WHERE owner_scope = 'setting' AND setting_id = ? AND entity_id = ?",
        (
            entity.overrides_id,
            entity.entity_type,
            entity.name,
            encoded_state,
            encoded_metadata,
            entity.setting_id,
            entity.entity_id,
        ),
    )
    if cursor.rowcount == 0:
        conn.execute(
            "INSERT INTO entities "
            "(entity_id, owner_scope, setting_id, campaign_id, overrides_id, "
            "entity_type, name, system_state, metadata) "
            "VALUES (?, 'setting', ?, NULL, ?, ?, ?, ?, ?)",
            (
                entity.entity_id,
                entity.setting_id,
                entity.overrides_id,
                entity.entity_type,
                entity.name,
                encoded_state,
                encoded_metadata,
            ),
        )


def _insert_fact(
    conn: sqlite3.Connection,
    *,
    proposed: ProposedFact,
    import_job_id: str,
    extraction_method: str,
    created_at: str,
) -> None:
    fact = Fact(
        fact_id=proposed.fact_id,
        fact_scope=FactScope(proposed.fact_scope),
        setting_id=proposed.setting_id,
        campaign_id=proposed.campaign_id,
        subject_id=proposed.subject_id,
        predicate=proposed.predicate,
        value=_encode_fact_value(proposed.value),
        canon_state=CanonState.PROPOSED,
        knowledge_state=KnowledgeState.UNREVEALED,
        visibility=proposed.visibility,
        valid_from=proposed.valid_from,
        valid_until=proposed.valid_until,
        source_document_id=proposed.source_document_id,
        source_chunk_id=proposed.source_chunk_id,
        import_job_id=import_job_id,
        extraction_method=extraction_method,
        source_ownership="attached",
        created_at=created_at,
    )
    check_fact_invariants(fact)
    conn.execute(
        "INSERT INTO facts "
        "(fact_id, fact_scope, setting_id, campaign_id, subject_id, predicate, "
        "value, canon_state, knowledge_state, visibility, valid_from, "
        "valid_until, source_document_id, source_chunk_id, import_job_id, "
        "extraction_method, source_ownership, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            fact.fact_id,
            fact.fact_scope.value,
            fact.setting_id,
            fact.campaign_id,
            fact.subject_id,
            fact.predicate,
            fact.value,
            fact.canon_state.value,
            fact.knowledge_state.value,
            fact.visibility,
            fact.valid_from,
            fact.valid_until,
            fact.source_document_id,
            fact.source_chunk_id,
            fact.import_job_id,
            fact.extraction_method,
            fact.source_ownership,
            fact.created_at,
        ),
    )


def import_extraction(
    conn: sqlite3.Connection,
    extraction: ProposedExtraction | Mapping[str, Any],
) -> ImportReport:
    """Validate an extraction envelope, filter proposals, and insert survivors."""

    parsed = _coerce_extraction(extraction)
    _validate_envelope(conn, parsed)

    accepted: list[str] = []
    rejected: list[RejectedProposal] = []
    accepted_entities: list[ProposedEntity] = []
    accepted_facts: list[ProposedFact] = []

    for entity in parsed.entities:
        reason = _entity_rejection_reason(entity)
        if reason is not None:
            rejected.append(
                RejectedProposal(proposal_id=entity.entity_id, reason=reason)
            )
            continue
        accepted_entities.append(entity)
        accepted.append(entity.entity_id)

    for fact in parsed.facts:
        reason = _fact_rejection_reason(fact)
        if reason is not None:
            rejected.append(RejectedProposal(proposal_id=fact.fact_id, reason=reason))
            continue
        accepted_facts.append(fact)
        accepted.append(fact.fact_id)

    created_at = _utc_now()
    with transaction(conn):
        for entity in accepted_entities:
            _insert_entity(conn, entity)
        for fact in accepted_facts:
            _insert_fact(
                conn,
                proposed=fact,
                import_job_id=parsed.job_id,
                extraction_method=parsed.extractor_version,
                created_at=created_at,
            )

    return ImportReport(accepted_ids=tuple(accepted), rejected=tuple(rejected))
