"""Raw documents stay separate from contained processed-document paths."""

import hashlib
from pathlib import Path

import pytest

from tabletop.documents.library import DocumentLibrary


def _library(tmp_path: Path) -> tuple[DocumentLibrary, Path, Path]:
    raw_root = tmp_path / "raw"
    processed_root = tmp_path / "processed"
    raw_root.mkdir()
    return DocumentLibrary(raw_root, processed_root), raw_root, processed_root


def test_lookup_resolves_one_logical_document_under_both_roots(tmp_path):
    library, raw_root, processed_root = _library(tmp_path)
    source = raw_root / "rules" / "combat.md"
    source.parent.mkdir()
    source.write_bytes(b"attack and defense")

    document = library.lookup("rules/combat.md")

    assert document.logical_path == Path("rules/combat.md")
    assert document.raw_path == source.resolve()
    assert document.processed_path == (processed_root / "rules/combat.md").resolve()
    assert document.sha256 == hashlib.sha256(b"attack and defense").hexdigest()


@pytest.mark.parametrize("logical_path", ["../outside.md", "/tmp/outside.md"])
def test_lookup_rejects_lexical_escape_from_document_roots(tmp_path, logical_path):
    library, _, _ = _library(tmp_path)

    with pytest.raises(ValueError, match="outside document root"):
        library.lookup(logical_path)


@pytest.mark.parametrize("escaped_root", ["raw", "processed"])
def test_lookup_rejects_symlink_escape_from_each_root(tmp_path, escaped_root):
    library, raw_root, processed_root = _library(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    (raw_root / "linked.md").write_text("raw")

    root = raw_root if escaped_root == "raw" else processed_root
    (root / "linked.md").unlink(missing_ok=True)
    (root / "linked.md").symlink_to(outside)

    with pytest.raises(ValueError, match="outside document root"):
        library.lookup("linked.md")


def test_missing_raw_root_fails_during_construction(tmp_path):
    with pytest.raises(FileNotFoundError, match="raw document root"):
        DocumentLibrary(tmp_path / "missing", tmp_path / "processed")


@pytest.mark.parametrize("processed_location", ["inside", "outside"])
def test_document_roots_cannot_be_nested(tmp_path, processed_location):
    if processed_location == "inside":
        raw_root = tmp_path / "raw"
        processed_root = raw_root / "processed"
    else:
        processed_root = tmp_path / "processed"
        raw_root = processed_root / "raw"

    raw_root.mkdir(parents=True)

    with pytest.raises(ValueError, match="must not be nested"):
        DocumentLibrary(raw_root, processed_root)


def test_library_has_no_raw_mutation_api(tmp_path):
    library, _, _ = _library(tmp_path)

    public_methods = {
        name
        for name in dir(library)
        if not name.startswith("_") and callable(getattr(library, name))
    }

    assert public_methods == {"lookup"}
