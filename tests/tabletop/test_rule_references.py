"""Resolve transport rule references to durable source identities."""

from __future__ import annotations

from pathlib import Path

import pytest

from tabletop.api.resolution import Resolution
from tabletop.api.rules import RuleReference
from tabletop.campaign.store import CampaignStore
from tabletop.documents.provenance import purge_document
from tabletop.retrieval.references import ResolvedRuleReference, resolve_reference
from tabletop.storage.sqlite import connect, migrate


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "references.db")
    migrate(connection)
    connection.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("setting-1", "Setting", "2026-09-22T00:00:00Z"),
    )
    CampaignStore(connection).create_campaign(
        "campaign-1",
        "Campaign",
        "system-1",
        setting_id="setting-1",
    )
    connection.execute(
        "INSERT INTO ingest_jobs "
        "(job_id, document_hash, parser_version, slice_strategy_version, "
        "status, total_slices, completed_slices, failed_slices, started_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'completed', 1, 1, 0, ?, ?)",
        (
            "job-1",
            "document-hash-1",
            "parser-3",
            "slice-2",
            "2026-09-22T00:00:00Z",
            "2026-09-22T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO ingest_slices (job_id, slice_index, status, updated_at) "
        "VALUES (?, 0, 'completed', ?)",
        ("job-1", "2026-09-22T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO documents "
        "(document_id, content_hash, source_path, title, document_shape, "
        "visibility, ingested_at) VALUES (?, ?, ?, ?, 'structured_rules', 'GM', ?)",
        (
            "document-1",
            "document-hash-1",
            "/library/core-rules.md",
            "Core Rules",
            "2026-09-22T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO document_chunks "
        "(chunk_id, document_id, ordinal, heading_path, page, text, content_hash, "
        "visibility) VALUES (?, ?, 7, ?, 42, ?, ?, 'GM')",
        (
            "chunk-7",
            "document-1",
            '["Combat", "Defense"]',
            "A defender may dodge.",
            "chunk-hash-7",
        ),
    )
    connection.execute(
        "INSERT INTO facts "
        "(fact_id, fact_scope, campaign_id, subject_id, predicate, value, "
        "source_document_id, source_chunk_id, import_job_id, extraction_method, "
        "created_at) VALUES (?, 'campaign', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "fact-1",
            "campaign-1",
            "dodge",
            "allows",
            "defense",
            "document-1",
            "chunk-7",
            "job-1",
            "extractor-1",
            "2026-09-22T00:00:00Z",
        ),
    )
    try:
        yield connection
    finally:
        connection.close()


def test_resolve_fact_reference_to_exact_source_version(conn) -> None:
    resolved = resolve_reference(
        conn,
        RuleReference(source_id="fact-1", chunk_id="chunk-7"),
    )

    assert resolved == ResolvedRuleReference(
        source_id="fact-1",
        document_id="document-1",
        document_path="/library/core-rules.md",
        content_hash="document-hash-1",
        chunk_id="chunk-7",
        ordinal=7,
        ingest_job_id="job-1",
        parser_version="parser-3",
        extractor_version="extractor-1",
    )


def test_resolve_document_reference_returns_original_path(conn) -> None:
    resolved = resolve_reference(
        conn,
        RuleReference(source_id="document-1", chunk_id="chunk-7"),
    )

    assert resolved is not None
    assert resolved.document_path == "/library/core-rules.md"
    assert resolved.content_hash == "document-hash-1"
    assert resolved.ordinal == 7


def test_resolve_transport_reference_by_original_path(conn) -> None:
    resolved = resolve_reference(
        conn,
        RuleReference(
            source_id="core-rules",
            document_path="/library/core-rules.md",
            chunk_id="chunk-7",
        ),
    )

    assert resolved is not None
    assert resolved.document_id == "document-1"
    assert resolved.document_path == "/library/core-rules.md"


def test_resolve_reference_reports_purged_document_as_absent(conn) -> None:
    ref = RuleReference(source_id="fact-1", chunk_id="chunk-7")
    purge_document(conn, "document-1")

    assert resolve_reference(conn, ref) is None


def test_deterministic_resolution_keeps_rule_references() -> None:
    ref = RuleReference(source_id="fact-1", chunk_id="chunk-7")

    resolution = Resolution(outcome={"allowed": True}, rule_references=(ref,))

    assert resolution.rule_references == (ref,)
