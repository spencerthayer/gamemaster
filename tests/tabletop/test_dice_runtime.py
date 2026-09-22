import json
from pathlib import Path

from systems.freeform import FreeformPlugin
from tabletop.api.capabilities import Capability
from tabletop.api.workspace import Workspace
from tabletop.runtime import TabletopRuntime

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_roll_returns_json_safe_roll_result_envelope():
    payload = TabletopRuntime(_REPO_ROOT, workspace=Workspace.CAMPAIGN).roll("2d6+1")

    assert payload["ok"] is True
    assert payload["operation"] == "roll"
    assert set(payload["data"]) == {"expression", "total", "details"}
    assert payload["data"]["expression"] == "2d6+1"
    assert payload["data"]["total"] == sum(payload["data"]["details"]["kept"]) + 1
    assert json.loads(json.dumps(payload)) == payload


def test_runtime_roll_contains_malformed_expression_error():
    payload = TabletopRuntime(_REPO_ROOT, workspace=Workspace.CAMPAIGN).roll("not dice")

    assert payload["ok"] is False
    assert payload["operation"] == "roll"
    assert payload["error"]["code"] == "dice_expression_error"
    assert payload["data"] == {}


def test_freeform_advertises_dice_capability():
    assert Capability.DICE in FreeformPlugin().capabilities()
