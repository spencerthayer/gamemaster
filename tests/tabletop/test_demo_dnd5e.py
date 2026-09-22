"""End-to-end demonstration of the partial D&D 5e 2014 plugin."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
import yaml

from tabletop.api.resolution import RollResult
from tabletop.api.workspace import Workspace
from tabletop.campaign.event_store import EventStore, EventType
from tabletop.campaign.store import CampaignStore
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate


_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE = _REPO_ROOT / "examples" / "campaigns" / "dnd5e-demo"
_CAMPAIGN_ID = "dnd5e-demo"
_SYSTEM_VOCABULARY = re.compile(
    r"\b(?:armor class|saving throws?|hit points?|spell slots?)\b",
    re.IGNORECASE,
)


def _action(
    actor: str,
    action_type: str,
    *,
    targets: tuple[str, ...] = (),
    **parameters: object,
) -> str:
    return json.dumps(
        {
            "actor": {"id": actor},
            "action_type": action_type,
            "targets": [{"id": target} for target in targets],
            "parameters": parameters,
        }
    )


def _without_docstrings(source: str) -> str:
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not body or not isinstance(body, list):
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            for index in range(first.lineno - 1, first.end_lineno or first.lineno):
                lines[index] = "\n" if lines[index].endswith("\n") else ""
    return "".join(lines)


def test_dnd5e_example_campaign_runs_combat_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    definition = yaml.safe_load((_EXAMPLE / "campaign.yaml").read_text())
    assert definition["system_id"] == "dnd5e"
    assert definition["rules_revision"] == 2014

    rolls = iter([18, 12, 19, 14, 5])

    def fake_roll(expression: str, rng=None) -> RollResult:
        return RollResult(
            expression=expression,
            total=next(rolls),
            details={"rolls": [], "kept": [], "dropped": [], "modifier": 0},
        )

    monkeypatch.setattr("systems.dnd5e.roller.roll", fake_roll)

    connection = connect(tmp_path / "dnd5e-demo.sqlite3")
    migrate(connection)
    store = CampaignStore(connection)
    store.create_campaign(
        _CAMPAIGN_ID,
        definition["name"],
        definition["system_id"],
        system_state=definition["initial_state"],
    )
    for entity_id, entity in definition["entities"].items():
        store.upsert_entity(
            _CAMPAIGN_ID,
            entity_id,
            entity["name"],
            entity_type=entity["entity_type"],
            system_state=entity["system_state"],
        )

    runtime = TabletopRuntime(
        _REPO_ROOT,
        workspace=Workspace.CAMPAIGN,
        campaign_roots=[_EXAMPLE.parent],
        active_campaign=_CAMPAIGN_ID,
        connection=connection,
    )

    initiative = runtime.resolve_action(
        _action(
            "hero",
            "roll_initiative",
            targets=("hero", "goblin"),
        )
    )
    attack = runtime.resolve_action(
        _action(
            "hero",
            "attack",
            targets=("goblin",),
            attack_bonus=5,
            advantage=True,
        )
    )
    damage = runtime.resolve_action(
        _action("hero", "apply_damage", targets=("goblin",), amount=4)
    )
    condition = runtime.resolve_action(
        _action(
            "hero",
            "apply_condition",
            targets=("goblin",),
            condition="poisoned",
        )
    )
    saving_throw = runtime.resolve_action(
        _action("hero", "saving_throw", ability="dexterity", dc=12)
    )
    short_rest = runtime.resolve_action(
        _action("hero", "short_rest", hit_dice_spent=1, hit_die_faces=8)
    )
    unsupported = runtime.resolve_action(_action("hero", "cast_spell"))

    resolved = [
        initiative,
        attack,
        damage,
        condition,
        saving_throw,
        short_rest,
    ]
    assert all(
        result["data"]["resolution"]["status"] == "resolved"
        for result in resolved
    )
    assert initiative["data"]["resolution"]["outcome"]["initiative_order"] == [
        "hero",
        "goblin",
    ]
    assert attack["data"]["resolution"]["rolls"][0]["expression"] == "2d20kh1+5"
    assert attack["data"]["resolution"]["outcome"]["attack_result"] == "hit"
    assert (
        saving_throw["data"]["resolution"]["outcome"]["save_result"]
        == "success"
    )
    assert (
        short_rest["data"]["resolution"]["outcome"]["hit_points_recovered"]
        == 5
    )
    assert unsupported["data"]["resolution"]["status"] == "unsupported"
    assert unsupported["data"]["resolution"]["outcome"] == {}
    assert unsupported["data"]["event"] is None
    assert unsupported["data"]["adjudication"]["status"] == "unsupported"

    campaign = store.get_campaign(_CAMPAIGN_ID)
    hero = store.get_entity(_CAMPAIGN_ID, "hero")
    goblin = store.get_entity(_CAMPAIGN_ID, "goblin")
    assert campaign is not None
    assert hero is not None
    assert goblin is not None
    assert campaign["system_state"]["initiative_order"] == ["hero", "goblin"]
    assert hero["system_state"]["hit_points"] == 17
    assert hero["system_state"]["resources"]["hit_dice"] == 1
    assert goblin["system_state"]["hit_points"] == 3
    assert goblin["system_state"]["conditions"] == ["poisoned"]

    events = EventStore(connection).read(_CAMPAIGN_ID)
    assert len(events) == len(resolved)
    assert all(
        event.event_type == EventType.ACTION_RESOLVED.value
        for event in events
    )
    assert [event.payload["action"]["action_type"] for event in events] == [
        "roll_initiative",
        "attack",
        "apply_damage",
        "apply_condition",
        "saving_throw",
        "short_rest",
    ]
    assert events[1].payload["rolls"][0]["expression"] == "2d20kh1+5"
    assert events[2].payload["state_changes"][0]["value"] == 3
    assert events[3].payload["state_changes"][0]["value"] == ["poisoned"]

    runtime.shutdown()
    connection.close()


def test_tabletop_api_has_no_live_dnd5e_vocabulary() -> None:
    api_root = _REPO_ROOT / "tabletop" / "api"
    matches: list[str] = []
    for path in sorted(api_root.rglob("*.py")):
        source = _without_docstrings(path.read_text())
        if match := _SYSTEM_VOCABULARY.search(source):
            matches.append(f"{path.relative_to(_REPO_ROOT)}:{match.group(0)}")

    assert matches == [], f"D&D 5e vocabulary leaked into live API code: {matches}"
