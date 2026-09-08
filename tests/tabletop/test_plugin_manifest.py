"""Phase 7 manifest parsing tests: strict, safe, API-version enforcing."""

import pytest

from tabletop.api.errors import PluginApiVersionError, PluginManifestError
from tabletop.plugins.manifest import load_manifest

VALID_MANIFEST = """\
id: freeform
name: Freeform
api_version: tabletop/v1
version: 0.1.0
entrypoint: freeform:FreeformPlugin
"""


def write_manifest(tmp_path, text: str):
    path = tmp_path / "plugin.yaml"
    path.write_text(text)
    return path


def test_valid_manifest_parses(tmp_path):
    manifest = load_manifest(write_manifest(tmp_path, VALID_MANIFEST))
    assert manifest.id == "freeform"
    assert manifest.name == "Freeform"
    assert manifest.api_version == "tabletop/v1"
    assert manifest.version == "0.1.0"
    assert manifest.entrypoint == "freeform:FreeformPlugin"


def test_missing_required_field_rejected(tmp_path):
    text = "id: freeform\nname: Freeform\napi_version: tabletop/v1\n"
    with pytest.raises(PluginManifestError, match="missing required"):
        load_manifest(write_manifest(tmp_path, text))


def test_malformed_yaml_rejected(tmp_path):
    with pytest.raises(PluginManifestError, match="malformed YAML"):
        load_manifest(write_manifest(tmp_path, "id: [unclosed\n  bad"))


def test_non_mapping_yaml_rejected(tmp_path):
    with pytest.raises(PluginManifestError, match="must be a mapping"):
        load_manifest(write_manifest(tmp_path, "- just\n- a\n- list\n"))


def test_invalid_id_rejected(tmp_path):
    text = VALID_MANIFEST.replace("id: freeform", "id: Bad Id")
    with pytest.raises(PluginManifestError, match="not a valid system id"):
        load_manifest(write_manifest(tmp_path, text))


def test_incompatible_api_version_rejected(tmp_path):
    text = VALID_MANIFEST.replace("api_version: tabletop/v1", "api_version: tabletop/v2")
    with pytest.raises(PluginApiVersionError):
        load_manifest(write_manifest(tmp_path, text))


def test_unknown_fields_rejected_so_typed_config_fails_loudly(tmp_path):
    text = VALID_MANIFEST + "capabilites:\n  - dice\n"
    with pytest.raises(PluginManifestError, match="unknown fields"):
        load_manifest(write_manifest(tmp_path, text))


@pytest.mark.parametrize(
    "entrypoint",
    [
        "../../plugin:Thing",
        "/abs/path:Thing",
        "foo:factory()",
        ":NoModule",
        "module:",
    ],
)
def test_malformed_entrypoints_rejected(tmp_path, entrypoint):
    text = VALID_MANIFEST.replace(
        "entrypoint: freeform:FreeformPlugin", f"entrypoint: {entrypoint}"
    )
    with pytest.raises(PluginManifestError, match="entrypoint"):
        load_manifest(write_manifest(tmp_path, text))


@pytest.mark.parametrize("entrypoint", ["freeform:FreeformPlugin", "my_system.plugin:MySystem"])
def test_valid_entrypoint_shapes_accepted(tmp_path, entrypoint):
    text = VALID_MANIFEST.replace(
        "entrypoint: freeform:FreeformPlugin", f"entrypoint: {entrypoint}"
    )
    manifest = load_manifest(write_manifest(tmp_path, text))
    assert manifest.entrypoint == entrypoint


def test_yaml_object_tags_are_never_constructed(tmp_path):
    text = (
        "id: !!python/object/apply:os.system ['touch /tmp/pwned']\n"
        "name: x\napi_version: tabletop/v1\nentrypoint: a:B\n"
    )
    with pytest.raises(PluginManifestError):
        load_manifest(write_manifest(tmp_path, text))
