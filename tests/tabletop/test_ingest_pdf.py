"""PDF ingestion uses the shared document chunk and provenance pipeline."""

import hashlib
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from tabletop.documents import pdf
from tabletop.documents.ingest import IngestContext
from tabletop.storage.sqlite import connect, migrate


def _context(database_path: Path, *, max_chunk_chars: int = 120) -> IngestContext:
    connection = connect(database_path)
    migrate(connection)
    return IngestContext(
        connection=connection,
        content_pack_id="core-rules",
        system_id="freeform",
        visibility="GM",
        max_chunk_chars=max_chunk_chars,
    )


def _write_pdf(path: Path, pages: list[str | None]) -> None:
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        if text is None:
            continue

        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font}
                )
            }
        )
        contents = DecodedStreamObject()
        escaped_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        contents.set_data(
            f"BT /F1 12 Tf 72 720 Td ({escaped_text}) Tj ET".encode("latin-1")
        )
        page[NameObject("/Contents")] = contents

    with path.open("wb") as output:
        writer.write(output)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_ingests_pdf_pages_with_shared_shape_detection_and_provenance(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "rules.pdf"
    _write_pdf(
        source,
        [
            "Initiative:",
            "Cover:",
        ],
    )
    original_hash = _sha256(source)
    context = _context(tmp_path / "ingest.db")
    detected_text: list[str] = []
    real_detect_shape = pdf.detect_shape

    def record_detect_shape(text: str):
        detected_text.append(text)
        return real_detect_shape(text)

    monkeypatch.setattr(pdf, "detect_shape", record_detect_shape)

    document_id = pdf.PdfIngestor().ingest(source, context)

    document = context.connection.execute(
        "SELECT * FROM documents WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    chunks = context.connection.execute(
        "SELECT * FROM document_chunks WHERE document_id = ? ORDER BY ordinal",
        (document_id,),
    ).fetchall()

    assert detected_text == [
        "Initiative:\n\nCover:"
    ]
    assert document["document_shape"] == "structured_rules"
    assert [(chunk["page"], chunk["text"]) for chunk in chunks] == [
        (1, "Initiative:"),
        (2, "Cover:"),
    ]
    assert all(chunk["heading_path"] == "[]" for chunk in chunks)
    assert all(chunk["content_pack_id"] == "core-rules" for chunk in chunks)
    assert all(chunk["system_id"] == "freeform" for chunk in chunks)
    assert all(chunk["visibility"] == "GM" for chunk in chunks)
    assert _sha256(source) == original_hash


def test_pdf_without_extractable_text_requires_manual_review(tmp_path):
    source = tmp_path / "scan.pdf"
    _write_pdf(source, [None])
    context = _context(tmp_path / "ingest.db")

    document_id = pdf.PdfIngestor().ingest(source, context)

    document = context.connection.execute(
        "SELECT document_shape FROM documents WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    chunk_count = context.connection.execute(
        "SELECT COUNT(*) FROM document_chunks WHERE document_id = ?",
        (document_id,),
    ).fetchone()[0]

    assert document["document_shape"] == "unsupported"
    assert chunk_count == 0
