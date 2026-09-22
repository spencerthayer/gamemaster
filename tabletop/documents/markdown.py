"""Markdown and plain-text ingestion with heading-aware chunking."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tabletop.documents.ingest import IngestContext
from tabletop.documents.shape import detect_shape
from tabletop.storage.sqlite import transaction

_SUPPORTED_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
_HEADING_PATTERN = re.compile(r"^\s{0,3}(#{1,6})[ \t]+(.+?)\s*$")
_OPEN_FENCE_PATTERN = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


@dataclass(frozen=True)
class _Section:
    heading_path: tuple[str, ...]
    text: str


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _heading_title(raw_title: str) -> str:
    return re.sub(r"[ \t]+#+[ \t]*$", "", raw_title).strip()


def _fence_marker(line: str) -> str | None:
    match = _OPEN_FENCE_PATTERN.match(line)
    return match.group(1) if match else None


def _closes_fence(line: str, opening: str) -> bool:
    stripped = line.lstrip()
    marker_character = opening[0]
    marker_length = len(opening)
    return bool(
        re.match(
            rf"^{re.escape(marker_character)}{{{marker_length},}}[ \t]*$",
            stripped,
        )
    )


def _sections(text: str, *, parse_headings: bool) -> list[_Section]:
    sections: list[_Section] = []
    heading_stack: list[tuple[int, str]] = []
    lines: list[str] = []
    open_fence: str | None = None

    def flush() -> None:
        section_text = "\n".join(lines).strip()
        if section_text:
            sections.append(
                _Section(
                    heading_path=tuple(title for _, title in heading_stack),
                    text=section_text,
                )
            )
        lines.clear()

    for line in text.splitlines():
        if open_fence is not None:
            lines.append(line)
            if _closes_fence(line, open_fence):
                open_fence = None
            continue

        fence = _fence_marker(line)
        if fence is not None:
            open_fence = fence
            lines.append(line)
            continue

        heading = _HEADING_PATTERN.match(line) if parse_headings else None
        if heading is None:
            lines.append(line)
            continue

        flush()
        level = len(heading.group(1))
        del heading_stack[
            next(
                (
                    index
                    for index, (existing_level, _) in enumerate(heading_stack)
                    if existing_level >= level
                ),
                len(heading_stack),
            ) :
        ]
        heading_stack.append((level, _heading_title(heading.group(2))))

    flush()
    return sections


def _paragraphs(section_text: str) -> list[str]:
    paragraphs: list[str] = []
    lines: list[str] = []
    open_fence: str | None = None

    def flush() -> None:
        paragraph = "\n".join(lines).strip()
        if paragraph:
            paragraphs.append(paragraph)
        lines.clear()

    for line in section_text.splitlines():
        if open_fence is not None:
            lines.append(line)
            if _closes_fence(line, open_fence):
                open_fence = None
            continue

        fence = _fence_marker(line)
        if fence is not None:
            open_fence = fence
            lines.append(line)
        elif line.strip():
            lines.append(line)
        else:
            flush()

    flush()
    return paragraphs


def _chunk_section(section: _Section, max_chars: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    for paragraph in _paragraphs(section.text):
        separator_length = 2 if current else 0
        if current and current_length + separator_length + len(paragraph) > max_chars:
            chunks.append("\n\n".join(current))
            current = []
            current_length = 0
            separator_length = 0
        current.append(paragraph)
        current_length += separator_length + len(paragraph)

    if current:
        chunks.append("\n\n".join(current))
    return chunks


class MarkdownIngestor:
    """Persist Markdown and plain text as provenance-rich retrieval chunks."""

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in _SUPPORTED_SUFFIXES

    def ingest(self, path: Path, context: IngestContext) -> str:
        if not self.supports(path):
            raise ValueError(f"unsupported document type: {path.suffix}")

        content = path.read_bytes()
        text = content.decode("utf-8")
        document_hash = _content_hash(content)
        connection = context.connection

        with transaction(connection):
            existing = connection.execute(
                "SELECT document_id FROM documents WHERE content_hash = ?",
                (document_hash,),
            ).fetchone()
            if existing is not None:
                return str(existing["document_id"])

            parsed_sections = _sections(
                text,
                parse_headings=path.suffix.lower() != ".txt",
            )
            title = (
                parsed_sections[0].heading_path[0]
                if parsed_sections and parsed_sections[0].heading_path
                else path.stem
            )
            document_id = f"doc-{document_hash}"
            connection.execute(
                "INSERT INTO documents "
                "(document_id, content_hash, source_path, title, document_shape, "
                "content_pack_id, system_id, visibility, ingested_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    document_hash,
                    str(path),
                    title,
                    detect_shape(text).shape.value,
                    context.content_pack_id,
                    context.system_id,
                    context.visibility,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

            ordinal = 0
            for section in parsed_sections:
                heading_path = json.dumps(section.heading_path)
                for chunk_text in _chunk_section(section, context.max_chunk_chars):
                    chunk_hash = _content_hash(chunk_text.encode("utf-8"))
                    connection.execute(
                        "INSERT INTO document_chunks "
                        "(chunk_id, document_id, ordinal, heading_path, page, text, "
                        "content_hash, content_pack_id, system_id, visibility) "
                        "VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)",
                        (
                            f"{document_id}-chunk-{ordinal}",
                            document_id,
                            ordinal,
                            heading_path,
                            chunk_text,
                            chunk_hash,
                            context.content_pack_id,
                            context.system_id,
                            context.visibility,
                        ),
                    )
                    ordinal += 1

        return document_id
