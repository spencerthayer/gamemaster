"""Workspace-scoped Omega skill surface (task 35).

Forbidden operations are absent from the registered tool list, not merely
rejected when called. The active workspace is fixed at construction.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import re
from pathlib import Path

import pytest

from tabletop.api.workspace import Workspace
from tabletop.runtime import WORKSPACE_ENV_VAR, TabletopRuntime

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADAPTER_PATH = _REPO_ROOT / "plugins" / "tabletop" / "omega_tabletop_adapter.py"
_METTA_PATH = _REPO_ROOT / "plugins" / "tabletop" / "tabletop.metta"

# Capability categories that must not appear on a SETTING surface.
_SETTING_FORBIDDEN = frozenset(
    {
        "read-session",
        "mutate-quest",
        "get-party-state",
        "read-campaign-secret",
    }
)

_SETTING_REQUIRED = frozenset(
    {
        "query-setting",
        "edit-setting",
        "get-world-entity",
        "upsert-world-entity",
        "query-world-history",
        "record-world-history",
    }
)

_SETTING_READ = frozenset(
    {
        "query-setting",
        "get-world-entity",
        "query-world-history",
    }
)

_CAMPAIGN_EXTRA = frozenset(
    {
        "read-session",
        "end-session",
        "current-scene",
        "get-party-state",
        "get-open-threads",
        "mutate-quest",
        "read-campaign-secret",
        "current-campaign",
        "query-campaign",
        "query-rules",
        "resolve-action",
        "roll",
        "get-entity",
        "get-fact",
        "get-relationships",
        "record-ruling",
    }
)

_FORBIDDEN_PARAM_TOKENS = frozenset(
    {
        "sql",
        "table",
        "table_name",
        "path",
        "file",
        "filepath",
        "file_path",
        "filename",
    }
)


def _load_adapter():
    spec = importlib.util.spec_from_file_location(
        "omega_tabletop_adapter_workspace_test", _ADAPTER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _skill_names(workspace: Workspace) -> set[str]:
    return {skill.name for skill in workspace.skills}


def test_workspace_is_fixed_at_construction_from_environment(tmp_path: Path) -> None:
    runtime = TabletopRuntime.from_environment(
        _REPO_ROOT,
        environ={WORKSPACE_ENV_VAR: "setting", "TABLETOP_PLUGIN_PATH": ""},
    )

    assert runtime.workspace is Workspace.SETTING
    assert not hasattr(runtime, "set_workspace")
    public_names = {
        name
        for name, _ in inspect.getmembers(runtime, predicate=inspect.ismethod)
        if not name.startswith("_")
    }
    assert "set_workspace" not in public_names
    assert not any("workspace" in name and name.startswith("set") for name in public_names)
    with pytest.raises(AttributeError):
        runtime.workspace = Workspace.CAMPAIGN  # type: ignore[misc]


def test_unset_workspace_fails_startup_without_defaulting(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="TABLETOP_WORKSPACE"):
        TabletopRuntime.from_environment(tmp_path, environ={})


def test_unknown_workspace_fails_startup_without_defaulting(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown"):
        TabletopRuntime.from_environment(
            tmp_path,
            environ={WORKSPACE_ENV_VAR: "everything"},
        )


def test_setting_skill_set_contains_world_ops_and_excludes_campaign_secrets() -> None:
    names = _skill_names(Workspace.SETTING)

    assert _SETTING_REQUIRED <= names
    assert names.isdisjoint(_SETTING_FORBIDDEN)
    assert "read-session" not in names
    assert "mutate-quest" not in names
    assert "get-party-state" not in names
    assert "read-campaign-secret" not in names


def test_campaign_inherits_setting_read_and_adds_session_party_thread_campaign() -> None:
    setting_names = _skill_names(Workspace.SETTING)
    campaign_names = _skill_names(Workspace.CAMPAIGN)

    assert _SETTING_READ <= campaign_names
    assert "edit-setting" not in campaign_names
    assert "upsert-world-entity" not in campaign_names
    assert "record-world-history" not in campaign_names
    assert _CAMPAIGN_EXTRA <= campaign_names
    assert _SETTING_FORBIDDEN <= campaign_names
    assert setting_names - campaign_names == {
        "edit-setting",
        "upsert-world-entity",
        "record-world-history",
        "promote-world-fact",
        "reveal-world-fact",
    }


def test_absent_skill_is_missing_from_registration_payload_not_only_rejected() -> None:
    adapter = _load_adapter()
    adapter.reset_runtime_for_tests()
    adapter.reset_skill_registration_for_tests()

    runtime = TabletopRuntime(
        _REPO_ROOT,
        campaign_roots=[],
        plugin_roots=[],
        workspace=Workspace.SETTING,
    )
    adapter._RUNTIME = runtime

    payload = json.loads(adapter.skill_registration_payload())
    registered_names = {entry["name"] for entry in payload["skills"]}

    assert registered_names == _skill_names(Workspace.SETTING)
    assert "read-session" not in registered_names
    assert "get-party-state" not in registered_names
    assert "mutate-quest" not in registered_names
    assert "read-campaign-secret" not in registered_names
    assert "current-campaign" not in registered_names


def test_campaign_registration_payload_includes_campaign_tools() -> None:
    adapter = _load_adapter()
    adapter.reset_runtime_for_tests()
    adapter.reset_skill_registration_for_tests()

    runtime = TabletopRuntime(
        _REPO_ROOT,
        campaign_roots=[],
        plugin_roots=[],
        workspace=Workspace.CAMPAIGN,
    )
    adapter._RUNTIME = runtime

    payload = json.loads(adapter.skill_registration_payload())
    registered_names = {entry["name"] for entry in payload["skills"]}
    assert registered_names == _skill_names(Workspace.CAMPAIGN)
    assert _SETTING_READ <= registered_names
    assert _CAMPAIGN_EXTRA <= registered_names


def test_every_registered_skill_has_non_empty_description() -> None:
    for workspace in Workspace:
        for skill in workspace.skills:
            assert skill.description.strip(), skill.name


def test_no_skill_exposes_raw_sql_table_or_file_path_parameter() -> None:
    for workspace in Workspace:
        for skill in workspace.skills:
            for parameter in skill.parameters:
                token = parameter.lower().replace("-", "_")
                assert token not in _FORBIDDEN_PARAM_TOKENS, (
                    f"{skill.name} exposes forbidden parameter {parameter!r}"
                )
                assert "sql" not in token
                assert "table" not in token
                assert "path" not in token
                assert "file" not in token


def test_skills_map_to_existing_runtime_methods() -> None:
    runtime = TabletopRuntime(
        _REPO_ROOT,
        campaign_roots=[],
        plugin_roots=[],
        workspace=Workspace.CAMPAIGN,
    )
    for workspace in Workspace:
        for skill in workspace.skills:
            assert hasattr(runtime, skill.runtime_method), skill.runtime_method
            assert callable(getattr(runtime, skill.runtime_method))


def test_adapter_registers_skills_only_once() -> None:
    adapter = _load_adapter()
    adapter.reset_runtime_for_tests()
    adapter.reset_skill_registration_for_tests()
    adapter._RUNTIME = TabletopRuntime(
        _REPO_ROOT,
        campaign_roots=[],
        plugin_roots=[],
        workspace=Workspace.SETTING,
    )

    first = json.loads(adapter.begin_skill_registration())
    assert first["ok"] is True
    assert {entry["name"] for entry in first["skills"]} == _skill_names(
        Workspace.SETTING
    )

    second = json.loads(adapter.begin_skill_registration())
    assert second["ok"] is False
    assert second["error"]["code"] == "skills_already_registered"


def test_claim_skill_registration_makes_second_metta_path_a_noop() -> None:
    adapter = _load_adapter()
    adapter.reset_runtime_for_tests()
    adapter.reset_skill_registration_for_tests()
    adapter._RUNTIME = TabletopRuntime(
        _REPO_ROOT,
        campaign_roots=[],
        plugin_roots=[],
        workspace=Workspace.CAMPAIGN,
    )

    first = adapter.claim_skill_registration()
    assert first == "campaign"
    assert adapter.skills_already_registered() == "true"

    second = adapter.claim_skill_registration()
    assert second == "already-registered"

    text = _METTA_PATH.read_text()
    assert "(= (register-workspace-skills already-registered) ())" in text
    assert "claim_skill_registration" in text
    load_body = text.split("(= (loadOmegaPlugin)", 1)[1].split(
        "(= (register-workspace-skills already-registered)", 1
    )[0]
    assert "claim_skill_registration" in load_body
    assert "register-workspace-skills $workspace" in load_body


def test_active_workspace_returns_bare_setting_or_campaign_token() -> None:
    adapter = _load_adapter()
    adapter.reset_runtime_for_tests()
    adapter.reset_skill_registration_for_tests()
    adapter._RUNTIME = TabletopRuntime(
        _REPO_ROOT,
        campaign_roots=[],
        plugin_roots=[],
        workspace=Workspace.SETTING,
    )

    token = adapter.active_workspace()
    assert token in {"setting", "campaign"}
    assert token == "setting"
    assert not token.startswith("{")
    assert '"' not in token
    assert "ok" not in token

    adapter._RUNTIME = TabletopRuntime(
        _REPO_ROOT,
        campaign_roots=[],
        plugin_roots=[],
        workspace=Workspace.CAMPAIGN,
    )
    assert adapter.active_workspace() == "campaign"


def test_active_workspace_failure_is_bare_sentinel_not_json() -> None:
    adapter = _load_adapter()
    adapter.reset_runtime_for_tests()
    adapter.reset_skill_registration_for_tests()

    class Boom:
        @property
        def workspace(self):
            raise RuntimeError("nope")

    adapter._RUNTIME = Boom()
    token = adapter.active_workspace()
    assert token == "workspace-unavailable"
    assert not token.startswith("{")


def test_metta_skill_names_match_workspace_skill_specs() -> None:
    text = _METTA_PATH.read_text()
    setting_block = text.split("(= (register-workspace-skills setting)", 1)[1].split(
        "(= (register-workspace-skills campaign)", 1
    )[0]
    # Campaign block ends before the first skill implementation equation.
    campaign_block = text.split("(= (register-workspace-skills campaign)", 1)[1].split(
        "(= (register-workspace-skills player)", 1
    )[0]
    player_block = text.split("(= (register-workspace-skills player)", 1)[1].split(
        "(= (query-setting", 1
    )[0]

    setting_metta = re.findall(r"\(add-skill ([^\s)]+)", setting_block)
    campaign_metta = re.findall(r"\(add-skill ([^\s)]+)", campaign_block)
    player_metta = re.findall(r"\(add-skill ([^\s)]+)", player_block)

    assert setting_metta == [skill.name for skill in Workspace.SETTING.skills]
    assert campaign_metta == [skill.name for skill in Workspace.CAMPAIGN.skills]
    assert player_metta == [skill.name for skill in Workspace.PLAYER.skills]


def test_metta_registers_from_workspace_payload_not_a_flat_global_list() -> None:
    text = _METTA_PATH.read_text()
    assert "claim_skill_registration" in text
    assert "register-workspace-skills" in text

    load_body = text.split("(= (loadOmegaPlugin)", 1)[1].split(
        "(= (register-workspace-skills already-registered)", 1
    )[0]
    for forbidden in _SETTING_FORBIDDEN:
        assert f"(add-skill {forbidden}" not in load_body

    setting_block = text.split("(= (register-workspace-skills setting)", 1)[1].split(
        "(= (register-workspace-skills campaign)", 1
    )[0]
    for forbidden in _SETTING_FORBIDDEN:
        assert f"(add-skill {forbidden}" not in setting_block

    campaign_block = text.split("(= (register-workspace-skills campaign)", 1)[1]
    for required in _SETTING_FORBIDDEN:
        assert f"(add-skill {required}" in campaign_block

