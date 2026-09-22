"""Vector retrieval and explicit degrade cascade contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from tabletop.retrieval.hybrid import (
    CascadeRetriever,
    RetrievalTier,
)
from tabletop.retrieval.lexical import LexicalRetriever
from tabletop.retrieval.models import RetrievalFilters, RetrievalNamespace
from tabletop.retrieval.vector import (
    EmbeddingDimensionError,
    EmbeddingModelError,
    Embedder,
    VectorRetriever,
)
from tabletop.storage.sqlite import connect, migrate


@dataclass(frozen=True)
class StaticEmbedder:
    model: str
    dimension: int
    vectors: dict[str, tuple[float, ...]]

    def embed(self, text: str) -> tuple[float, ...]:
        return self.vectors[text]


def _connection(database_path: Path):
    connection = connect(database_path)
    migrate(connection)
    return connection


def _index_vector(
    retriever: VectorRetriever,
    *,
    chunk_id: str = "chunk-1",
    text: str = "ancient sealed vault",
) -> None:
    retriever.index(
        RetrievalNamespace.ADVENTURE,
        chunk_id=chunk_id,
        document_id="doc-1",
        document_title="The Lost Vault",
        section=("Vault", "Door"),
        text=text,
        page=14,
        source_path="/adventures/vault.md",
        content_pack_id="vault-pack",
        system_id="freeform",
        visibility="GM",
    )


def _index_lexical(
    retriever: LexicalRetriever,
    *,
    chunk_id: str = "chunk-1",
    text: str = "ancient sealed vault",
) -> None:
    retriever.index(
        RetrievalNamespace.ADVENTURE,
        chunk_id=chunk_id,
        document_id="doc-1",
        document_title="The Lost Vault",
        section=("Vault", "Door"),
        text=text,
        page=14,
        source_path="/adventures/vault.md",
        content_pack_id="vault-pack",
        system_id="freeform",
        visibility="GM",
    )


def test_embedder_is_a_runtime_protocol():
    assert Embedder._is_protocol


def test_vector_rows_pin_model_dimension_and_encoded_vector(tmp_path):
    connection = _connection(tmp_path / "vector.db")
    embedder = StaticEmbedder(
        model="tiny-embedding-v1",
        dimension=2,
        vectors={"ancient sealed vault": (0.25, 0.75)},
    )
    retriever = VectorRetriever(connection, embedder)

    _index_vector(retriever)

    row = connection.execute(
        "SELECT chunk_id, embedding_model, dimension, embedding "
        "FROM retrieval_vectors WHERE chunk_id = ?",
        ("chunk-1",),
    ).fetchone()
    assert dict(row) == {
        "chunk_id": "chunk-1",
        "embedding_model": "tiny-embedding-v1",
        "dimension": 2,
        "embedding": "[0.25,0.75]",
    }


def test_vector_search_raises_before_comparing_a_different_dimension(tmp_path):
    connection = _connection(tmp_path / "dimension.db")
    indexing_embedder = StaticEmbedder(
        model="two-dimensional",
        dimension=2,
        vectors={"ancient sealed vault": (1.0, 0.0)},
    )
    _index_vector(VectorRetriever(connection, indexing_embedder))
    querying_embedder = StaticEmbedder(
        model="three-dimensional",
        dimension=3,
        vectors={"vault": (1.0, 0.0, 0.0)},
    )

    with pytest.raises(
        EmbeddingDimensionError,
        match="stored dimension 2 does not match embedder dimension 3",
    ):
        VectorRetriever(connection, querying_embedder).search(
            "vault",
            RetrievalFilters(namespace=RetrievalNamespace.ADVENTURE),
            limit=5,
        )


def test_same_dimension_different_model_raises_and_cascade_uses_lexical(tmp_path):
    connection = _connection(tmp_path / "model-mismatch.db")
    indexing_embedder = StaticEmbedder(
        model="embedding-model-a",
        dimension=2,
        vectors={"ancient sealed vault": (1.0, 0.0)},
    )
    _index_vector(VectorRetriever(connection, indexing_embedder))
    querying_embedder = StaticEmbedder(
        model="embedding-model-b",
        dimension=2,
        vectors={"vault": (1.0, 0.0)},
    )
    vector = VectorRetriever(connection, querying_embedder)

    with pytest.raises(
        EmbeddingModelError,
        match="stored model 'embedding-model-a' does not match "
        "embedder model 'embedding-model-b'",
    ):
        vector.search(
            "vault",
            RetrievalFilters(namespace=RetrievalNamespace.ADVENTURE),
            limit=5,
        )

    lexical = LexicalRetriever(connection)
    _index_lexical(lexical)
    result = CascadeRetriever(vector=vector, lexical=lexical).search(
        "vault",
        RetrievalFilters(namespace=RetrievalNamespace.ADVENTURE),
        limit=5,
    )

    assert result.answered_by is RetrievalTier.LEXICAL
    assert [(skip.tier, skip.reason) for skip in result.skipped_tiers] == [
        (
            RetrievalTier.SEMANTIC,
            "stored model 'embedding-model-a' does not match "
            "embedder model 'embedding-model-b'",
        )
    ]
    assert [chunk.source.chunk_id for chunk in result.chunks] == ["chunk-1"]


def test_matching_embedder_uses_only_the_semantic_tier(tmp_path):
    connection = _connection(tmp_path / "semantic.db")
    embedder = StaticEmbedder(
        model="tiny-embedding-v1",
        dimension=2,
        vectors={
            "ancient sealed vault": (1.0, 0.0),
            "forgotten treasure": (0.9, 0.1),
        },
    )
    vector = VectorRetriever(connection, embedder)
    _index_vector(vector)
    lexical = LexicalRetriever(connection)
    _index_lexical(lexical)

    result = CascadeRetriever(vector=vector, lexical=lexical).search(
        "forgotten treasure",
        RetrievalFilters(namespace=RetrievalNamespace.ADVENTURE),
        limit=5,
    )

    assert result.answered_by is RetrievalTier.SEMANTIC
    assert result.skipped_tiers == ()
    assert [chunk.source.chunk_id for chunk in result.chunks] == ["chunk-1"]


@pytest.mark.parametrize(
    ("vector_reason", "vector_factory"),
    [
        ("embedder unavailable", lambda _connection: None),
        (
            "stored dimension 2 does not match embedder dimension 3",
            lambda connection: VectorRetriever(
                connection,
                StaticEmbedder(
                    model="three-dimensional",
                    dimension=3,
                    vectors={"vault": (1.0, 0.0, 0.0)},
                ),
            ),
        ),
    ],
)
def test_missing_or_mismatched_embedder_degrades_to_lexical(
    tmp_path,
    vector_reason,
    vector_factory,
):
    connection = _connection(tmp_path / "lexical-fallback.db")
    indexing_embedder = StaticEmbedder(
        model="two-dimensional",
        dimension=2,
        vectors={"ancient sealed vault": (1.0, 0.0)},
    )
    _index_vector(VectorRetriever(connection, indexing_embedder))
    lexical = LexicalRetriever(connection)
    _index_lexical(lexical)

    result = CascadeRetriever(
        vector=vector_factory(connection),
        lexical=lexical,
    ).search(
        "vault",
        RetrievalFilters(namespace=RetrievalNamespace.ADVENTURE),
        limit=5,
    )

    assert result.answered_by is RetrievalTier.LEXICAL
    assert [(skip.tier, skip.reason) for skip in result.skipped_tiers] == [
        (RetrievalTier.SEMANTIC, vector_reason)
    ]
    assert [chunk.source.chunk_id for chunk in result.chunks] == ["chunk-1"]


def test_unavailable_result_names_every_skipped_tier():
    result = CascadeRetriever(vector=None, lexical=None).search(
        "vault",
        RetrievalFilters(namespace=RetrievalNamespace.ADVENTURE),
        limit=5,
    )

    assert result.answered_by is RetrievalTier.UNAVAILABLE
    assert result.chunks == ()
    assert [(skip.tier, skip.reason) for skip in result.skipped_tiers] == [
        (RetrievalTier.SEMANTIC, "embedder unavailable"),
        (RetrievalTier.LEXICAL, "FTS5 unavailable"),
    ]


def test_search_is_a_cascade_not_a_fused_score_and_writes_no_facts(tmp_path):
    connection = _connection(tmp_path / "read-only-search.db")
    embedder = StaticEmbedder(
        model="tiny-embedding-v1",
        dimension=2,
        vectors={
            "ancient sealed vault": (1.0, 0.0),
            "vault": (1.0, 0.0),
        },
    )
    vector = VectorRetriever(connection, embedder)
    _index_vector(vector)
    lexical = LexicalRetriever(connection)
    _index_lexical(lexical)
    statements: list[str] = []
    connection.set_trace_callback(statements.append)

    result = CascadeRetriever(vector=vector, lexical=lexical).search(
        "vault",
        RetrievalFilters(namespace=RetrievalNamespace.ADVENTURE),
        limit=5,
    )

    assert result.answered_by is RetrievalTier.SEMANTIC
    assert len(result.chunks) == 1
    assert not any("facts" in statement.lower() for statement in statements)
    assert not any("fts_adventure" in statement.lower() for statement in statements)
