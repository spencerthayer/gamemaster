"""Contained access to separate raw and processed document roots."""

from __future__ import annotations

import hashlib
from pathlib import Path

from tabletop.documents.models import DocumentRecord


class DocumentLibrary:
    """Resolve immutable source documents and their derived-artifact paths."""

    def __init__(self, raw_root: Path, processed_root: Path) -> None:
        raw = Path(raw_root).resolve()
        processed = Path(processed_root).resolve()

        if not raw.is_dir():
            raise FileNotFoundError(f"raw document root does not exist: {raw}")
        if raw.is_relative_to(processed) or processed.is_relative_to(raw):
            raise ValueError("raw and processed document roots must not be nested")
        if processed.exists() and not processed.is_dir():
            raise NotADirectoryError(
                f"processed document root is not a directory: {processed}"
            )

        processed.mkdir(parents=True, exist_ok=True)
        self._raw_root = raw
        self._processed_root = processed

    def lookup(self, logical_path: str | Path) -> DocumentRecord:
        """Resolve a logical path under both roots and hash its raw bytes."""
        logical = Path(logical_path)
        if logical.is_absolute():
            raise ValueError(f"path is outside document root: {logical}")

        raw_path = self._contained_path(self._raw_root, logical)
        processed_path = self._contained_path(self._processed_root, logical)
        digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()

        return DocumentRecord(
            logical_path=logical,
            raw_path=raw_path,
            processed_path=processed_path,
            sha256=digest,
        )

    @staticmethod
    def _contained_path(root: Path, logical_path: Path) -> Path:
        path = (root / logical_path).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"path is outside document root: {logical_path}")
        return path
