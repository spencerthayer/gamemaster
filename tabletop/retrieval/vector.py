"""SQLite-backed vector retrieval with a pinned embedding model.

This first backend performs brute-force cosine similarity in Python after
loading every candidate vector in one namespace. It is intended for small
corpora, not unbounded production indexes. A later backend can replace only
the candidate similarity step while preserving the ``Retriever`` interface.
"""

from __future__ import annotations

import json
import math
import sqlite3
from typing import Protocol

from tabletop.retrieval.models import (
    RetrievalFilters,
    RetrievalNamespace,
    RetrievedChunk,
    SourceReference,
)


class Embedder(Protocol):
    """Caller-supplied text embedding provider."""

    @property
    def model(self) -> str:
        """Stable embedding model name."""

    @property
    def dimension(self) -> int:
        """Number of coordinates returned by ``embed``."""

    def embed(self, text: str) -> tuple[float, ...]:
        """Encode text into a fixed-width vector."""


class EmbeddingDimensionError(ValueError):
    """Raised before vectors with different dimensions can be compared."""


class EmbeddingModelError(ValueError):
    """Raised when indexing would mix embedding models in one store."""


_CREATE_VECTOR_TABLE = """
CREATE TABLE IF NOT EXISTS retrieval_vectors (
    namespace TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    document_title TEXT NOT NULL,
    heading_path TEXT NOT NULL,
    page INTEGER,
    source_path TEXT NOT NULL,
    content_pack_id TEXT,
    system_id TEXT,
    visibility TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    embedding TEXT NOT NULL,
    PRIMARY KEY (namespace, chunk_id)
)
"""


def _encode(vector: tuple[float, ...]) -> str:
    return json.dumps(vector, separators=(",", ":"))


def _decode(encoded: str) -> tuple[float, ...]:
    values = json.loads(encoded)
    if not isinstance(values, list):
        raise ValueError("stored embedding must be a JSON array")
    return tuple(float(value) for value in values)


def _cosine_similarity(
    left: tuple[float, ...],
    right: tuple[float, ...],
) -> float:
    if len(left) != len(right):
        raise EmbeddingDimensionError(
            f"cannot compare dimensions {len(left)} and {len(right)}"
        )
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    dot_product = sum(
        left_value * right_value
        for left_value, right_value in zip(left, right, strict=True)
    )
    return dot_product / (left_norm * right_norm)


