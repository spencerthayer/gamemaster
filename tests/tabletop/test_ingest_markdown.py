"""Markdown and text ingestion preserves chunk context and provenance."""

from pathlib import Path

from tabletop.documents.ingest import DocumentIngestor, IngestContext
from tabletop.documents.markdown import MarkdownIngestor
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


def test_document_ingestor_protocol_exposes_required_operations():
    assert DocumentIngestor._is_protocol
    assert callable(DocumentIngestor.supports)
    assert callable(DocumentIngestor.ingest)


def test_ingests_markdown_with_nested_heading_paths_and_provenance(tmp_path):
    source = tmp_path / "travel.md"
    source.write_text(
        "# Travel\n\n"
        "Pace:\nTravel proceeds in turns.\n\n"
        "## Difficult Terrain\n\n"
        "Cost:\nEach foot costs one extra foot of movement.\n"
    )
    context = _context(tmp_path / "ingest.db")
    ingestor = MarkdownIngestor()

    assert ingestor.supports(source)
    document_id = ingestor.ingest(source, context)

    document = context.connection.execute(
        "SELECT * FROM documents WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    chunks = context.connection.execute(
        "SELECT * FROM document_chunks WHERE document_id = ? ORDER BY ordinal",
        (document_id,),
    ).fetchall()

    assert document["source_path"] == str(source)
    assert document["title"] == "Travel"
    assert document["document_shape"] == "structured_rules"
    assert [chunk["heading_path"] for chunk in chunks] == [
        '["Travel"]',
        '["Travel", "Difficult Terrain"]',
    ]
    for chunk in chunks:
        assert chunk["document_id"] == document_id
        assert chunk["content_hash"]
        assert chunk["page"] is None
        assert chunk["content_pack_id"] == "core-rules"
        assert chunk["system_id"] == "freeform"
        assert chunk["visibility"] == "GM"

    assert context.connection.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0


def test_ingestion_is_idempotent_by_content_hash(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("A plain text paragraph with enough words to identify prose content.\n")
    context = _context(tmp_path / "ingest.db")
    ingestor = MarkdownIngestor()

    first_id = ingestor.ingest(source, context)
    second_id = ingestor.ingest(source, context)

    assert first_id == second_id
    assert context.connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    assert (
        context.connection.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0]
        == 1
    )


def test_oversized_sections_split_at_paragraphs_without_splitting_fences(tmp_path):
    source = tmp_path / "examples.md"
    source.write_text(
        "# Examples\n\n"
        "First paragraph is long enough to consume most of a small chunk budget.\n\n"
        "```python\n"
        "result = calculate(\n"
        "    one_really_long_argument,\n"
        "    another_really_long_argument,\n"
        ")\n"
        "```\n\n"
        "Final paragraph belongs after the complete code example.\n"
    )
    context = _context(tmp_path / "ingest.db", max_chunk_chars=90)

    document_id = MarkdownIngestor().ingest(source, context)
    chunks = context.connection.execute(
        "SELECT text, heading_path FROM document_chunks "
        "WHERE document_id = ? ORDER BY ordinal",
        (document_id,),
    ).fetchall()

    assert len(chunks) >= 3
    assert all(chunk["heading_path"] == '["Examples"]' for chunk in chunks)
    fenced_chunks = [chunk["text"] for chunk in chunks if "```python" in chunk["text"]]
    assert len(fenced_chunks) == 1
    assert fenced_chunks[0].count("```") == 2
