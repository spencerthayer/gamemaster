"""PDF ingestion with page-aware provenance."""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

from tabletop.documents.ingest import IngestContext
from tabletop.documents.markdown import _chunk_section, _content_hash, _sections
from tabletop.documents.shape import detect_shape
from tabletop.storage.sqlite import transaction


class PdfIngestor:
    """Extract PDF pages into the shared document chunk pipeline."""

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def ingest(self, path: Path, context: IngestContext) -> str:
        if not self.supports(path):
            raise ValueError(f"unsupported document type: {path.suffix}")

        content = path.read_bytes()
        document_hash = _content_hash(content)
        page_texts = [
            (page.extract_text() or "").strip()
            for page in PdfReader(io.BytesIO(content)).pages
        ]
        extracted_text = "\n\n".join(text for text in page_texts if text)
        detection = detect_shape(extracted_text)
        document_id = f"doc-{document_hash}"
        connection = context.connection

        with transaction(connection):
            existing = connection.execute(
                "SELECT document_id FROM documents WHERE content_hash = ?",
                (document_hash,),
            ).fetchone()
            if existing is not None:
                return str(existing["document_id"])

            connection.execute(
                "INSERT INTO documents "
                "(document_id, content_hash, source_path, title, document_shape, "
                "content_pack_id, system_id, visibility, ingested_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    document_hash,
                    str(path),
                    path.stem,
                    detection.shape.value,
                    context.content_pack_id,
                    context.system_id,
                    context.visibility,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

            ordinal = 0
            for page_number, page_text in enumerate(page_texts, start=1):
                if not page_text:
                    continue
                for section in _sections(page_text, parse_headings=False):
                    heading_path = json.dumps(section.heading_path)
                    for chunk_text in _chunk_section(
                        section,
                        context.max_chunk_chars,
                    ):
                        chunk_hash = _content_hash(chunk_text.encode("utf-8"))
                        connection.execute(
                            "INSERT INTO document_chunks "
                            "(chunk_id, document_id, ordinal, heading_path, page, "
                            "text, content_hash, content_pack_id, system_id, "
                            "visibility) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                f"{document_id}-chunk-{ordinal}",
                                document_id,
                                ordinal,
                                heading_path,
                                page_number,
                                chunk_text,
                                chunk_hash,
                                context.content_pack_id,
                                context.system_id,
                                context.visibility,
                            ),
                        )
                        ordinal += 1

        return document_id
