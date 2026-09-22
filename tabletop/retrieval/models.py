"""Retrieval result models.

Retrieved chunks carry namespace, filters applied, and provenance so any
retrieved rule can be cited back to its source document.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class RetrievalNamespace(str, Enum):
    """Isolated retrieval corpora. Setting and campaign stay separate."""

    SYSTEM = "system"
    SETTING = "setting"
    ADVENTURE = "adventure"
    CAMPAIGN = "campaign"
    RULINGS = "rulings"
    CHARACTER = "character"
    NPC = "npc"


@dataclass(frozen=True)
class SourceReference:
    """Refetchable citation for one retrieved record.

    ``refetch_tool`` and ``refetch_args`` are enough to load the full record
    again. Document title, section, page, and path support GM-facing citations.
    """

    chunk_id: str
    document_id: str
    document_title: str
    section: str
    page: int | None
    source_path: str
    refetch_tool: str = "get_document_chunk"

    def __post_init__(self) -> None:
        if not self.chunk_id:
            raise ValueError("chunk_id must be non-empty")
        if not self.document_id:
            raise ValueError("document_id must be non-empty")
        if not self.refetch_tool:
            raise ValueError("refetch_tool must be non-empty")

    @property
    def refetch_args(self) -> Mapping[str, str]:
        return MappingProxyType({"chunk_id": self.chunk_id})


@dataclass(frozen=True)
class RetrievalFilters:
    """Namespace plus optional metadata predicates applied at search time."""

    namespace: RetrievalNamespace
    content_pack_id: str | None = None
    system_id: str | None = None
    visibility: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, RetrievalNamespace):
            raise TypeError(
                f"namespace must be RetrievalNamespace, got {type(self.namespace).__name__}"
            )


@dataclass(frozen=True)
class RetrievedChunk:
    """One lexical or semantic hit with score, namespace, and source handle."""

    text: str
    score: float
    namespace: RetrievalNamespace
    source: SourceReference

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, RetrievalNamespace):
            raise TypeError(
                f"namespace must be RetrievalNamespace, got {type(self.namespace).__name__}"
            )
        if not isinstance(self.source, SourceReference):
            raise TypeError(
                f"source must be SourceReference, got {type(self.source).__name__}"
            )
