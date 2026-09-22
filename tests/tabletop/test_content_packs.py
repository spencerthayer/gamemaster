"""Content-pack manifests describe data and can never execute pack code."""

from importlib import import_module, util
from pathlib import Path

import pytest


def _content_pack_api():
    spec = util.find_spec("tabletop.documents.content_pack")
    assert spec is not None, "content-pack manifest loader is not implemented"
    module = import_module("tabletop.documents.content_pack")
    assert callable(getattr(module, "load_content_pack", None))
    return module


def _write_manifest(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "content-pack.yaml"
    path.write_text(text)
    return path


def _valid_manifest(pack_type: str = "rules", gm_only: str = "") -> str:
    return (
        "id: core-rules\n"
        "name: Core Rules\n"
        f"pack_type: {pack_type}\n"
        "system_id: freeform\n"
        "version: 1.2.3\n"
        f"{gm_only}"
    )


@pytest.mark.parametrize(
    "pack_type",
    ["rules", "setting", "adventure", "campaign-seed", "supplement"],
)
def test_valid_content_pack_manifest_parses(tmp_path, pack_type):
    module = _content_pack_api()
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "notes.md").write_text("secret")
    _write_manifest(
        tmp_path,
        _valid_manifest(pack_type, "gm_only:\n  - secrets/notes.md\n"),
    )

    manifest = module.load_content_pack(tmp_path)

    assert manifest.id == "core-rules"
    assert manifest.name == "Core Rules"
    assert manifest.pack_type == pack_type
    assert manifest.system_id == "freeform"
    assert manifest.version == "1.2.3"
    assert manifest.gm_only == ("secrets/notes.md",)


def test_gm_only_is_optional(tmp_path):
    module = _content_pack_api()
    _write_manifest(tmp_path, _valid_manifest())

    manifest = module.load_content_pack(tmp_path)

    assert manifest.gm_only == ()


def test_unknown_fields_are_rejected(tmp_path):
    module = _content_pack_api()
    _write_manifest(tmp_path, _valid_manifest() + "description: unexpected\n")

    with pytest.raises(module.ContentPackError, match="unknown fields"):
        module.load_content_pack(tmp_path)


def test_entrypoint_is_rejected_as_non_executable(tmp_path):
    module = _content_pack_api()
    _write_manifest(tmp_path, _valid_manifest() + "entrypoint: pack_code:run\n")

    with pytest.raises(module.ContentPackError, match="entrypoint"):
        module.load_content_pack(tmp_path)


@pytest.mark.parametrize("pack_type", ["plugin", "", "RULES"])
def test_invalid_pack_type_is_rejected(tmp_path, pack_type):
    module = _content_pack_api()
    _write_manifest(tmp_path, _valid_manifest(pack_type))

    with pytest.raises(module.ContentPackError, match="pack_type"):
        module.load_content_pack(tmp_path)


def test_missing_required_field_is_rejected(tmp_path):
    module = _content_pack_api()
    _write_manifest(
        tmp_path,
        "id: core-rules\nname: Core Rules\npack_type: rules\nsystem_id: freeform\n",
    )

    with pytest.raises(module.ContentPackError, match="missing required"):
        module.load_content_pack(tmp_path)


def test_non_mapping_yaml_is_rejected(tmp_path):
    module = _content_pack_api()
    _write_manifest(tmp_path, "- just\n- a\n- list\n")

    with pytest.raises(module.ContentPackError, match="must be a mapping"):
        module.load_content_pack(tmp_path)


def test_yaml_object_tags_are_never_constructed(tmp_path):
    module = _content_pack_api()
    sentinel = tmp_path / "yaml-executed"
    _write_manifest(
        tmp_path,
        (
            f"id: !!python/object/apply:pathlib.Path.touch ['{sentinel}']\n"
            "name: Core Rules\n"
            "pack_type: rules\n"
            "system_id: freeform\n"
            "version: 1.2.3\n"
        ),
    )

    with pytest.raises(module.ContentPackError):
        module.load_content_pack(tmp_path)
    assert not sentinel.exists()


@pytest.mark.parametrize("gm_only", ["../outside.md", "/tmp/outside.md"])
def test_gm_only_lexical_escape_is_rejected(tmp_path, gm_only):
    module = _content_pack_api()
    _write_manifest(tmp_path, _valid_manifest(gm_only=f"gm_only:\n  - {gm_only}\n"))

    with pytest.raises(module.ContentPackError, match="outside pack directory"):
        module.load_content_pack(tmp_path)


def test_gm_only_symlink_escape_is_rejected(tmp_path):
    module = _content_pack_api()
    outside = tmp_path.parent / "outside-secret.md"
    outside.write_text("secret")
    (tmp_path / "linked-secret.md").symlink_to(outside)
    _write_manifest(
        tmp_path,
        _valid_manifest(gm_only="gm_only:\n  - linked-secret.md\n"),
    )

    with pytest.raises(module.ContentPackError, match="outside pack directory"):
        module.load_content_pack(tmp_path)


def test_loading_content_pack_does_not_import_python_from_pack(tmp_path):
    module = _content_pack_api()
    sentinel = tmp_path / "python-imported"
    (tmp_path / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('imported')\n"
    )
    _write_manifest(tmp_path, _valid_manifest())

    module.load_content_pack(tmp_path)

    assert not sentinel.exists(), "content-pack discovery executed Python code"
