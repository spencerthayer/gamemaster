"""Retriever interface.

``Retriever.search(query, filters, limit)`` with metadata filtering and
namespace selection (system, setting, adventure, campaign, rulings,
character, npc).
"""

from __future__ import annotations

from typing import Protocol

from tabletop.retrieval.models import RetrievalFilters, RetrievedChunk


class Retriever(Protocol):
    """Search contract shared by lexical, vector, and cascade backends."""

    def search(
        self,
        query: str,
        filters: RetrievalFilters,
        limit: int,
    ) -> tuple[RetrievedChunk, ...]:
        """Return ranked chunks for ``query`` under ``filters``, capped by ``limit``."""
