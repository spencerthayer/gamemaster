"""Resolve rule-reference transport values to persisted source identities."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from tabletop.api.rules import RuleReference


@dataclass(frozen=True)
class ResolvedRuleReference:
    """Exact persisted source version behind a rule reference."""

    source_id: str
    document_id: str
    document_path: str
    content_hash: str
    chunk_id: str | None
    ordinal: int | None
    ingest_job_id: str | None
    parser_version: str | None
    extractor_version: str | None

    @property
    def document_content_hash(self) -> str:
        """Explicit alias for callers that distinguish document and chunk hashes."""

        return self.content_hash

    @property
    def chunk_or_slice_ordinal(self) -> int | None:
        """The resolved chunk ordinal, or slice ordinal when represented as a chunk."""

        return self.ordinal


def _resolve_fact_reference(
    conn: sqlite3.Connection,
    ref: RuleReference,
) -> ResolvedRuleReference | None:
    row = conn.execute(
        "SELECT f.fact_id, d.document_id, d.source_path, d.content_hash, "
        "c.chunk_id, c.ordinal, f.import_job_id, j.parser_version, "
        "f.extraction_method "
        "FROM facts AS f "
        "LEFT JOIN documents AS d ON d.document_id = f.source_document_id "
        "LEFT JOIN document_chunks AS c "
        "ON c.chunk_id = f.source_chunk_id AND c.document_id = d.document_id "
        "LEFT JOIN ingest_jobs AS j ON j.job_id = f.import_job_id "
        "WHERE f.fact_id = ? "
        "AND (? IS NULL OR f.source_chunk_id = ?)",
        (ref.source_id, ref.chunk_id, ref.chunk_id),
    ).fetchone()
    if row is None or row["document_id"] is None:
        return None
    return ResolvedRuleReference(
        source_id=str(row["fact_id"]),
        document_id=str(row["document_id"]),
        document_path=str(row["source_path"]),
        content_hash=str(row["content_hash"]),
        chunk_id=None if row["chunk_id"] is None else str(row["chunk_id"]),
        ordinal=None if row["ordinal"] is None else int(row["ordinal"]),
        ingest_job_id=(
            None if row["import_job_id"] is None else str(row["import_job_id"])
        ),
        parser_version=(
            None if row["parser_version"] is None else str(row["parser_version"])
        ),
        extractor_version=(
            None
            if row["extraction_method"] is None
            else str(row["extraction_method"])
        ),
    )


def _resolve_document_reference(
    conn: sqlite3.Connection,
    ref: RuleReference,
) -> ResolvedRuleReference | None:
    row = conn.execute(
        "SELECT d.document_id, d.source_path, d.content_hash, "
        "c.chunk_id, c.ordinal, j.job_id, j.parser_version "
        "FROM documents AS d "
        "LEFT JOIN document_chunks AS c "
        "ON c.document_id = d.document_id "
        "AND ? IS NOT NULL AND c.chunk_id = ? "
        "LEFT JOIN ingest_jobs AS j ON j.document_hash = d.content_hash "
        "WHERE d.document_id = ? "
        "OR (? IS NOT NULL AND d.source_path = ?) "
        "OR (? IS NOT NULL AND c.chunk_id = ?) "
        "ORDER BY CASE "
        "WHEN d.document_id = ? THEN 0 "
        "WHEN d.source_path = ? THEN 1 "
        "ELSE 2 END, "
        "j.started_at DESC, j.job_id DESC "
        "LIMIT 1",
        (
            ref.chunk_id,
            ref.chunk_id,
            ref.source_id,
            ref.document_path,
            ref.document_path,
            ref.chunk_id,
            ref.chunk_id,
            ref.source_id,
            ref.document_path,
        ),
    ).fetchone()
    if row is None:
        return None
    if ref.chunk_id is not None and row["chunk_id"] is None:
        return None
    return ResolvedRuleReference(
        source_id=ref.source_id,
        document_id=str(row["document_id"]),
        document_path=str(row["source_path"]),
        content_hash=str(row["content_hash"]),
        chunk_id=None if row["chunk_id"] is None else str(row["chunk_id"]),
        ordinal=None if row["ordinal"] is None else int(row["ordinal"]),
        ingest_job_id=None if row["job_id"] is None else str(row["job_id"]),
        parser_version=(
            None if row["parser_version"] is None else str(row["parser_version"])
        ),
        extractor_version=None,
    )


def resolve_reference(
    conn: sqlite3.Connection,
    ref: RuleReference,
) -> ResolvedRuleReference | None:
    """Resolve a fact or document reference without failing after source purge."""

    fact_reference = _resolve_fact_reference(conn, ref)
    if fact_reference is not None:
        return fact_reference
    return _resolve_document_reference(conn, ref)
