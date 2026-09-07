"""Phase 5 contracts for the Omega tabletop adapter and runtime bootstrap."""

import importlib.util
import json
from pathlib import Path

import yaml

from tabletop.runtime import TabletopRuntime

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADAPTER_PATH = _REPO_ROOT / "plugins" / "tabletop" / "omega_tabletop_adapter.py"
_SKILLS = {
    "current-campaign",
    "current-scene",
    "query-rules",
    "query-campaign",
    "resolve-action",
    "roll",
    "get-entity",
    "get-relationships",
    "record-ruling",
    "end-session",
}


def _load_adapter():
    spec = importlib.util.spec_from_file_location("omega_tabletop_adapter_test", _ADAPTER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_discovery_is_shallow_and_non_executing(tmp_path):
    campaign_root = tmp_path / "campaigns"
    system_root = tmp_path / "systems"
    (campaign_root / "alpha").mkdir(parents=True)
    (campaign_root / ".hidden").mkdir()
    (system_root / "freeform").mkdir(parents=True)
    (system_root / "freeform" / "__init__.py").write_text(
        "raise RuntimeError('discovery must not import system plugins')"
    )
    (system_root / "not_a_plugin").mkdir()

    runtime = TabletopRuntime(
        tmp_path,
        campaign_roots=[campaign_root],
        system_roots=[system_root],
    )

    status = runtime.bootstrap_status()
    assert status["data"]["campaigns"] == ["alpha"]
    assert status["data"]["systems"] == ["freeform"]


def test_current_campaign_requires_explicit_choice_when_ambiguous(tmp_path):
    root = tmp_path / "campaigns"
    (root / "alpha").mkdir(parents=True)
    (root / "beta").mkdir()
    runtime = TabletopRuntime(tmp_path, campaign_roots=[root], system_roots=[])

    result = runtime.current_campaign()
    assert result["ok"] is False
    assert result["error"]["code"] == "campaign_selection_required"
    assert result["data"]["available"] == ["alpha", "beta"]


def test_future_phase_operation_is_explicitly_unavailable(tmp_path):
    runtime = TabletopRuntime(tmp_path, campaign_roots=[], system_roots=[])
    result = runtime.roll("2d6")

    assert result["ok"] is False
    assert result["operation"] == "roll"
    assert result["error"]["code"] == "capability_unavailable"
    assert result["data"]["planned_phase"] == 10
    assert result["data"]["input"] == {"expression": "2d6"}


def test_adapter_returns_json_and_contains_runtime_exceptions():
    adapter = _load_adapter()

    class BrokenRuntime:
        def roll(self, expression):
            raise RuntimeError("secret detail")

    adapter._RUNTIME = BrokenRuntime()
    payload = json.loads(adapter.roll("1d20"))

    assert payload["ok"] is False
    assert payload["error"]["code"] == "adapter_error"
    assert payload["error"]["exception_type"] == "RuntimeError"
    assert "secret detail" not in json.dumps(payload)


def test_adapter_rejects_unsupported_response_types():
    adapter = _load_adapter()

    class UnsafeRuntime:
        def current_scene(self):
            return object()

    adapter._RUNTIME = UnsafeRuntime()
    payload = json.loads(adapter.current_scene())
    assert payload["error"]["code"] == "adapter_error"
    assert payload["error"]["exception_type"] == "TypeError"


def test_metta_plugin_registers_all_tabletop_skills_and_prompt_extension():
    text = (_REPO_ROOT / "plugins" / "tabletop" / "tabletop.metta").read_text()
    assert "omega_tabletop_adapter.py" in text
    assert "(= (loadOmegaPlugin)" in text
    for skill in _SKILLS:
        assert f"(add-skill {skill}" in text
        assert f"(= ({skill}" in text
    assert "(add-prompt-extension tabletop-runtime-policy" in text


def test_plugin_config_uses_metta_loader():
    plugins = yaml.safe_load((_REPO_ROOT / "config" / "plugins.yaml").read_text())
    tabletop = [plugin for plugin in plugins if plugin["name"] == "tabletop"]
    assert tabletop == [
        {
            "name": "tabletop",
            "loader": "metta",
            "location": "{REPO}/plugins/tabletop",
        }
    ]
