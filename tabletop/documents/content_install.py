"""Classify and install content without executing it.

``inspect_content`` answers what a path is before anything is written:
a system plugin, a content pack, a document, or unsupported. It reads and
classifies. It never installs, never attaches, and never imports.

Installation registers data and reference artifacts. A path that looks
executable is refused outright, because only configured plugin roots may
execute code.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from tabletop.api.errors import ContentPackError
from tabletop.documents.catalog import CatalogError, ContentCatalog
from tabletop.storage.sqlite import transaction

#: Suffixes that imply runnable content. Refused by install and classified
#: unsupported by inspect.
_EXECUTABLE_SUFFIXES = frozenset(
    {".py", ".sh", ".bash", ".exe", ".dll", ".so", ".dylib", ".bat", ".ps1", ".jar"}
)

#: Suffixes install accepts as documents.
_DOCUMENT_SUFFIXES = frozenset({".md", ".txt", ".pdf"})

_PACK_MANIFEST_NAMES = ("content-pack.yaml", "content_pack.yaml")


class ContentKind(str, Enum):
    """What a path is."""

    SYSTEM_PLUGIN = "system_plugin"
    CONTENT_PACK = "content_pack"
    DOCUMENT = "document"
    UNSUPPORTED = "unsupported"


class ContentError(RuntimeError):
    """A content operation was refused."""


@dataclass(frozen=True)
class Inspection:
    """What one path is, and why."""

    path: Path
    kind: ContentKind
    reason: str
    manifest: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "kind": self.kind.value,
            "reason": self.reason,
            "manifest": self.manifest,
        }


def content_hash(path: Path) -> str:
    """Content-address a file. Identical bytes give an identical hash."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_content(path: Path) -> Inspection:
    """Classify one path. Reads and writes nothing, executes nothing."""
    resolved = Path(path).expanduser()
    if not resolved.exists():
        raise ContentError(f"path does not exist: {path}")
    if resolved.is_dir():
        return _inspect_directory(resolved)
    if resolved.suffix.lower() in _EXECUTABLE_SUFFIXES:
        return Inspection(
            resolved,
            ContentKind.UNSUPPORTED,
            "executable content is never installed or executed",
        )
    if resolved.suffix.lower() in _DOCUMENT_SUFFIXES:
        return Inspection(
            resolved, ContentKind.DOCUMENT, f"{resolved.suffix.lower()} document"
        )
    return Inspection(
        resolved,
        ContentKind.UNSUPPORTED,
        f"unsupported file type {resolved.suffix or '(none)'}",
    )


def _inspect_directory(resolved: Path) -> Inspection:
    if (resolved / "plugin.yaml").is_file():
        return Inspection(
            resolved,
            ContentKind.SYSTEM_PLUGIN,
            "a system plugin; it belongs to a configured plugin root, "
            "not to the content catalog",
        )
    for name in _PACK_MANIFEST_NAMES:
        manifest_path = resolved / name
        if manifest_path.is_file():
            from tabletop.documents.content_pack import load_content_pack

            try:
                manifest = load_content_pack(resolved)
            except ContentPackError as exc:
                return Inspection(resolved, ContentKind.UNSUPPORTED, str(exc))
            return Inspection(
                resolved,
                ContentKind.CONTENT_PACK,
                f"{manifest.pack_type} pack {manifest.id} {manifest.version}",
                manifest={
                    "id": manifest.id,
                    "name": manifest.name,
                    "pack_type": manifest.pack_type,
                    "system_id": manifest.system_id,
                    "version": manifest.version,
                },
            )
    return Inspection(
        resolved,
        ContentKind.UNSUPPORTED,
        "directory has no content pack manifest and is not a plugin root",
    )


def install_document(
    conn,
    path: Path,
    *,
    system_id: str | None = None,
    visibility: str = "GM",
    title: str | None = None,
) -> str:
    """Register one document's bytes. Returns its document id.

    Installing makes the bytes available. It does not attach the document to
    any campaign, so it cannot change what a campaign can see.
    """
    inspection = inspect_content(path)
    if inspection.kind is not ContentKind.DOCUMENT:
        raise ContentError(
            f"cannot install {inspection.kind.value}: {inspection.reason}"
        )
    source = Path(path).expanduser()
    digest = content_hash(source)
    existing = conn.execute(
        "SELECT document_id FROM documents WHERE content_hash = ?", (digest,)
    ).fetchone()
    if existing is not None:
        # Same bytes, already installed. Return the existing record rather
        # than storing a second copy.
        return str(existing["document_id"])
    document_id = f"doc-{digest[:16]}"
    with transaction(conn):
        conn.execute(
            "INSERT INTO documents (document_id, content_hash, source_path, title, "
            "document_shape, system_id, visibility, ingested_at) "
            "VALUES (?, ?, ?, ?, 'prose', ?, ?, ?)",
            (
                document_id,
                digest,
                str(source),
                title or source.stem,
                system_id,
                visibility,
                datetime.now(timezone.utc).replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
            ),
        )
        _index_chunks(conn, document_id, source, digest, system_id, visibility)
    return document_id


def _index_chunks(
    conn,
    document_id: str,
    source: Path,
    digest: str,
    system_id: str | None,
    visibility: str,
) -> None:
    """Split a text document into chunks and index every one.

    Ingestion that skips chunk indexing leaves retrieval silently empty, which
    looks like a search problem rather than a missing index.
    """
    if source.suffix.lower() == ".pdf":
        return
    text = source.read_text(encoding="utf-8", errors="replace")
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    for ordinal, paragraph in enumerate(paragraphs):
        chunk_id = f"{document_id}-{ordinal}"
        conn.execute(
            "INSERT INTO document_chunks "
            "(chunk_id, document_id, ordinal, heading_path, page, text, content_hash, "
            "content_pack_id, system_id, visibility) "
            "VALUES (?, ?, ?, ?, NULL, ?, ?, NULL, ?, ?)",
            (
                chunk_id,
                document_id,
                ordinal,
                "",
                paragraph,
                hashlib.sha256(paragraph.encode("utf-8")).hexdigest(),
                system_id,
                visibility,
            ),
        )


def install_pack(conn, path: Path) -> str:
    """Register a content pack and its documents. Returns the pack id."""
    inspection = inspect_content(path)
    if inspection.kind is not ContentKind.CONTENT_PACK:
        raise ContentError(
            f"cannot install {inspection.kind.value}: {inspection.reason}"
        )
    manifest = inspection.manifest or {}
    resolved = Path(path).expanduser()
    manifest_hash = content_hash(resolved / "content-pack.yaml")
    catalog = ContentCatalog(conn)
    try:
        pack = catalog.require_pack(str(manifest["id"]))
    except CatalogError:
        pack = catalog.install_pack(
            str(manifest["id"]),
            str(manifest["name"]),
            str(manifest["pack_type"]),
            manifest_hash,
            str(resolved),
            system_id=manifest.get("system_id"),
            version=manifest.get("version"),
        )
    for document in sorted(resolved.rglob("*")):
        if document.is_file() and document.suffix.lower() in _DOCUMENT_SUFFIXES:
            install_document(
                conn, document, system_id=manifest.get("system_id")
            )
    return pack.pack_id


def list_installed(conn) -> list[dict[str, Any]]:
    return [pack.to_dict() for pack in ContentCatalog(conn).list_packs()]
