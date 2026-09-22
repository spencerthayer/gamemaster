"""Retrieved chunks name get-chunk and reload the stored source row."""

from __future__ import annotations

import json
from pathlib import Path

from tabletop.api.visibility import Viewpoint, parse_scope
from tabletop.api.workspace import Workspace
from tabletop.orchestration.context import ContextSource, _entry_from_record, compact
from tabletop.retrieval.lexical import LexicalRetriever, load_visible_chunk
from tabletop.retrieval.models import RetrievalFilters, RetrievalNamespace
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate


def _store_chunk(connection, *, visibility: str = "GM") -> None:
    connection.execute(
        "INSERT INTO documents "
        "(document_id, content_hash, source_path, title, document_shape, "
        "visibility, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "doc-1",
            "hash-doc",
            "/rules/combat.md",
            "Core Rules",
            "prose",
            visibility,
            "2026-09-22T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO document_chunks "
        "(chunk_id, document_id, ordinal, heading_path, page, text, "
        "content_hash, visibility) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "chunk-attack",
            "doc-1",
            0,
            '["Combat"]',
            12,
            "An attack roll determines whether an attack hits its target.",
            "hash-chunk",
            visibility,
        ),
    )
    connection.commit()


def test_lexical_hits_name_get_chunk(tmp_path: Path) -> None:
    connection = connect(tmp_path / "chunks.db")
    migrate(connection)
    retriever = LexicalRetriever(connection)
    retriever.index(
        RetrievalNamespace.SYSTEM,
        chunk_id="chunk-attack",
        document_id="doc-1",
        document_title="Core Rules",
        section=("Combat",),
        page=12,
        source_path="/rules/combat.md",
        text="An attack roll determines whether an attack hits its target.",
        visibility="GM",
    )
    chunk = retriever.search(
        "attack roll",
        RetrievalFilters(namespace=RetrievalNamespace.SYSTEM),
        limit=1,
    )[0]
    assert chunk.source.refetch_tool == "get-chunk"
    assert chunk.source.refetch_args["chunk_id"] == "chunk-attack"
    connection.close()


def test_get_chunk_reloads_the_library_row_and_hides_gm_text(tmp_path: Path) -> None:
    connection = connect(tmp_path / "chunks.db")
    migrate(connection)
    _store_chunk(connection)
    runtime = TabletopRuntime(
        tmp_path,
        campaign_roots=[],
        plugin_roots=[],
        connection=connection,
        workspace=Workspace.CAMPAIGN,
        active_campaign=None,
    )
    loaded = runtime.get_chunk("chunk-attack")
    assert loaded["ok"] is True
    assert loaded["data"]["text"] == (
        "An attack roll determines whether an attack hits its target."
    )
    assert loaded["data"]["document_id"] == "doc-1"
    assert loaded["data"]["source_path"] == "/rules/combat.md"
    missing = runtime.get_chunk("missing")
    assert missing["ok"] is False
    hidden = load_visible_chunk(
        connection,
        "chunk-attack",
        viewpoint=Viewpoint(scope=parse_scope("CHARACTER:hero")),
    )
    assert hidden is None
    connection.close()


def test_compacted_chunk_refetches_the_same_source_record(tmp_path: Path) -> None:
    connection = connect(tmp_path / "chunks.db")
    migrate(connection)
    _store_chunk(connection)
    long_text = "attack roll " + ("detail " * 400)
    connection.execute(
        "UPDATE document_chunks SET text = ? WHERE chunk_id = ?",
        (long_text, "chunk-attack"),
    )
    connection.commit()
    retriever = LexicalRetriever(connection)
    retriever.index(
        RetrievalNamespace.SYSTEM,
        chunk_id="chunk-attack",
        document_id="doc-1",
        document_title="Core Rules",
        section=("Combat",),
        page=12,
        source_path="/rules/combat.md",
        text=long_text,
        visibility="GM",
    )
    chunk = retriever.search(
        "attack roll",
        RetrievalFilters(namespace=RetrievalNamespace.SYSTEM),
        limit=1,
    )[0]
    entry = _entry_from_record(chunk, ContextSource.RETRIEVED_RULES, 1)
    assert entry is not None
    compacted = compact((entry,), budget=40)
    assert compacted[0].refetch_tool == "get-chunk"
    runtime = TabletopRuntime(
        tmp_path,
        campaign_roots=[],
        plugin_roots=[],
        connection=connection,
        workspace=Workspace.SETTING,
        active_campaign=None,
    )
    recovered = runtime.get_chunk(str(compacted[0].refetch_args["chunk_id"]))
    assert recovered["data"]["text"] == chunk.text
    assert recovered["data"]["document_id"] == chunk.source.document_id
    assert "summary" not in json.dumps(recovered["data"])
    connection.close()
