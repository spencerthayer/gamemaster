"""Namespaced FTS5 lexical retrieval contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from tabletop.retrieval.interface import Retriever
from tabletop.retrieval.lexical import LexicalRetriever
from tabletop.retrieval.models import (
    RetrievalFilters,
    RetrievalNamespace,
    RetrievedChunk,
    SourceReference,
)
from tabletop.storage.sqlite import connect, migrate


def _retriever(database_path: Path) -> LexicalRetriever:
    connection = connect(database_path)
    migrate(connection)
    return LexicalRetriever(connection)


def _index(
    retriever: LexicalRetriever,
    namespace: RetrievalNamespace,
    *,
    chunk_id: str,
    text: str,
    document_id: str = "doc-1",
    document_title: str = "Core Rules",
    section: tuple[str, ...] = ("Combat", "Attack Rolls"),
    page: int | None = 12,
    source_path: str = "/rules/combat.md",
    content_pack_id: str | None = "core",
    system_id: str | None = "freeform",
    visibility: str = "GM",
) -> None:
    retriever.index(
        namespace,
        chunk_id=chunk_id,
        document_id=document_id,
        document_title=document_title,
        section=section,
        page=page,
        source_path=source_path,
        text=text,
        content_pack_id=content_pack_id,
        system_id=system_id,
        visibility=visibility,
    )


def test_retriever_search_contract_is_callable():
    assert Retriever._is_protocol
    assert callable(Retriever.search)


def test_search_returns_retrieved_chunks_with_score_namespace_and_source(tmp_path):
    retriever = _retriever(tmp_path / "retrieval.db")
    _index(
        retriever,
        RetrievalNamespace.SYSTEM,
        chunk_id="chunk-attack",
        text="An attack roll determines whether an attack hits its target.",
    )

    results = retriever.search(
        "attack roll",
        RetrievalFilters(namespace=RetrievalNamespace.SYSTEM),
        limit=5,
    )

    assert len(results) == 1
    chunk = results[0]
    assert isinstance(chunk, RetrievedChunk)
    assert "attack roll" in chunk.text.lower()
    assert chunk.score > 0
    assert chunk.namespace is RetrievalNamespace.SYSTEM
    assert isinstance(chunk.source, SourceReference)
    assert chunk.source.chunk_id == "chunk-attack"
    assert chunk.source.document_id == "doc-1"
    assert chunk.source.refetch_tool == "get-chunk"
    assert chunk.source.refetch_args["chunk_id"] == "chunk-attack"
    fetched = retriever.fetch(chunk.source)
    assert fetched is not None
    assert fetched.text == chunk.text


def test_namespaces_are_isolated(tmp_path):
    retriever = _retriever(tmp_path / "retrieval.db")
    shared_phrase = "unique retrieval isolation phrase"
    for namespace in RetrievalNamespace:
        _index(
            retriever,
            namespace,
            chunk_id=f"chunk-{namespace.value}",
            document_id=f"doc-{namespace.value}",
            text=f"{shared_phrase} lives in the {namespace.value} corpus.",
            document_title=f"{namespace.value} title",
        )

    for namespace in RetrievalNamespace:
        results = retriever.search(
            shared_phrase,
            RetrievalFilters(namespace=namespace),
            limit=10,
        )
        assert len(results) == 1
        assert results[0].namespace is namespace
        assert results[0].source.chunk_id == f"chunk-{namespace.value}"
        foreign = [
            row
            for row in results
            if row.namespace is not namespace
            or row.source.chunk_id != f"chunk-{namespace.value}"
        ]
        assert foreign == []


def test_limit_is_always_applied(tmp_path):
    retriever = _retriever(tmp_path / "retrieval.db")
    for index in range(5):
        _index(
            retriever,
            RetrievalNamespace.SETTING,
            chunk_id=f"chunk-{index}",
            document_id=f"doc-{index}",
            text=f"The capital city appears in lore document number {index}.",
        )

    results = retriever.search(
        "capital city",
        RetrievalFilters(namespace=RetrievalNamespace.SETTING),
        limit=2,
    )

    assert len(results) == 2


def test_fts5_special_characters_are_escaped_not_operators(tmp_path):
    retriever = _retriever(tmp_path / "retrieval.db")
    _index(
        retriever,
        RetrievalNamespace.CAMPAIGN,
        chunk_id="chunk-literal",
        text='The ritual says: match "exact phrase" and use star* tokens carefully.',
    )
    _index(
        retriever,
        RetrievalNamespace.CAMPAIGN,
        chunk_id="chunk-other",
        text="Unrelated campaign note about weather patterns.",
        document_id="doc-other",
    )

    results = retriever.search(
        'match "exact phrase" star*',
        RetrievalFilters(namespace=RetrievalNamespace.CAMPAIGN),
        limit=5,
    )

    assert len(results) == 1
    assert results[0].source.chunk_id == "chunk-literal"


def test_results_carry_human_citable_provenance(tmp_path):
    retriever = _retriever(tmp_path / "retrieval.db")
    _index(
        retriever,
        RetrievalNamespace.SYSTEM,
        chunk_id="chunk-cite",
        text="Cover grants a bonus to defense when the target is obscured.",
        document_title="Player Handbook",
        section=("Combat", "Cover"),
        page=196,
        source_path="/packs/core/phb.md",
    )

    results = retriever.search(
        "cover grants",
        RetrievalFilters(namespace=RetrievalNamespace.SYSTEM),
        limit=1,
    )

    assert len(results) == 1
    source = results[0].source
    assert source.document_title == "Player Handbook"
    assert source.section == "Combat > Cover"
    assert source.page == 196
    assert source.source_path == "/packs/core/phb.md"


def test_metadata_filters_narrow_results(tmp_path):
    retriever = _retriever(tmp_path / "retrieval.db")
    _index(
        retriever,
        RetrievalNamespace.ADVENTURE,
        chunk_id="chunk-a",
        text="The sealed vault contains ancient glyphs.",
        content_pack_id="pack-a",
        system_id="freeform",
        visibility="GM",
    )
    _index(
        retriever,
        RetrievalNamespace.ADVENTURE,
        chunk_id="chunk-b",
        document_id="doc-b",
        text="The sealed vault appears in the player summary.",
        content_pack_id="pack-b",
        system_id="freeform",
        visibility="PLAYER",
    )

    results = retriever.search(
        "sealed vault",
        RetrievalFilters(
            namespace=RetrievalNamespace.ADVENTURE,
            content_pack_id="pack-a",
            visibility="GM",
        ),
        limit=5,
    )

    assert [row.source.chunk_id for row in results] == ["chunk-a"]


def test_missing_fts5_fails_at_construction(tmp_path, monkeypatch):
    connection = connect(tmp_path / "no-fts.db")
    migrate(connection)
    monkeypatch.setattr(
        "tabletop.retrieval.lexical._sqlite_compile_options",
        lambda _conn: frozenset(),
    )

    with pytest.raises(Exception, match="ENABLE_FTS5"):
        LexicalRetriever(connection)
