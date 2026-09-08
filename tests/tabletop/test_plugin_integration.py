"""Phase 7 runtime integration tests: built-ins, external roots, environment.

Proves built-in systems and third-party installed systems flow through the
exact same discovery/loading/registry pipeline, with no Omega or config
modification, and that the Phase 5 shallow system discovery is replaced.
"""

import os
from pathlib import Path

import pytest

from tabletop.api.errors import (
    DuplicatePluginError,
    PluginManifestError,
)
from tabletop.runtime import PLUGIN_PATH_ENV_VAR, TabletopRuntime

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_builtin_systems_load_through_generic_pipeline():
    runtime = TabletopRuntime(_REPO_ROOT)
    systems = {record["id"]: record for record in runtime.systems()}
    assert set(systems) == {"freeform", "dnd5e"}
    assert systems["freeform"]["name"] == "Freeform Reference System"
    assert systems["freeform"]["version"] == "0.1.0"
    assert systems["freeform"]["api_version"] == "tabletop/v1"
    assert systems["freeform"]["capabilities"] == []
    assert systems["dnd5e"]["capabilities"] == []
    assert runtime.system_capabilities("freeform") == []


def test_builtin_systems_are_registered_not_hardcoded():
    """No special-casing: the registry is populated purely by discovery."""
    runtime = TabletopRuntime(_REPO_ROOT)
    assert runtime._registry.contains("freeform")
    assert runtime._registry.contains("dnd5e")
    assert runtime.get_system("freeform")["id"] == "freeform"
    with pytest.raises(Exception, match="not registered"):
        runtime.get_system("nonexistent")


def test_external_plugin_loads_without_modifying_repo(tmp_path, make_plugin):
    make_plugin(
        tmp_path,
        "example-system",
        module_name="example_system",
        name="Example System",
    )
    runtime = TabletopRuntime(_REPO_ROOT, plugin_roots=[tmp_path])
    ids = {record["id"] for record in runtime.systems()}
    assert {"freeform", "dnd5e", "example-system"} <= ids
    record = runtime.get_system("example-system")
    assert record["name"] == "Example System"
    assert record["capabilities"] == []


def test_builtin_and_external_roots_coexist(tmp_path, make_plugin):
    make_plugin(tmp_path, "external-one")
    runtime = TabletopRuntime(_REPO_ROOT, plugin_roots=[tmp_path])
    ids = {record["id"] for record in runtime.systems()}
    assert ids == {"freeform", "dnd5e", "external-one"}


def test_duplicate_ids_fail_closed(tmp_path, make_plugin):
    # Different module name so the failure is the duplicate registration,
    # not a sys.modules cache collision (module names must be unique per
    # process; see discovery module docs).
    make_plugin(tmp_path, "freeform", module_name="my_freeform")
    with pytest.raises(DuplicatePluginError):
        TabletopRuntime(_REPO_ROOT, plugin_roots=[tmp_path])


def test_missing_configured_plugin_root_fails_startup(tmp_path):
    with pytest.raises(PluginManifestError, match="does not exist"):
        TabletopRuntime(_REPO_ROOT, plugin_roots=[tmp_path / "nowhere"])


def test_initialization_failure_fails_runtime_startup(tmp_path, make_plugin):
    make_plugin(tmp_path, "broken", init_body="raise RuntimeError('no resources')")
    with pytest.raises(Exception, match="failed during initialize"):
        TabletopRuntime(_REPO_ROOT, plugin_roots=[tmp_path])


def test_runtime_shutdown_collects_plugin_failures(tmp_path, make_plugin):
    runtime = TabletopRuntime(_REPO_ROOT, plugin_roots=[])
    result = runtime.shutdown()
    assert result["ok"] is True
    assert result["failures"] == []


def test_env_plugin_path_single_root(tmp_path, make_plugin):
    make_plugin(tmp_path, "env-plugin", module_name="env_plugin")
    environ = {PLUGIN_PATH_ENV_VAR: str(tmp_path)}
    runtime = TabletopRuntime.from_environment(_REPO_ROOT, environ=environ)
    ids = {record["id"] for record in runtime.systems()}
    assert {"freeform", "dnd5e", "env-plugin"} <= ids


def test_env_plugin_path_supports_multiple_roots(tmp_path, make_plugin):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    make_plugin(root_a, "from-a")
    make_plugin(root_b, "from-b")
    environ = {PLUGIN_PATH_ENV_VAR: os.pathsep.join([str(root_a), str(root_b)])}
    runtime = TabletopRuntime.from_environment(_REPO_ROOT, environ=environ)
    ids = {record["id"] for record in runtime.systems()}
    assert {"freeform", "dnd5e", "from-a", "from-b"} <= ids


def test_env_plugin_path_empty_falls_back_to_builtin():
    runtime = TabletopRuntime.from_environment(_REPO_ROOT, environ={PLUGIN_PATH_ENV_VAR: ""})
    ids = {record["id"] for record in runtime.systems()}
    assert ids == {"freeform", "dnd5e"}


def test_env_plugin_path_nonexistent_fails_startup(tmp_path):
    environ = {PLUGIN_PATH_ENV_VAR: str(tmp_path / "missing")}
    with pytest.raises(PluginManifestError, match="does not exist"):
        TabletopRuntime.from_environment(_REPO_ROOT, environ=environ)


def test_bootstrap_status_reports_registry_metadata():
    runtime = TabletopRuntime(_REPO_ROOT)
    payload = runtime.bootstrap_status()
    systems = payload["data"]["systems"]
    assert [record["id"] for record in systems] == ["dnd5e", "freeform"]
    assert all("capabilities" in record for record in systems)
    assert "plugin_roots" in payload["data"]
