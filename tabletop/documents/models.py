"""Document and chunk models."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DocumentRecord:
    """Resolved paths and byte identity for one source document."""

    logical_path: Path
    raw_path: Path
    processed_path: Path
    sha256: str
