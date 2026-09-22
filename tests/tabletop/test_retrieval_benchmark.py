"""Measure the current retrievers. Do not replace them from this test."""

from __future__ import annotations

import time
from pathlib import Path

from tabletop.retrieval.lexical import LexicalRetriever
from tabletop.retrieval.models import RetrievalFilters, RetrievalNamespace
from tabletop.retrieval.vector import VectorRetriever
from tabletop.storage.sqlite import connect, migrate


class _Embedder:
    model = "test"
    dimension = 4

    def embed(self, text: str) -> tuple[float, ...]:
        value = float(len(text) % 7)
        return (value, 1.0, 0.0, 0.5)


def test_lexical_and_vector_search_stay_under_two_seconds(tmp_path: Path) -> None:
    connection = connect(tmp_path / "bench.db")
    migrate(connection)
    lexical = LexicalRetriever(connection)
    vector = VectorRetriever(connection, _Embedder())
    for index in range(200):
        text = f"lantern passage {index} brass gate"
        lexical.index(
            RetrievalNamespace.SYSTEM,
            chunk_id=f"chunk-{index}",
            document_id="doc",
            document_title="Bench",
            section=("Rules",),
            page=1,
            source_path="/bench.md",
            text=text,
            visibility="GM",
        )
        vector.index(
            RetrievalNamespace.SYSTEM,
            chunk_id=f"vec-{index}",
            document_id="doc",
            document_title="Bench",
            section=("Rules",),
            text=text,
            visibility="GM",
        )
    filters = RetrievalFilters(namespace=RetrievalNamespace.SYSTEM)
    started = time.perf_counter()
    lexical_hits = lexical.search("lantern", filters, limit=5)
    lexical_seconds = time.perf_counter() - started
    started = time.perf_counter()
    vector_hits = vector.search("lantern", filters, limit=5)
    vector_seconds = time.perf_counter() - started
    assert lexical_hits
    assert vector_hits
    assert lexical_seconds < 2
    assert vector_seconds < 2
    connection.close()
