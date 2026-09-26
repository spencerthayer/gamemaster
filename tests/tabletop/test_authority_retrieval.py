"""A retrieval score may break a tie. It may not grant authority."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.campaign.store import CampaignStore
from tabletop.documents.catalog import authority_aware_search
from tabletop.retrieval.models import RetrievedChunk, SourceReference
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "campaign-1"


def _chunk(document_id: str, text: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        text=text,
        score=score,
        namespace=_namespace(),
        source=SourceReference(
            chunk_id=f"{document_id}-0",
            document_id=document_id,
            document_title=document_id,
            section="",
            page=None,
            source_path=f"{document_id}.md",
        ),
    )


def _namespace():
    from tabletop.retrieval.models import RetrievalNamespace

    return RetrievalNamespace.CAMPAIGN


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "authority.db")
    migrate(connection)
    store = CampaignStore(connection)
    store.create_campaign(_CAMPAIGN, "First", "freeform")
    store.create_campaign("campaign-2", "Second", "freeform")
    for document_id, title in (
        ("doc-rules", "Rules"),
        ("doc-notes", "Notes"),
        ("doc-other", "Other Campaign"),
    ):
        connection.execute(
            "INSERT INTO documents (document_id, content_hash, source_path, title, "
            "document_shape, system_id, visibility, ingested_at) "
            "VALUES (?, ?, ?, ?, 'prose', 'freeform', 'GM', '2026-09-25T00:00:00Z')",
            (document_id, f"hash-{document_id}", f"{document_id}.md", title),
        )
    yield connection
    connection.close()


def _attach(conn: sqlite3.Connection, document_id: str, role: str, **kw: object) -> None:
    from tabletop.documents.catalog import ContentCatalog

    ContentCatalog(conn).attach_document(
        _CAMPAIGN, document_id, role, gm_only=bool(kw.get("gm_only", False))
    )


def _search(*chunks: RetrievedChunk):
    def run(_query: str) -> list[RetrievedChunk]:
        return list(chunks)

    return run


# -- authority beats score --------------------------------------------------


def test_a_matching_note_cannot_beat_attached_rules(
    conn: sqlite3.Connection,
) -> None:
    _attach(conn, "doc-rules", "rules")
    _attach(conn, "doc-notes", "notes")
    winner = authority_aware_search(
        conn,
        _CAMPAIGN,
        "gate dc",
        search=_search(
            _chunk("doc-notes", "The gate DC is 15.", 0.99),
            _chunk("doc-rules", "Gate DC 15.", 0.10),
        ),
    )
    assert winner is not None
    assert winner["document_id"] == "doc-rules"
    assert winner["role"] == "rules"


def test_score_decides_inside_one_tier(conn: sqlite3.Connection) -> None:
    _attach(conn, "doc-rules", "rules")
    _attach(conn, "doc-notes", "rules")
    winner = authority_aware_search(
        conn,
        _CAMPAIGN,
        "gate dc",
        search=_search(
            _chunk("doc-notes", "Gate DC 12.", 0.20),
            _chunk("doc-rules", "Gate DC 15.", 0.80),
        ),
    )
    assert winner is not None
    assert winner["document_id"] == "doc-rules"


def test_lower_tier_conflicts_are_preserved_for_inspection(
    conn: sqlite3.Connection,
) -> None:
    _attach(conn, "doc-rules", "rules")
    _attach(conn, "doc-notes", "adventure")
    # An adventure tier outranks base rules, so it wins, and the disagreement
    # is preserved for the GM rather than discarded.
    winner = authority_aware_search(
        conn,
        _CAMPAIGN,
        "gate dc",
        search=_search(
            _chunk("doc-rules", "Gate DC 15.", 0.99),
            _chunk("doc-notes", "Gate DC 9.", 0.1),
        ),
    )
    assert winner is not None
    assert winner["document_id"] == "doc-notes"
    assert [c["document_id"] for c in winner["conflicts"]] == ["doc-rules"]


# -- visibility -------------------------------------------------------------


def test_a_player_cannot_request_gm_only_content(
    conn: sqlite3.Connection,
) -> None:
    _attach(conn, "doc-rules", "rules", gm_only=True)
    player = authority_aware_search(
        conn,
        _CAMPAIGN,
        "gate dc",
        viewpoint_is_gm=False,
        search=_search(_chunk("doc-rules", "Gate DC 15.", 0.5)),
    )
    assert player is None


def test_a_gm_sees_gm_only_content(conn: sqlite3.Connection) -> None:
    _attach(conn, "doc-rules", "rules", gm_only=True)
    winner = authority_aware_search(
        conn,
        _CAMPAIGN,
        "gate dc",
        viewpoint_is_gm=True,
        search=_search(_chunk("doc-rules", "Gate DC 15.", 0.5)),
    )
    assert winner is not None
    assert winner["document_id"] == "doc-rules"


def test_content_from_another_campaign_never_appears(
    conn: sqlite3.Connection,
) -> None:
    _attach(conn, "doc-rules", "rules")
    winner = authority_aware_search(
        conn,
        _CAMPAIGN,
        "gate dc",
        search=_search(
            _chunk("doc-rules", "Gate DC 15.", 0.1),
            _chunk("doc-other", "Other campaign gate DC 99.", 0.99),
        ),
    )
    assert winner is not None
    assert winner["document_id"] == "doc-rules"
    assert "doc-other" not in {c["document_id"] for c in winner["conflicts"]}


def test_an_unattached_document_is_filtered_out(
    conn: sqlite3.Connection,
) -> None:
    _attach(conn, "doc-rules", "rules")
    winner = authority_aware_search(
        conn, _CAMPAIGN, "gate dc", search=_search(_chunk("doc-notes", "DC 15.", 0.9))
    )
    assert winner is None


def test_no_candidates_yields_nothing(conn: sqlite3.Connection) -> None:
    _attach(conn, "doc-rules", "rules")
    assert authority_aware_search(conn, _CAMPAIGN, "x", search=_search()) is None


def test_a_disabled_attachment_is_not_searched(conn: sqlite3.Connection) -> None:
    from tabletop.documents.catalog import ContentCatalog

    ContentCatalog(conn).attach_document(
        _CAMPAIGN, "doc-rules", "rules", enabled=False
    )
    winner = authority_aware_search(
        conn, _CAMPAIGN, "gate dc", search=_search(_chunk("doc-rules", "Gate DC 15.", 0.5))
    )
    assert winner is None


def test_the_search_runs_no_queries_against_the_database(
    conn: sqlite3.Connection,
) -> None:
    """Authority is decided from the catalog, not from a second retrieval."""
    _attach(conn, "doc-rules", "rules")
    calls: list[str] = []

    def run(query: str) -> list[RetrievedChunk]:
        calls.append(query)
        return [_chunk("doc-rules", "Gate DC 15.", 0.5)]

    authority_aware_search(conn, _CAMPAIGN, "gate dc", search=run)
    assert calls == ["gate dc"]
