"""Observable retrieval cascade, never an opaque fused score.

The order is semantic, then FTS5 lexical, then unavailable. Results identify
the tier that answered and every tier skipped on the way down the cascade.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from tabletop.retrieval.interface import Retriever
from tabletop.retrieval.models import RetrievalFilters, RetrievedChunk
from tabletop.retrieval.vector import EmbeddingDimensionError, EmbeddingModelError


class RetrievalTier(str, Enum):
    """One stage in the retrieval degrade cascade."""

    SEMANTIC = "semantic"
    LEXICAL = "lexical"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SkippedTier:
    """A tier bypassed because its backend could not safely answer."""

    tier: RetrievalTier
    reason: str


@dataclass(frozen=True)
class CascadeResult:
    """Search hits plus explicit cascade provenance."""

    chunks: tuple[RetrievedChunk, ...]
    answered_by: RetrievalTier
    skipped_tiers: tuple[SkippedTier, ...]


class CascadeRetriever:
    """Try semantic retrieval, degrade to lexical, then report unavailable."""

    def __init__(
        self,
        *,
        vector: Retriever | None,
        lexical: Retriever | None,
    ) -> None:
        self._vector = vector
        self._lexical = lexical

    def search(
        self,
        query: str,
        filters: RetrievalFilters,
        limit: int,
    ) -> CascadeResult:
        skipped: list[SkippedTier] = []

        if self._vector is None:
            skipped.append(
                SkippedTier(
                    tier=RetrievalTier.SEMANTIC,
                    reason="embedder unavailable",
                )
            )
        else:
            try:
                chunks = self._vector.search(query, filters, limit)
            except (EmbeddingDimensionError, EmbeddingModelError) as exc:
                skipped.append(
                    SkippedTier(
                        tier=RetrievalTier.SEMANTIC,
                        reason=str(exc),
                    )
                )
            else:
                return CascadeResult(
                    chunks=chunks,
                    answered_by=RetrievalTier.SEMANTIC,
                    skipped_tiers=(),
                )

        if self._lexical is not None:
            return CascadeResult(
                chunks=self._lexical.search(query, filters, limit),
                answered_by=RetrievalTier.LEXICAL,
                skipped_tiers=tuple(skipped),
            )

        skipped.append(
            SkippedTier(
                tier=RetrievalTier.LEXICAL,
                reason="FTS5 unavailable",
            )
        )
        return CascadeResult(
            chunks=(),
            answered_by=RetrievalTier.UNAVAILABLE,
            skipped_tiers=tuple(skipped),
        )