class VectorRetriever:
    """Persist embeddings in SQLite and rank namespace candidates in Python."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        embedder: Embedder,
    ) -> None:
        if embedder.dimension <= 0:
            raise ValueError("embedder dimension must be positive")
        if not embedder.model:
            raise ValueError("embedder model must be non-empty")
        self._connection = connection
        self._embedder = embedder
        self._connection.execute(_CREATE_VECTOR_TABLE)

    def index(
        self,
        namespace: RetrievalNamespace,
        *,
        chunk_id: str,
        document_id: str,
        document_title: str,
        section: str | tuple[str, ...] | list[str],
        text: str,
        page: int | None = None,
        source_path: str = "",
        content_pack_id: str | None = None,
        system_id: str | None = None,
        visibility: str = "GM",
    ) -> None:
        """Insert or replace one embedded chunk."""
        if not isinstance(namespace, RetrievalNamespace):
            raise TypeError(
                f"namespace must be RetrievalNamespace, got {type(namespace).__name__}"
            )
        self._assert_store_identity()
        embedding = self._embedder.embed(text)
        self._assert_embedding_dimension(embedding)
        heading_path = (
            json.dumps([part.strip() for part in section.split(">") if part.strip()])
            if isinstance(section, str)
            else json.dumps(list(section))
        )
        self._connection.execute(
            "INSERT INTO retrieval_vectors "
            "(namespace, chunk_id, document_id, document_title, heading_path, "
            "page, source_path, content_pack_id, system_id, visibility, text, "
            "embedding_model, dimension, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(namespace, chunk_id) DO UPDATE SET "
            "document_id = excluded.document_id, "
            "document_title = excluded.document_title, "
            "heading_path = excluded.heading_path, "
            "page = excluded.page, "
            "source_path = excluded.source_path, "
            "content_pack_id = excluded.content_pack_id, "
            "system_id = excluded.system_id, "
            "visibility = excluded.visibility, "
            "text = excluded.text, "
            "embedding_model = excluded.embedding_model, "
            "dimension = excluded.dimension, "
            "embedding = excluded.embedding",
            (
                namespace.value,
                chunk_id,
                document_id,
                document_title,
                heading_path,
                page,
                source_path,
                content_pack_id,
                system_id,
                visibility,
                text,
                self._embedder.model,
                self._embedder.dimension,
                _encode(embedding),
            ),
        )

    def search(
        self,
        query: str,
        filters: RetrievalFilters,
        limit: int,
    ) -> tuple[RetrievedChunk, ...]:
        """Rank candidates from exactly one namespace by cosine similarity."""
        if limit <= 0:
            raise ValueError("limit must be positive")
        if not isinstance(filters, RetrievalFilters):
            raise TypeError(
                f"filters must be RetrievalFilters, got {type(filters).__name__}"
            )

        rows = self._connection.execute(
            "SELECT chunk_id, document_id, document_title, heading_path, page, "
            "source_path, text, embedding_model, dimension, embedding "
            "FROM retrieval_vectors "
            "WHERE namespace = ? "
            "AND (? IS NULL OR content_pack_id = ?) "
            "AND (? IS NULL OR system_id = ?) "
            "AND (? IS NULL OR visibility = ?)",
            (
                filters.namespace.value,
                filters.content_pack_id,
                filters.content_pack_id,
                filters.system_id,
                filters.system_id,
                filters.visibility,
                filters.visibility,
            ),
        ).fetchall()
        self._assert_candidate_identity(rows)
        query_embedding = self._embedder.embed(query)
        self._assert_embedding_dimension(query_embedding)

        ranked = sorted(
            (
                (
                    _cosine_similarity(query_embedding, _decode(row["embedding"])),
                    row,
                )
                for row in rows
            ),
            key=lambda item: (-item[0], item[1]["chunk_id"]),
        )
        return tuple(
            RetrievedChunk(
                text=row["text"],
                score=score,
                namespace=filters.namespace,
                source=self._source_from_row(row),
            )
            for score, row in ranked[:limit]
        )

    def _assert_store_identity(self) -> None:
        row = self._connection.execute(
            "SELECT embedding_model, dimension FROM retrieval_vectors LIMIT 1"
        ).fetchone()
        if row is None:
            return
        if row["embedding_model"] != self._embedder.model:
            raise EmbeddingModelError(
                f"stored model {row['embedding_model']!r} does not match "
                f"embedder model {self._embedder.model!r}"
            )
        self._raise_dimension_mismatch(int(row["dimension"]))

    def _assert_candidate_identity(self, rows: list[sqlite3.Row]) -> None:
        for row in rows:
            stored_dimension = int(row["dimension"])
            self._raise_dimension_mismatch(stored_dimension)
            if row["embedding_model"] != self._embedder.model:
                raise EmbeddingModelError(
                    f"stored model {row['embedding_model']!r} does not match "
                    f"embedder model {self._embedder.model!r}"
                )
            stored_embedding = _decode(row["embedding"])
            if len(stored_embedding) != stored_dimension:
                raise EmbeddingDimensionError(
                    f"stored vector length {len(stored_embedding)} does not match "
                    f"stored dimension {stored_dimension}"
                )

    def _assert_embedding_dimension(self, embedding: tuple[float, ...]) -> None:
        if len(embedding) != self._embedder.dimension:
            raise EmbeddingDimensionError(
                f"embedder declared dimension {self._embedder.dimension} but "
                f"returned dimension {len(embedding)}"
            )

    def _raise_dimension_mismatch(self, stored_dimension: int) -> None:
        if stored_dimension != self._embedder.dimension:
            raise EmbeddingDimensionError(
                f"stored dimension {stored_dimension} does not match "
                f"embedder dimension {self._embedder.dimension}"
            )

    @staticmethod
    def _source_from_row(row: sqlite3.Row) -> SourceReference:
        heading_parts = json.loads(row["heading_path"])
        return SourceReference(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            document_title=row["document_title"],
            section=" > ".join(heading_parts),
            page=row["page"],
            source_path=row["source_path"],
            refetch_tool="get-chunk",
        )
