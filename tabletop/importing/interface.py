"""Importer interface for external campaign material."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class ImportItem:
    kind: str
    payload: Mapping[str, Any]
    provenance: Mapping[str, Any] = field(default_factory=dict)
    proposed_key: str | None = None
    review: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ImportBatch:
    format_id: str
    source_label: str
    items: tuple[ImportItem, ...]
    report: Mapping[str, Any] = field(default_factory=dict)


class CampaignImporter(Protocol):
    def load(self, path: str) -> ImportBatch:
        """Load an external campaign file into staged proposals."""
