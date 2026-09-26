"""Inspect classifies without executing; install registers data only."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.campaign.store import CampaignStore
from tabletop.documents.catalog import ContentCatalog
from tabletop.documents.content_install import (
    ContentError,
    ContentKind,
    inspect_content,
    install_document,
    install_pack,
    list_installed,
)
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "campaign-1"

_PACK_MANIFEST = """id: core-rules
name: Core Rules
pack_type: rules
system_id: freeform
version: 1.0.0
"""


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "content.db")
    migrate(connection)
    CampaignStore(connection).create_campaign(_CAMPAIGN, "First", "freeform")
    yield connection
    connection.close()


def _pack(tmp_path: Path) -> Path:
    root = tmp_path / "core-rules"
    root.mkdir(exist_ok=True)
    (root / "content-pack.yaml").write_text(_PACK_MANIFEST, encoding="utf-8")
    (root / "gates.md").write_text("Gate DC is 15.\n\nThe gate is rusted.", encoding="utf-8")
    return root


# -- inspection -------------------------------------------------------------


def test_a_content_pack_directory_is_classified(tmp_path: Path) -> None:
    result = inspect_content(_pack(tmp_path))
    assert result.kind is ContentKind.CONTENT_PACK
    assert result.manifest is not None
    assert result.manifest["id"] == "core-rules"


def test_a_plugin_directory_is_classified_as_a_system_plugin(
    tmp_path: Path,
) -> None:
    root = tmp_path / "myplugin"
    root.mkdir()
    (root / "plugin.yaml").write_text("id: myplugin\n", encoding="utf-8")
    assert inspect_content(root).kind is ContentKind.SYSTEM_PLUGIN


def test_a_markdown_file_is_classified_as_a_document(tmp_path: Path) -> None:
    doc = tmp_path / "notes.md"
    doc.write_text("some notes", encoding="utf-8")
    assert inspect_content(doc).kind is ContentKind.DOCUMENT


def test_a_pdf_is_classified_as_a_document(tmp_path: Path) -> None:
    doc = tmp_path / "rules.pdf"
    doc.write_bytes(b"%PDF-1.4\n")
    assert inspect_content(doc).kind is ContentKind.DOCUMENT


def test_a_python_file_is_classified_as_unsupported(tmp_path: Path) -> None:
    payload = tmp_path / "payload.py"
    payload.write_text("import os", encoding="utf-8")
    result = inspect_content(payload)
    assert result.kind is ContentKind.UNSUPPORTED
    assert "executable" in result.reason


def test_a_shell_script_is_classified_as_unsupported(tmp_path: Path) -> None:
    payload = tmp_path / "run.sh"
    payload.write_text("#!/bin/sh\n", encoding="utf-8")
    assert inspect_content(payload).kind is ContentKind.UNSUPPORTED


def test_an_unknown_extension_is_classified_as_unsupported(tmp_path: Path) -> None:
    other = tmp_path / "thing.xyz"
    other.write_text("data", encoding="utf-8")
    assert inspect_content(other).kind is ContentKind.UNSUPPORTED


def test_a_directory_with_no_manifest_is_unsupported(tmp_path: Path) -> None:
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert inspect_content(empty).kind is ContentKind.UNSUPPORTED


def test_inspecting_a_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(ContentError, match="does not exist"):
        inspect_content(tmp_path / "absent.md")


def test_inspection_writes_nothing(conn: sqlite3.Connection, tmp_path: Path) -> None:
    before = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    inspect_content(_pack(tmp_path))
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM installed_packs").fetchone()[0] == 0


# -- installation -----------------------------------------------------------


def test_installing_a_document_registers_its_bytes(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    doc = tmp_path / "rules.md"
    doc.write_text("Gate DC is 15.", encoding="utf-8")
    document_id = install_document(conn, doc, system_id="freeform")
    row = conn.execute(
        "SELECT source_path, system_id FROM documents WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    assert row["source_path"] == str(doc)
    assert row["system_id"] == "freeform"


def test_installing_the_same_bytes_twice_stores_one_document(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    doc = tmp_path / "rules.md"
    doc.write_text("Gate DC is 15.", encoding="utf-8")
    first = install_document(conn, doc)
    second = install_document(conn, doc)
    assert first == second
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


def test_installing_indexes_every_chunk(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """A document with no indexed chunks looks like an empty search, not a bug."""
    doc = tmp_path / "rules.md"
    doc.write_text("First paragraph.\n\nSecond paragraph.\n\nThird.", encoding="utf-8")
    document_id = install_document(conn, doc)
    chunks = conn.execute(
        "SELECT ordinal FROM document_chunks WHERE document_id = ? ORDER BY ordinal",
        (document_id,),
    ).fetchall()
    assert [row["ordinal"] for row in chunks] == [0, 1, 2]


def test_installation_does_not_attach_to_a_campaign(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    doc = tmp_path / "rules.md"
    doc.write_text("Gate DC is 15.", encoding="utf-8")
    install_document(conn, doc)
    assert ContentCatalog(conn).attached_documents(_CAMPAIGN) == []


def test_installing_an_executable_is_refused(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    payload = tmp_path / "payload.py"
    payload.write_text("import os", encoding="utf-8")
    with pytest.raises(ContentError, match="cannot install unsupported"):
        install_document(conn, payload)
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


def test_installing_a_plugin_directory_is_refused(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Only configured plugin roots may execute code, so a plugin is not content."""
    root = tmp_path / "myplugin"
    root.mkdir()
    (root / "plugin.yaml").write_text("id: myplugin\n", encoding="utf-8")
    with pytest.raises(ContentError, match="cannot install system_plugin"):
        install_pack(conn, root)


def test_installing_a_pack_registers_it_and_its_documents(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    pack_id = install_pack(conn, _pack(tmp_path))
    assert pack_id == "core-rules"
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] >= 1
    listed = list_installed(conn)
    assert listed[0]["pack_id"] == "core-rules"
    assert listed[0]["pack_type"] == "rules"


def test_installing_a_pack_does_not_attach_it(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    install_pack(conn, _pack(tmp_path))
    assert conn.execute("SELECT COUNT(*) FROM campaign_content_packs").fetchone()[0] == 0


def test_installing_a_pack_twice_is_idempotent(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    install_pack(conn, _pack(tmp_path))
    documents = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    install_pack(conn, _pack(tmp_path))
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == documents
    assert conn.execute("SELECT COUNT(*) FROM installed_packs").fetchone()[0] == 1


def test_a_symlinked_outside_document_is_still_content(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Inspection classifies by suffix; it does not chase symlinks anywhere."""
    outside = tmp_path.parent / "outside-content.md"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "linked.md"
    link.symlink_to(outside)
    document_id = install_document(conn, link)
    assert document_id
