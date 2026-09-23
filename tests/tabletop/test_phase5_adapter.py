"""Phase 5 contracts for the Omega tabletop adapter and runtime bootstrap."""

import importlib.util
import json
import re
import sqlite3
from pathlib import Path

import yaml

from tabletop.api.workspace import Workspace
from tabletop.campaign.store import CampaignStore
from tabletop.runtime import DATABASE_PATH_ENV_VAR, TabletopRuntime
from tabletop.storage.sqlite import connect, migrate

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADAPTER_PATH = _REPO_ROOT / "plugins" / "tabletop" / "omega_tabletop_adapter.py"
_ENTRYPOINT_PATH = _REPO_ROOT / "entrypoint.sh"
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


def test_bootstrap_campaign_discovery_stays_shallow(tmp_path):
    """Campaign directory discovery is a Phase 5 placeholder; dirs without
    manifests are ignored by the plugin pipeline (Phase 7 replaced shallow
    *system* discovery only)."""
    campaign_root = tmp_path / "campaigns"
    (campaign_root / "alpha").mkdir(parents=True)
    (campaign_root / ".hidden").mkdir()
    plugin_root = tmp_path / "systems"
    (plugin_root / "not-a-plugin").mkdir(parents=True)

    runtime = TabletopRuntime(
        tmp_path,
        campaign_roots=[campaign_root],
        plugin_roots=[plugin_root],
        workspace=Workspace.CAMPAIGN,
    )

    status = runtime.bootstrap_status()
    assert status["data"]["campaigns"] == ["alpha"]


def test_current_campaign_requires_explicit_choice_when_ambiguous(tmp_path):
    database = tmp_path / "campaign.db"
    conn = connect(database)
    migrate(conn)
    store = CampaignStore(conn)
    store.create_campaign("alpha", "Alpha", "freeform")
    store.create_campaign("beta", "Beta", "freeform")
    runtime = TabletopRuntime(
        tmp_path,
        campaign_roots=[],
        plugin_roots=[],
        workspace=Workspace.CAMPAIGN,
        connection=conn,
    )

    result = runtime.current_campaign()
    assert result["ok"] is False
    assert result["error"]["code"] == "campaign_selection_required"
    assert result["data"]["available"] == ["alpha", "beta"]
    runtime.shutdown()
    conn.close()


def test_current_campaign_uses_sole_sqlite_row(tmp_path):
    database = tmp_path / "campaign.db"
    conn = connect(database)
    migrate(conn)
    CampaignStore(conn).create_campaign("only", "Only", "freeform")
    (tmp_path / "campaigns" / "directory-only").mkdir(parents=True)
    runtime = TabletopRuntime(
        tmp_path,
        campaign_roots=[tmp_path / "campaigns"],
        plugin_roots=[],
        workspace=Workspace.CAMPAIGN,
        connection=conn,
    )
    result = runtime.current_campaign()
    assert result["ok"] is True
    assert result["data"]["campaign"] == "only"
    runtime.shutdown()
    conn.close()


def test_current_campaign_none_configured_without_rows(tmp_path):
    database = tmp_path / "campaign.db"
    conn = connect(database)
    migrate(conn)
    runtime = TabletopRuntime(
        tmp_path,
        campaign_roots=[],
        plugin_roots=[],
        workspace=Workspace.CAMPAIGN,
        connection=conn,
    )
    result = runtime.current_campaign()
    assert result["ok"] is False
    assert result["error"]["code"] == "campaign_not_configured"
    runtime.shutdown()
    conn.close()


def test_roll_operation_is_available(tmp_path):
    runtime = TabletopRuntime(
        tmp_path,
        campaign_roots=[],
        plugin_roots=[],
        workspace=Workspace.CAMPAIGN,
    )
    result = runtime.roll("2d6")

    assert result["ok"] is True
    assert result["operation"] == "roll"
    assert result["data"]["expression"] == "2d6"


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


def test_metta_plugin_registers_workspace_skills_and_prompt_extension():
    text = (_REPO_ROOT / "plugins" / "tabletop" / "tabletop.metta").read_text()
    assert "omega_tabletop_adapter.py" in text
    assert "(= (loadOmegaPlugin)" in text
    assert "(= (register-workspace-skills setting)" in text
    assert "(= (register-workspace-skills campaign)" in text
    assert "claim_skill_registration" in text
    for skill in _SKILLS:
        assert f"(add-skill {skill}" in text
        assert f"(= ({skill}" in text
    assert "(prompt-extension tabletop-allocated-context)" in text
    assert "add-prompt-extension" not in text


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


def test_entrypoint_preserves_tabletop_runtime_environment():
    entrypoint = _ENTRYPOINT_PATH.read_text()
    match = re.search(r'SAFE_VARS="(?P<vars>.*?)"\n\n', entrypoint, re.DOTALL)

    assert match is not None
    allowlist = set(match.group("vars").replace("\\\n", "").split())
    assert {
        "TABLETOP_WORKSPACE",
        "TABLETOP_CAMPAIGN",
        "TABLETOP_CAMPAIGN_PATHS",
        "TABLETOP_PLUGIN_PATH",
        "TABLETOP_DATABASE_PATH",
    } <= allowlist


def test_entrypoint_makes_existing_tabletop_state_writable_before_privilege_drop():
    entrypoint = _ENTRYPOINT_PATH.read_text()

    mkdir = 'mkdir -p -- "$TABLETOP_STATE_DIRECTORY"'
    chown = 'chown -R 65534:65534 -- "$TABLETOP_STATE_DIRECTORY"'
    first_privilege_drop = min(
        entrypoint.index("su www-data"),
        entrypoint.index("su nobody"),
    )

    assert mkdir in entrypoint
    assert chown in entrypoint
    assert entrypoint.index(mkdir) < entrypoint.index(chown) < first_privilege_drop


def test_entrypoint_creates_tabletop_data_before_privilege_drop():
    entrypoint = _ENTRYPOINT_PATH.read_text()
    mkdir = 'mkdir -p -- "$TABLETOP_DATA_DIRECTORY"'
    chown = 'chown 65534:65534 -- "$TABLETOP_DATA_DIRECTORY"'
    first_privilege_drop = min(
        entrypoint.index("su www-data"),
        entrypoint.index("su nobody"),
    )

    assert "TABLETOP_DATA_DIRECTORY=/PeTTa/repos/Omega/tabletop/data" in entrypoint
    assert entrypoint.index(mkdir) < entrypoint.index(chown) < first_privilege_drop


def test_from_environment_creates_and_migrates_configured_database(tmp_path):
    (tmp_path / "systems").mkdir()
    database_path = tmp_path / "state" / "tabletop.sqlite3"

    runtime = TabletopRuntime.from_environment(
        tmp_path,
        environ={
            "TABLETOP_WORKSPACE": "campaign",
            DATABASE_PATH_ENV_VAR: str(database_path),
        },
    )

    assert database_path.is_file()
    assert runtime.query_setting("") == {
        "ok": True,
        "operation": "query-setting",
        "data": {"settings": [], "query": ""},
    }
    with sqlite3.connect(database_path) as conn:
        migration_count = conn.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0]
    assert migration_count > 0
