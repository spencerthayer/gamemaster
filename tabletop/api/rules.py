"""Rule references.

``RuleReference`` is a transport type for citing a mechanical source.
Retrieval, persistence, precedence, and source-version resolution live in
``tabletop.retrieval``. This module only carries citation fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tabletop.api._contract import optional_non_empty_str, require_non_empty_str
from tabletop.api.errors import InvalidResolutionError


@dataclass(frozen=True)
class RuleReference:
    """Citation of a rule source used during resolution.

    This is not a retrieval result. Lookup, RAG, campaign rulings, and
    document verification belong to the retrieval layer.
    """

    source_id: str
    title: str | None = None
    section: str | None = None
    page: int | None = None
    document_path: str | None = None
    chunk_id: str | None = None

    def __post_init__(self) -> None:
        require_non_empty_str(self.source_id, "source_id", InvalidResolutionError)
        optional_non_empty_str(self.title, "title", InvalidResolutionError)
        optional_non_empty_str(self.section, "section", InvalidResolutionError)
        optional_non_empty_str(
            self.document_path, "document_path", InvalidResolutionError
        )
        optional_non_empty_str(self.chunk_id, "chunk_id", InvalidResolutionError)
        if self.page is not None and (
            isinstance(self.page, bool) or not isinstance(self.page, int)
        ):
            raise InvalidResolutionError(
                f"page must be an int or None, got {type(self.page).__name__}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "section": self.section,
            "page": self.page,
            "document_path": self.document_path,
            "chunk_id": self.chunk_id,
        }
