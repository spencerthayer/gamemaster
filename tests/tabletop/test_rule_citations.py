"""A citation stays answerable, or the decision is not settled."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.api.rules import RuleReference
from tabletop.campaign.store import CampaignStore
from tabletop.retrieval.references import (
    CitationError,
    assert_citations_resolve,
    cite_rule,
    verify_citation,
)
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "campaign-1"
_HASH = "hash-rules-1"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "citations.db")
    migrate(connection)
    CampaignStore(connection).create_campaign(_CAMPAIGN, "First", "freeform")
    connection.execute(
        "INSERT INTO documents (document_id, content_hash, source_path, title, "
        "document_shape, system_id, visibility, ingested_at) "
        "VALUES ('doc-1', ?, 'rules.md', 'Gate Rules', 'prose', 'freeform', 'GM', "
        "'2026-09-25T00:00:00Z')",
        (_HASH,),
    )
    connection.execute(
        "INSERT INTO document_chunks (chunk_id, document_id, ordinal, heading_path, "
        "text, content_hash, system_id, visibility) "
        "VALUES ('chunk-1', 'doc-1', 0, 'Gates', 'Gate DC is 15.', 'chunk-hash', "
        "'freeform', 'GM')",
    )
    # Reference resolution requires a completed ingest job and a derived fact,
    # not just a document row. Seeding the whole chain keeps the fixture
    # honest about what a real citation depends on.
    connection.execute(
        "INSERT INTO ingest_jobs (job_id, document_hash, parser_version, "
        "slice_strategy_version, status, started_at, updated_at) "
        "VALUES ('job-1', ?, 'v1', 'v1', 'completed', "
        "'2026-09-25T00:00:00Z', '2026-09-25T00:00:00Z')",
        (_HASH,),
    )
    connection.execute(
        "INSERT INTO facts (fact_id, fact_scope, campaign_id, subject_id, predicate, "
        "value, canon_state, knowledge_state, visibility, source_document_id, "
        "source_chunk_id, import_job_id, extraction_method, created_at) "
        "VALUES ('fact-1', 'campaign', ?, 'gate', 'dc', '15', 'confirmed', "
        "'known', 'PUBLIC', 'doc-1', 'chunk-1', 'job-1', 'rule_extract', "
        "'2026-09-25T00:00:00Z')",
        (_CAMPAIGN,),
    )
    yield connection
    connection.close()


def _ref(chunk_id: str = "chunk-1") -> RuleReference:
    return RuleReference(
        source_id="doc-1",
        title="Gate Rules",
        section="Gates",
        chunk_id=chunk_id,
    )


def test_a_citation_records_the_exact_source(conn: sqlite3.Connection) -> None:
    citation = cite_rule(
        conn,
        _ref(),
        authority_tier="active system rules",
        parameter="dc",
        parameter_interpretation="the gate lists DC 15",
    )
    assert citation.document_id == "doc-1"
    assert citation.chunk_id == "chunk-1"
    assert citation.content_hash == _HASH
    assert citation.parameter == "dc"
    assert citation.authority_tier == "active system rules"


def test_a_citation_carries_a_refetch_recipe(conn: sqlite3.Connection) -> None:
    citation = cite_rule(
        conn, _ref(), authority_tier="t", parameter="dc", parameter_interpretation="i"
    )
    payload = citation.to_dict()
    assert payload["refetch_tool"]
    assert payload["refetch_args"]["chunk_id"] == "chunk-1"


def test_a_citation_to_a_purged_source_is_refused(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM documents WHERE document_id = 'doc-1'")
    with pytest.raises(CitationError, match="cannot be refetched"):
        cite_rule(
            conn, _ref(), authority_tier="t", parameter="dc", parameter_interpretation="i"
        )


def test_a_verified_citation_verifies(conn: sqlite3.Connection) -> None:
    citation = cite_rule(
        conn, _ref(), authority_tier="t", parameter="dc", parameter_interpretation="i"
    )
    assert verify_citation(conn, citation) is True


def test_a_citation_whose_source_changed_is_stale(conn: sqlite3.Connection) -> None:
    citation = cite_rule(
        conn, _ref(), authority_tier="t", parameter="dc", parameter_interpretation="i"
    )
    conn.execute(
        "UPDATE documents SET content_hash = 'changed' WHERE document_id = 'doc-1'"
    )
    assert verify_citation(conn, citation) is False


def test_asserting_one_unresolvable_citation_names_it(conn: sqlite3.Connection) -> None:
    citation = cite_rule(
        conn, _ref(), authority_tier="t", parameter="dc", parameter_interpretation="i"
    )
    conn.execute("DELETE FROM documents WHERE document_id = 'doc-1'")
    with pytest.raises(CitationError, match="doc-1"):
        assert_citations_resolve(conn, (citation,))


def test_asserting_several_unresolvable_citations_lists_them(
    conn: sqlite3.Connection,
) -> None:
    citation = cite_rule(
        conn, _ref(), authority_tier="t", parameter="dc", parameter_interpretation="i"
    )
    conn.execute("DELETE FROM documents WHERE document_id = 'doc-1'")
    with pytest.raises(CitationError, match="citations cannot be refetched"):
        assert_citations_resolve(conn, (citation, citation))


def test_a_fully_resolved_citation_set_passes(conn: sqlite3.Connection) -> None:
    citation = cite_rule(
        conn, _ref(), authority_tier="t", parameter="dc", parameter_interpretation="i"
    )
    assert_citations_resolve(conn, (citation, citation))
