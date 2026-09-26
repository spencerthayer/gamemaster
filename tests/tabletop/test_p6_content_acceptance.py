"""P6 acceptance: install, attach, authority, citations, and trust boundaries.

One scenario, several files: a rules document, an adventure pack, a GM-only
note, and a malicious executable-looking file. The campaign attaches only the
roles it should, a query picks by authority, a decision cites a rule that
refetches, and the executable is refused at every boundary.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.api.rules import RuleReference
from tabletop.campaign.store import CampaignStore
from tabletop.documents.catalog import ContentCatalog, authority_aware_search
from tabletop.documents.content_install import (
    ContentError,
    ContentKind,
    inspect_content,
    install_document,
    install_pack,
)
from tabletop.retrieval.models import RetrievedChunk, RetrievalNamespace, SourceReference
from tabletop.retrieval.references import (
    CitationError,
    assert_citations_resolve,
    cite_rule,
    verify_citation,
)
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "campaign-1"
_OTHER = "campaign-2"
_HASH = "hash-rules"


def _chunk(document_id: str, text: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        text=text,
        score=score,
        namespace=RetrievalNamespace.CAMPAIGN,
        source=SourceReference(
            chunk_id=f"{document_id}-0",
            document_id=document_id,
            document_title=document_id,
            section="",
            page=None,
            source_path=f"{document_id}.md",
        ),
    )


@pytest.fixture
def content(tmp_path: Path) -> dict[str, Path]:
    pack = tmp_path / "adventure"
    pack.mkdir()
    (pack / "content-pack.yaml").write_text(
        "id: adventure-pack\nname: Adventure\npack_type: adventure\n"
        "system_id: freeform\nversion: 1.0.0\n",
        encoding="utf-8",
    )
    (pack / "ruins.md").write_text("The ruins swallow the road.", encoding="utf-8")

    rules = tmp_path / "gate-rules.md"
    rules.write_text("Gate DC is 15.\n\nThe gate is rusted.", encoding="utf-8")

    notes = tmp_path / "gm-notes.md"
    notes.write_text("Vor blames Ada for the floodgate.", encoding="utf-8")

    payload = tmp_path / "payload.py"
    payload.write_text("import os\nos.system('true')\n", encoding="utf-8")

    return {"pack": pack, "rules": rules, "notes": notes, "payload": payload}


@pytest.fixture
def conn(tmp_path: Path, content: dict[str, Path]) -> sqlite3.Connection:
    connection = connect(tmp_path / "p6.db")
    migrate(connection)
    CampaignStore(connection).create_campaign(_CAMPAIGN, "First", "freeform")
    CampaignStore(connection).create_campaign(_OTHER, "Second", "freeform")
    # A completed ingest chain, which is what a citation resolves against.
    connection.execute(
        "INSERT INTO documents (document_id, content_hash, source_path, title, "
        "document_shape, system_id, visibility, ingested_at) "
        "VALUES ('doc-rules', ?, 'gate-rules.md', 'Gate Rules', 'prose', 'freeform', "
        "'GM', '2026-09-25T00:00:00Z')",
        (_HASH,),
    )
    connection.execute(
        "INSERT INTO document_chunks (chunk_id, document_id, ordinal, heading_path, "
        "text, content_hash, system_id, visibility) VALUES "
        "('chunk-rules', 'doc-rules', 0, 'Gates', 'Gate DC is 15.', 'ch', 'freeform', 'GM')",
    )
    connection.execute(
        "INSERT INTO ingest_jobs (job_id, document_hash, parser_version, "
        "slice_strategy_version, status, started_at, updated_at) VALUES "
        "('job-1', ?, 'v1', 'v1', 'completed', '2026-09-25T00:00:00Z', "
        "'2026-09-25T00:00:00Z')",
        (_HASH,),
    )
    connection.execute(
        "INSERT INTO facts (fact_id, fact_scope, campaign_id, subject_id, predicate, "
        "value, canon_state, knowledge_state, visibility, source_document_id, "
        "source_chunk_id, import_job_id, extraction_method, created_at) VALUES "
        "('fact-gate', 'campaign', ?, 'gate', 'dc', '15', 'confirmed', 'known', "
        "'PUBLIC', 'doc-rules', 'chunk-rules', 'job-1', 'rule_extract', "
        "'2026-09-25T00:00:00Z')",
        (_CAMPAIGN,),
    )
    yield connection
    connection.close()


# -- installation is not activation -----------------------------------------


def test_installation_alone_changes_nothing_for_a_campaign(
    conn: sqlite3.Connection, content: dict[str, Path]
) -> None:
    install_document(conn, content["rules"])
    install_pack(conn, content["pack"])
    catalog = ContentCatalog(conn)
    assert catalog.attached_documents(_CAMPAIGN) == []
    assert conn.execute("SELECT COUNT(*) FROM campaign_content_packs").fetchone()[0] == 0


def test_attaching_is_a_separate_explicit_step(
    conn: sqlite3.Connection, content: dict[str, Path]
) -> None:
    document_id = install_document(conn, content["rules"])
    ContentCatalog(conn).attach_document(_CAMPAIGN, document_id, "rules")
    assert [d["document_id"] for d in ContentCatalog(conn).attached_documents(_CAMPAIGN)] == [
        document_id
    ]


# -- role priority ----------------------------------------------------------


def test_role_priority_decides_not_score(
    conn: sqlite3.Connection, content: dict[str, Path]
) -> None:
    rules_id = install_document(conn, content["rules"])
    notes_id = install_document(conn, content["notes"])
    catalog = ContentCatalog(conn)
    catalog.attach_document(_CAMPAIGN, rules_id, "rules")
    catalog.attach_document(_CAMPAIGN, notes_id, "notes")

    winner = authority_aware_search(
        conn,
        _CAMPAIGN,
        "gate dc",
        search=lambda _q: [
            _chunk(notes_id, "Gate DC 15.", 0.99),
            _chunk(rules_id, "Gate DC 15.", 0.01),
        ],
    )
    assert winner is not None
    assert winner["document_id"] == rules_id
    assert winner["role"] == "rules"


# -- visibility -------------------------------------------------------------


def test_gm_only_notes_never_reach_a_player(
    conn: sqlite3.Connection, content: dict[str, Path]
) -> None:
    notes_id = install_document(conn, content["notes"])
    ContentCatalog(conn).attach_document(_CAMPAIGN, notes_id, "notes", gm_only=True)
    player = authority_aware_search(
        conn,
        _CAMPAIGN,
        "vor",
        viewpoint_is_gm=False,
        search=lambda _q: [_chunk(notes_id, "Vor blames Ada.", 0.9)],
    )
    assert player is None


def test_content_attached_to_another_campaign_never_appears(
    conn: sqlite3.Connection, content: dict[str, Path]
) -> None:
    rules_id = install_document(conn, content["rules"])
    ContentCatalog(conn).attach_document(_OTHER, rules_id, "rules")
    winner = authority_aware_search(
        conn,
        _CAMPAIGN,
        "gate dc",
        search=lambda _q: [_chunk(rules_id, "Gate DC 15.", 0.9)],
    )
    assert winner is None


# -- citations --------------------------------------------------------------


def test_a_decision_cites_a_rule_that_refetches(conn: sqlite3.Connection) -> None:
    citation = cite_rule(
        conn,
        RuleReference(source_id="fact-gate", chunk_id="chunk-rules"),
        authority_tier="active system rules",
        parameter="dc",
        parameter_interpretation="the gate rules list DC 15",
    )
    assert verify_citation(conn, citation) is True
    assert_citations_resolve(conn, (citation,))
    # Refetchable: the chunk is still there.
    row = conn.execute(
        "SELECT text FROM document_chunks WHERE chunk_id = ?", (citation.chunk_id,)
    ).fetchone()
    assert row is not None


def test_a_citation_to_a_purged_source_is_refused(conn: sqlite3.Connection) -> None:
    citation = cite_rule(
        conn,
        RuleReference(source_id="fact-gate", chunk_id="chunk-rules"),
        authority_tier="t",
        parameter="dc",
        parameter_interpretation="i",
    )
    conn.execute("DELETE FROM documents WHERE document_id = 'doc-rules'")
    with pytest.raises(CitationError):
        assert_citations_resolve(conn, (citation,))


# -- non-executable content boundaries --------------------------------------


def test_an_executable_looking_file_is_never_installed(
    conn: sqlite3.Connection, content: dict[str, Path]
) -> None:
    assert inspect_content(content["payload"]).kind is ContentKind.UNSUPPORTED
    with pytest.raises(ContentError):
        install_document(conn, content["payload"])
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


def test_a_traversing_content_path_is_refused(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    from tabletop.campaign.setup import SetupManifestError, parse_setup_manifest

    with pytest.raises(SetupManifestError):
        parse_setup_manifest(
            {
                "campaign_id": "x",
                "name": "X",
                "system_id": "freeform",
                "content": [{"path": "../../etc/passwd"}],
            },
            base_dir=tmp_path,
        )


def test_only_a_configured_plugin_root_may_execute_code() -> None:
    """A plugin directory is content-inspectable, never content-installable."""
    plugin_root = Path(__file__).resolve().parents[2] / "systems"
    for system in sorted(plugin_root.iterdir()) if plugin_root.is_dir() else []:
        if system.is_dir():
            assert inspect_content(system).kind is ContentKind.SYSTEM_PLUGIN
