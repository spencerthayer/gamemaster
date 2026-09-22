"""Deterministic import gate between extraction proposals and authoritative state."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tabletop.campaign.models import CanonState, KnowledgeState
from tabletop.campaign.store import CampaignStore
from tabletop.documents.extraction import (
    KNOWN_EXTRACTOR_VERSIONS,
    ProposedEntity,
    ProposedExtraction,
    ProposedFact,
)
from tabletop.documents.importer import (
    ImportEnvelopeError,
    ImportReport,
    import_extraction,
)
from tabletop.storage.sqlite import connect, migrate


_REPO_ROOT = Path(__file__).resolve().parents[2]
_BANNED_TOPLEVEL = {
    "providers",
    "httpx",
    "requests",
    "aiohttp",
    "openai",
    "anthropic",
    "credentials",
    "boto3",
}


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "import.db")
    migrate(connection)
    connection.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("setting-1", "Test Setting", "2026-09-22T00:00:00Z"),
    )
    CampaignStore(connection).create_campaign(
        "campaign-1",
        "First",
        "test",
        setting_id="setting-1",
    )
    connection.execute(
        "INSERT INTO ingest_jobs "
        "(job_id, document_hash, parser_version, slice_strategy_version, "
        "status, total_slices, completed_slices, failed_slices, "
        "started_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'completed', 1, 1, 0, ?, ?)",
        (
            "job-1",
            "hash-1",
            "1",
            "1",
            "2026-09-22T00:00:00Z",
            "2026-09-22T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO documents "
        "(document_id, content_hash, source_path, title, document_shape, "
        "content_pack_id, system_id, visibility, ingested_at) "
        "VALUES (?, ?, ?, ?, 'prose', NULL, NULL, 'GM', ?)",
        (
            "document-1",
            "doc-hash-1",
            "/tmp/doc.md",
            "Doc",
            "2026-09-22T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO document_chunks "
        "(chunk_id, document_id, ordinal, heading_path, page, text, "
        "content_hash, content_pack_id, system_id, visibility) "
        "VALUES (?, ?, 0, ?, NULL, ?, ?, NULL, NULL, 'GM')",
        (
            "chunk-1",
            "document-1",
            '["Doc"]',
            "Mara lives in Greyhaven.",
            "chunk-hash-1",
        ),
    )
    try:
        yield connection
    finally:
        connection.close()


def _entity(**overrides: object) -> ProposedEntity:
    values = {
        "entity_id": "mara",
        "owner_scope": "campaign",
        "setting_id": None,
        "campaign_id": "campaign-1",
        "overrides_id": None,
        "entity_type": "npc",
        "name": "Mara",
        "system_state": {},
        "metadata": {},
    }
    values.update(overrides)
    return ProposedEntity(**values)  # type: ignore[arg-type]


def _fact(**overrides: object) -> ProposedFact:
    values = {
        "fact_id": "fact-1",
        "fact_scope": "campaign",
        "setting_id": None,
        "campaign_id": "campaign-1",
        "subject_id": "mara",
        "predicate": "lives_in",
        "value": "Greyhaven",
        "source_document_id": "document-1",
        "source_chunk_id": "chunk-1",
    }
    values.update(overrides)
    return ProposedFact(**values)  # type: ignore[arg-type]


def _extraction(
    *,
    entities: tuple[ProposedEntity, ...] = (),
    facts: tuple[ProposedFact, ...] = (),
    job_id: str = "job-1",
    extractor_version: str | None = None,
) -> ProposedExtraction:
    version = (
        next(iter(KNOWN_EXTRACTOR_VERSIONS))
        if extractor_version is None
        else extractor_version
    )
    return ProposedExtraction(
        job_id=job_id,
        slice_index=0,
        extractor_version=version,
        entities=entities,
        facts=facts,
    )


def _fact_rows(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT fact_id, canon_state, knowledge_state, source_document_id, "
        "source_chunk_id, import_job_id, extraction_method, source_ownership "
        "FROM facts ORDER BY fact_id"
    ).fetchall()
    return [dict(row) for row in rows]


def test_known_extractor_versions_are_exported() -> None:
    assert isinstance(KNOWN_EXTRACTOR_VERSIONS, frozenset)
    assert KNOWN_EXTRACTOR_VERSIONS
    assert all(isinstance(item, str) and item for item in KNOWN_EXTRACTOR_VERSIONS)


def test_envelope_unknown_extractor_version_imports_nothing(conn) -> None:
    extraction = _extraction(
        facts=(_fact(),),
        extractor_version="not-a-known-extractor",
    )

    with pytest.raises(ImportEnvelopeError, match="extractor_version"):
        import_extraction(conn, extraction)

    assert _fact_rows(conn) == []


def test_envelope_unknown_fields_and_wrong_schema_import_nothing(conn) -> None:
    with pytest.raises(ImportEnvelopeError, match="unknown fields"):
        import_extraction(
            conn,
            {
                "job_id": "job-1",
                "slice_index": 0,
                "extractor_version": next(iter(KNOWN_EXTRACTOR_VERSIONS)),
                "entities": [],
                "facts": [],
                "extra": True,
            },
        )

    with pytest.raises(ImportEnvelopeError, match="slice_index|invalid"):
        import_extraction(
            conn,
            {
                "job_id": "job-1",
                "slice_index": -1,
                "extractor_version": next(iter(KNOWN_EXTRACTOR_VERSIONS)),
                "entities": [],
                "facts": [],
            },
        )

    assert _fact_rows(conn) == []


def test_envelope_bad_job_identity_imports_nothing(conn) -> None:
    extraction = _extraction(facts=(_fact(),), job_id="missing-job")

    with pytest.raises(ImportEnvelopeError, match="job"):
        import_extraction(conn, extraction)

    assert _fact_rows(conn) == []


def test_rejected_proposals_do_not_block_valid_siblings(conn) -> None:
    extraction = _extraction(
        entities=(
            _entity(),
            _entity(
                entity_id="fragment",
                name="lives in the...",
            ),
            _entity(
                entity_id="absurd",
                name="???",
                entity_type="noise",
            ),
        ),
        facts=(
            _fact(),
            _fact(
                fact_id="empty-subject",
                subject_id=None,
                predicate="weather",
                value="rain",
            ),
            _fact(
                fact_id="refused-subject",
                subject_id="unknown",
                predicate="is",
                value="placeholder",
            ),
        ),
    )

    report = import_extraction(conn, extraction)

    assert isinstance(report, ImportReport)
    assert report.accepted_ids == ("mara", "fact-1")
    reasons = {item.proposal_id: item.reason for item in report.rejected}
    assert set(reasons) == {"fragment", "absurd", "empty-subject", "refused-subject"}
    assert "fragment" in reasons["fragment"].lower() or "name" in reasons["fragment"].lower()
    assert "subject" in reasons["empty-subject"].lower()
    assert "subject" in reasons["refused-subject"].lower()
    assert "absurd" in reasons["absurd"].lower() or "entity" in reasons["absurd"].lower()

    rows = _fact_rows(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["fact_id"] == "fact-1"
    assert row["canon_state"] == CanonState.PROPOSED.value
    assert row["knowledge_state"] == KnowledgeState.UNREVEALED.value
    assert row["source_document_id"] == "document-1"
    assert row["source_chunk_id"] == "chunk-1"
    assert row["import_job_id"] == "job-1"
    assert row["extraction_method"] == next(iter(KNOWN_EXTRACTOR_VERSIONS))
    assert row["source_ownership"] == "attached"


def test_import_is_one_transaction_with_no_partial_batch(conn) -> None:
    extraction = _extraction(
        facts=(
            _fact(fact_id="dup"),
            _fact(fact_id="dup", predicate="serves"),
        )
    )

    with pytest.raises(Exception):
        import_extraction(conn, extraction)

    assert _fact_rows(conn) == []


def test_importer_module_has_no_provider_http_or_credential_imports() -> None:
    module_path = _REPO_ROOT / "tabletop" / "documents" / "importer.py"
    tree = ast.parse(module_path.read_text())
    tops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            tops.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            tops.add(node.module.split(".")[0])
    assert tops.isdisjoint(_BANNED_TOPLEVEL)

    probe = (
        "import sys\n"
        "import tabletop.documents.importer\n"
        f"banned = {sorted(_BANNED_TOPLEVEL)!r}\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in banned]\n"
        "assert not bad, bad\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=str(_REPO_ROOT)),
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
