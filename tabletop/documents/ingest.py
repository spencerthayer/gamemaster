"""Shared document ingestion contract and context."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class IngestContext:
    """Database and provenance attached to each ingested chunk."""

    connection: sqlite3.Connection
    content_pack_id: str | None
    system_id: str | None
    visibility: str
    max_chunk_chars: int = 2_000

    def __post_init__(self) -> None:
        if self.max_chunk_chars <= 0:
            raise ValueError("max_chunk_chars must be positive")


class DocumentIngestor(Protocol):
    """Interface implemented by source-format ingestors."""

    def supports(self, path: Path) -> bool:
        """Return whether this ingestor accepts the source path."""

    def ingest(self, path: Path, context: IngestContext) -> str:
        """Persist a source document and return its document id."""
