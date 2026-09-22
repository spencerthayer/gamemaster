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
    chunk_id: str
    ordinal: int
    ingest_job_id: str
    parser_version: str
    extractor_version: str

    @property
    def document_content_hash(self) -> str:
        """Explicit alias for callers that distinguish document and chunk hashes."""

        return self.content_hash

    @property
    def chunk_or_slice_ordinal(self) -> int:
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
        "JOIN documents AS d ON d.document_id = f.source_document_id "
        "JOIN document_chunks AS c "
        "ON c.chunk_id = f.source_chunk_id AND c.document_id = d.document_id "
        "JOIN ingest_jobs AS j "
        "ON j.job_id = f.import_job_id AND j.document_hash = d.content_hash "
        "WHERE f.fact_id = ? "
        "AND f.extraction_method IS NOT NULL "
        "AND (? IS NULL OR f.source_chunk_id = ?) "
        "AND (? IS NULL OR d.source_path = ?)",
        (
            ref.source_id,
            ref.chunk_id,
            ref.chunk_id,
            ref.document_path,
            ref.document_path,
        ),
    ).fetchone()
    if row is None:
        return None
    return ResolvedRuleReference(
        source_id=str(row["fact_id"]),
        document_id=str(row["document_id"]),
        document_path=str(row["source_path"]),
        content_hash=str(row["content_hash"]),
        chunk_id=str(row["chunk_id"]),
        ordinal=int(row["ordinal"]),
        ingest_job_id=str(row["import_job_id"]),
        parser_version=str(row["parser_version"]),
        extractor_version=str(row["extraction_method"]),
    )


def _resolve_document_reference(
    conn: sqlite3.Connection,
    ref: RuleReference,
) -> ResolvedRuleReference | None:
    rows = conn.execute(
        "WITH sole_job AS ("
        "SELECT document_hash, MIN(job_id) AS job_id "
        "FROM ingest_jobs "
        "GROUP BY document_hash "
        "HAVING COUNT(*) = 1"
        ") "
        "SELECT d.document_id, d.source_path, d.content_hash, "
        "c.chunk_id, c.ordinal, j.job_id, j.parser_version, "
        "f.extraction_method "
        "FROM documents AS d "
        "JOIN document_chunks AS c "
        "ON c.document_id = d.document_id AND c.chunk_id = ? "
        "JOIN sole_job AS sj ON sj.document_hash = d.content_hash "
        "JOIN ingest_jobs AS j "
        "ON j.job_id = sj.job_id AND j.document_hash = d.content_hash "
        "JOIN facts AS f "
        "ON f.source_document_id = d.document_id "
        "AND f.source_chunk_id = c.chunk_id "
        "AND f.import_job_id = j.job_id "
        "AND f.extraction_method IS NOT NULL "
        "WHERE ("
        "d.document_id = ? "
        "OR ("
        "NOT EXISTS (SELECT 1 FROM documents WHERE document_id = ?) "
        "AND (? IS NOT NULL OR ? IS NOT NULL)"
        ")"
        ") "
        "AND (? IS NULL OR d.source_path = ?) "
        "GROUP BY d.document_id, d.source_path, d.content_hash, "
        "c.chunk_id, c.ordinal, j.job_id, j.parser_version, "
        "f.extraction_method",
        (
            ref.chunk_id,
            ref.source_id,
            ref.source_id,
            ref.document_path,
            ref.chunk_id,
            ref.document_path,
            ref.document_path,
        ),
    ).fetchall()
    if len(rows) != 1:
        return None
    row = rows[0]
    return ResolvedRuleReference(
        source_id=ref.source_id,
        document_id=str(row["document_id"]),
        document_path=str(row["source_path"]),
        content_hash=str(row["content_hash"]),
        chunk_id=str(row["chunk_id"]),
        ordinal=int(row["ordinal"]),
        ingest_job_id=str(row["job_id"]),
        parser_version=str(row["parser_version"]),
        extractor_version=str(row["extraction_method"]),
    )


def resolve_reference(
    conn: sqlite3.Connection,
    ref: RuleReference,
) -> ResolvedRuleReference | None:
    """Resolve a fact or document reference without failing after source purge."""

    fact_exists = conn.execute(
        "SELECT 1 FROM facts WHERE fact_id = ?",
        (ref.source_id,),
    ).fetchone()
    if fact_exists is not None:
        return _resolve_fact_reference(conn, ref)
    return _resolve_document_reference(conn, ref)
