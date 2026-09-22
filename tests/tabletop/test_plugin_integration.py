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
from tabletop.api.workspace import Workspace
from tabletop.runtime import PLUGIN_PATH_ENV_VAR, WORKSPACE_ENV_VAR, TabletopRuntime

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_builtin_systems_load_through_generic_pipeline():
    runtime = TabletopRuntime(_REPO_ROOT, workspace=Workspace.CAMPAIGN)
    systems = {record["id"]: record for record in runtime.systems()}
    freeform_capabilities = [
        "action-resolution",
        "dice",
        "opposed-resolution",
        "resource-tracking",
    ]
    assert set(systems) == {"freeform", "dnd5e", "gurps"}
    assert systems["freeform"]["name"] == "Freeform Reference System"
    assert systems["freeform"]["version"] == "0.1.0"
    assert systems["freeform"]["api_version"] == "tabletop/v1"
    assert systems["freeform"]["capabilities"] == freeform_capabilities
    dnd5e_capabilities = [
        "action-resolution",
        "conditions",
        "damage",
        "dice",
        "healing",
        "resource-tracking",
        "turn-order",
    ]
    assert systems["dnd5e"]["capabilities"] == dnd5e_capabilities
    assert runtime.system_capabilities("freeform") == freeform_capabilities


def test_builtin_systems_are_registered_not_hardcoded():
    """No special-casing: the registry is populated purely by discovery."""
    runtime = TabletopRuntime(_REPO_ROOT, workspace=Workspace.CAMPAIGN)
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
    runtime = TabletopRuntime(_REPO_ROOT, plugin_roots=[tmp_path], workspace=Workspace.CAMPAIGN)
    ids = {record["id"] for record in runtime.systems()}
    assert {"freeform", "dnd5e", "example-system"} <= ids
    record = runtime.get_system("example-system")
    assert record["name"] == "Example System"
    assert record["capabilities"] == []


def test_builtin_and_external_roots_coexist(tmp_path, make_plugin):
    make_plugin(tmp_path, "external-one")
    runtime = TabletopRuntime(_REPO_ROOT, plugin_roots=[tmp_path], workspace=Workspace.CAMPAIGN)
    ids = {record["id"] for record in runtime.systems()}
    assert ids == {"freeform", "dnd5e", "gurps", "external-one"}


def test_duplicate_ids_fail_closed(tmp_path, make_plugin):
    # Different module name so the failure is the duplicate registration,
    # not a sys.modules cache collision (module names must be unique per
    # process; see discovery module docs).
    make_plugin(tmp_path, "freeform", module_name="my_freeform")
    with pytest.raises(DuplicatePluginError):
        TabletopRuntime(_REPO_ROOT, plugin_roots=[tmp_path], workspace=Workspace.CAMPAIGN)


def test_missing_configured_plugin_root_fails_startup(tmp_path):
    with pytest.raises(PluginManifestError, match="does not exist"):
        TabletopRuntime(_REPO_ROOT, plugin_roots=[tmp_path / "nowhere"], workspace=Workspace.CAMPAIGN)


def test_initialization_failure_fails_runtime_startup(tmp_path, make_plugin):
    make_plugin(tmp_path, "broken", init_body="raise RuntimeError('no resources')")
    with pytest.raises(Exception, match="failed during initialize"):
        TabletopRuntime(_REPO_ROOT, plugin_roots=[tmp_path], workspace=Workspace.CAMPAIGN)


def test_runtime_shutdown_collects_plugin_failures(tmp_path, make_plugin):
    runtime = TabletopRuntime(_REPO_ROOT, plugin_roots=[], workspace=Workspace.CAMPAIGN)
    result = runtime.shutdown()
    assert result["ok"] is True
    assert result["failures"] == []


def test_env_plugin_path_single_root(tmp_path, make_plugin):
    make_plugin(tmp_path, "env-plugin", module_name="env_plugin")
    environ = {
        PLUGIN_PATH_ENV_VAR: str(tmp_path),
        WORKSPACE_ENV_VAR: "campaign",
    }
    runtime = TabletopRuntime.from_environment(_REPO_ROOT, environ=environ)
    ids = {record["id"] for record in runtime.systems()}
    assert {"freeform", "dnd5e", "env-plugin"} <= ids


def test_env_plugin_path_supports_multiple_roots(tmp_path, make_plugin):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    make_plugin(root_a, "from-a")
    make_plugin(root_b, "from-b")
    environ = {
        PLUGIN_PATH_ENV_VAR: os.pathsep.join([str(root_a), str(root_b)]),
        WORKSPACE_ENV_VAR: "campaign",
    }
    runtime = TabletopRuntime.from_environment(_REPO_ROOT, environ=environ)
    ids = {record["id"] for record in runtime.systems()}
    assert {"freeform", "dnd5e", "from-a", "from-b"} <= ids


def test_env_plugin_path_empty_falls_back_to_builtin():
    runtime = TabletopRuntime.from_environment(
        _REPO_ROOT,
        environ={PLUGIN_PATH_ENV_VAR: "", WORKSPACE_ENV_VAR: "campaign"},
    )
    ids = {record["id"] for record in runtime.systems()}
    assert ids == {"freeform", "dnd5e", "gurps"}


def test_env_plugin_path_nonexistent_fails_startup(tmp_path):
    environ = {
        PLUGIN_PATH_ENV_VAR: str(tmp_path / "missing"),
        WORKSPACE_ENV_VAR: "campaign",
    }
    with pytest.raises(PluginManifestError, match="does not exist"):
        TabletopRuntime.from_environment(_REPO_ROOT, environ=environ)


def test_bootstrap_status_reports_registry_metadata():
    runtime = TabletopRuntime(_REPO_ROOT, workspace=Workspace.CAMPAIGN)
    payload = runtime.bootstrap_status()
    systems = payload["data"]["systems"]
    assert [record["id"] for record in systems] == ["dnd5e", "freeform", "gurps"]
    assert all("capabilities" in record for record in systems)
    assert "plugin_roots" in payload["data"]
