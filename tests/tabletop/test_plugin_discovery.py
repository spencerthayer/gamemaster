"""Phase 7 discovery and loading tests.

The critical regression here: discovery and manifest validation never
execute plugin Python. Import happens only inside load_plugin.
"""

import sys

import pytest

from tabletop.api.errors import (
    PluginApiVersionError,
    PluginIdentityMismatchError,
    PluginLoadError,
    PluginManifestError,
)
from tabletop.api.plugin import GameSystemPlugin
from tabletop.plugins.discovery import DiscoveredPlugin, discover_plugins, load_plugin
from tabletop.plugins.manifest import PluginManifest, load_manifest


def _append_import_sentinel(plugin_dir: Path, module_name: str, sentinel: Path) -> None:
    module_file = plugin_dir / module_name / "__init__.py"
    module_file.write_text(
        module_file.read_text()
        + f"\nimport pathlib\npathlib.Path({str(sentinel)!r}).write_text('imported')\n"
    )


def test_immediate_child_discovered_and_nested_ignored(tmp_path, make_plugin):
    make_plugin(tmp_path, "alpha")
    make_plugin(tmp_path / "outer", "beta")  # nested: outer is not a plugin root

    candidates = discover_plugins([tmp_path])
    assert [candidate.manifest.id for candidate in candidates] == ["alpha"]


def test_directories_without_manifest_are_ignored(tmp_path, make_plugin):
    (tmp_path / "not-a-plugin").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "__pycache__").mkdir()
    make_plugin(tmp_path, "alpha")

    candidates = discover_plugins([tmp_path])
    assert [candidate.manifest.id for candidate in candidates] == ["alpha"]


def test_discovery_is_deterministic_regardless_of_creation_order(tmp_path, make_plugin):
    make_plugin(tmp_path, "zeta")
    make_plugin(tmp_path, "alpha")
    make_plugin(tmp_path, "mid")

    ids = [candidate.manifest.id for candidate in discover_plugins([tmp_path])]
    assert ids == ["alpha", "mid", "zeta"]


def test_symlink_escape_rejected(tmp_path, make_plugin):
    outside = tmp_path / "outside"
    outside.mkdir()
    make_plugin(outside, "evil")
    link = tmp_path / "link"
    link.symlink_to(outside / "evil", target_is_directory=True)

    with pytest.raises(PluginManifestError, match="outside configured root"):
        discover_plugins([tmp_path])


def test_discovery_does_not_execute_plugin_code(tmp_path, make_plugin):
    sentinel = tmp_path / "sentinel"
    make_plugin(tmp_path, "sideeffect")
    _append_import_sentinel(tmp_path / "sideeffect", "sideeffect", sentinel)

    candidates = discover_plugins([tmp_path])
    assert [candidate.manifest.id for candidate in candidates] == ["sideeffect"]
    assert not sentinel.exists(), "discovery executed plugin code"


def test_load_plugin_imports_only_at_load_time(tmp_path, make_plugin):
    sentinel = tmp_path / "sentinel"
    make_plugin(tmp_path, "sideeffect")
    _append_import_sentinel(tmp_path / "sideeffect", "sideeffect", sentinel)

    candidate = discover_plugins([tmp_path])[0]
    assert not sentinel.exists()

    plugin = load_plugin(candidate)
    assert sentinel.exists()
    assert isinstance(plugin, GameSystemPlugin)
    assert plugin.info.id == "sideeffect"


def test_incompatible_manifest_fails_before_import(tmp_path, make_plugin):
    """The pre-import validation regression test (spec section 32)."""
    sentinel = tmp_path / "sentinel"
    make_plugin(tmp_path, "badversion", api_version="tabletop/v0")
    _append_import_sentinel(tmp_path / "badversion", "badversion", sentinel)

    # Discovery itself fails closed on an incompatible manifest.
    with pytest.raises(PluginApiVersionError):
        discover_plugins([tmp_path])
    assert not sentinel.exists()

    # Even a hand-built candidate is rejected at load, before import.
    plugin_dir = tmp_path / "badversion"
    manifest = PluginManifest(
        id="badversion",
        name="badversion",
        api_version="tabletop/v0",
        version="0.1.0",
        entrypoint="badversion:TestPlugin",
    )
    with pytest.raises(PluginApiVersionError):
        load_plugin(DiscoveredPlugin(manifest=manifest, root=plugin_dir))
    assert not sentinel.exists(), "incompatible plugin executed Python"


def test_missing_entrypoint_module_rejected(tmp_path, make_plugin):
    make_plugin(tmp_path, "ghost", module_name="ghost_module")
    candidate_root = tmp_path / "ghost"
    candidate = discover_plugins([tmp_path])[0]
    # Remove the whole module directory: a bare directory would import as a
    # namespace package, which is not the failure under test.
    import shutil

    shutil.rmtree(candidate_root / "ghost_module")
    with pytest.raises(PluginLoadError, match="not found inside plugin directory"):
        load_plugin(candidate)


def test_missing_entrypoint_class_rejected(tmp_path, make_plugin):
    make_plugin(tmp_path, "nothere", manifest_overrides={"entrypoint": "nothere:Absent"})
    candidate = discover_plugins([tmp_path])[0]
    with pytest.raises(PluginLoadError, match="no class"):
        load_plugin(candidate)


def test_non_conforming_class_rejected(tmp_path, make_plugin):
    make_plugin(tmp_path, "loose")
    module_file = tmp_path / "loose" / "loose" / "__init__.py"
    module_file.write_text(
        "class TestPlugin:\n"
        "    def capabilities(self):\n"
        "        return frozenset()\n"
    )
    candidate = discover_plugins([tmp_path])[0]
    with pytest.raises(PluginLoadError, match="does not implement GameSystemPlugin"):
        load_plugin(candidate)


def test_identity_mismatch_rejected(tmp_path, make_plugin):
    make_plugin(tmp_path, "honest")
    candidate = discover_plugins([tmp_path])[0]
    lying = load_manifest(candidate.root / "plugin.yaml")
    object.__setattr__(lying, "name", "Impostor")  # bypass frozen for the test only
    with pytest.raises(PluginIdentityMismatchError, match="identity mismatch"):
        load_plugin(DiscoveredPlugin(manifest=lying, root=candidate.root))


def test_initialization_failure_propagates(tmp_path, make_plugin):
    make_plugin(tmp_path, "brokeninit", init_body="raise RuntimeError('boom')")
    candidate = discover_plugins([tmp_path])[0]
    with pytest.raises(Exception, match="failed during initialize"):
        load_plugin(candidate)


def test_import_paths_are_restored_after_loading(tmp_path, make_plugin):
    before = list(sys.path)
    make_plugin(tmp_path, "first")
    make_plugin(tmp_path, "second")
    for candidate in discover_plugins([tmp_path]):
        load_plugin(candidate)
    assert sys.path == before


def test_plugins_with_distinct_module_names_import_their_own_code(tmp_path, make_plugin):
    """Two plugins with different package names import their own packages.

    Documented limitation: two plugins sharing one top-level module name
    would collide in sys.modules for the process lifetime; module names must
    be globally unique within a process.
    """
    make_plugin(tmp_path, "plugin-one", module_name="pkg_one", class_name="One")
    make_plugin(tmp_path, "plugin-two", module_name="pkg_two", class_name="Two")
    candidates = discover_plugins([tmp_path])
    loaded = {load_plugin(candidate).info.id for candidate in candidates}
    assert loaded == {"plugin-one", "plugin-two"}
