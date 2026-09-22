"""Behavior tests for the partial D&D 5e (2014) reference plugin."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from systems.dnd5e import Dnd5ePlugin
from tabletop.api.actions import GameAction
from tabletop.api.capabilities import Capability
from tabletop.api.entities import EntityRef
from tabletop.api.resolution import (
    ResolutionContext,
    ResolutionStatus,
    RollResult,
    StateOperation,
)
from tabletop.plugins.manifest import load_manifest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN_DIR = _REPO_ROOT / "systems" / "dnd5e"

# Snake-case identifiers that must not appear as API surface in tabletop/api.
# Prose mentions of the same ideas in boundary docstrings are allowed.
_FIVE_E_API_LEAKS = (
    "armor_class",
    "hit_points",
    "saving_throw",
    "spell_slot",
    "ability_check",
    "proficiency_bonus",
    "challenge_rating",
    "death_save",
)


def _action(action_type: str, actor: str = "hero", **parameters: object) -> GameAction:
    return GameAction(
        actor=EntityRef(id=actor),
        action_type=action_type,
        parameters=parameters,
    )


def _targeted(
    action_type: str,
    target: str,
    actor: str = "hero",
    **parameters: object,
) -> GameAction:
    return GameAction(
        actor=EntityRef(id=actor),
        action_type=action_type,
        targets=(EntityRef(id=target),),
        parameters=parameters,
    )


def _entity_system(**overrides: object) -> dict[str, object]:
    system: dict[str, object] = {
        "armor_class": 15,
        "hit_points": 20,
        "max_hit_points": 20,
        "speed": 30,
        "position": {"x": 0, "y": 0},
        "conditions": [],
        "abilities": {
            "strength": 16,
            "dexterity": 14,
            "constitution": 12,
            "intelligence": 10,
            "wisdom": 8,
            "charisma": 13,
        },
        "resources": {"hit_dice": 3},
    }
    system.update(overrides)
    return system


def _hero_state(
    *,
    hero: dict[str, object] | None = None,
    goblin: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "entities": {
            "hero": {"system": hero or _entity_system()},
            "goblin": {
                "system": goblin
                or _entity_system(
                    armor_class=13,
                    hit_points=7,
                    max_hit_points=7,
                    position={"x": 5, "y": 0},
                    abilities={
                        "strength": 8,
                        "dexterity": 14,
                        "constitution": 10,
                        "intelligence": 10,
                        "wisdom": 8,
                        "charisma": 8,
                    },
                    resources={"hit_dice": 1},
                )
            },
        }
    }


def _context(
    state: dict[str, object] | None = None,
    *,
    scene_id: str | None = None,
) -> ResolutionContext:
    return ResolutionContext(
        campaign_id="campaign-1",
        system_id="dnd5e",
        scene_id=scene_id,
        state=state or _hero_state(),
    )


def _patch_rolls(monkeypatch: pytest.MonkeyPatch, totals: list[int]) -> None:
    remaining = iter(totals)

    def fake_roll(expression: str, rng=None) -> RollResult:
        return RollResult(
            expression=expression,
            total=next(remaining),
            details={"rolls": [], "kept": [], "dropped": [], "modifier": 0},
        )

    monkeypatch.setattr("systems.dnd5e.roller.roll", fake_roll)


def test_manifest_declares_2014_rules_revision_only():
    manifest = load_manifest(_PLUGIN_DIR / "plugin.yaml")
    assert manifest.id == "dnd5e"
    assert manifest.description is not None
    assert "2014" in manifest.description
    assert "2024" not in manifest.description

    raw = yaml.safe_load((_PLUGIN_DIR / "plugin.yaml").read_text())
    text = yaml.safe_dump(raw)
    assert "2014" in text
    assert "2024" not in text


def test_advertises_only_implemented_capabilities():
    assert Dnd5ePlugin().capabilities() == frozenset(
        {
            Capability.DICE,
            Capability.ACTION_RESOLUTION,
            Capability.TURN_ORDER,
            Capability.DAMAGE,
            Capability.HEALING,
            Capability.CONDITIONS,
            Capability.RESOURCE_TRACKING,
        }
    )
    assert not Dnd5ePlugin().supports(Capability.MAGIC)


def test_ability_check_resolves_against_dc():
    result = Dnd5ePlugin().resolve(
        _action("ability_check", ability="strength", dc=10),
        _context(),
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert len(result.rolls) == 1
    roll = result.rolls[0]
    assert roll.expression == "1d20+3"
    assert result.outcome["check_result"] in {"success", "failure"}
    assert result.outcome["roll_total"] == roll.total
    assert result.outcome["dc"] == 10
    assert result.explanation


def test_saving_throw_resolves_against_dc():
    result = Dnd5ePlugin().resolve(
        _action("saving_throw", ability="dexterity", dc=12),
        _context(),
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.rolls[0].expression == "1d20+2"
    assert result.outcome["save_result"] in {"success", "failure"}
    assert result.outcome["dc"] == 12
    assert result.explanation


def test_attack_roll_compares_total_to_target_armor_class():
    result = Dnd5ePlugin().resolve(
        _targeted("attack", target="goblin", attack_bonus=5),
        _context(),
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.rolls[0].expression == "1d20+5"
    assert result.outcome["attack_result"] in {"hit", "miss"}
    assert result.outcome["target_armor_class"] == 13
    assert result.explanation


def test_advantage_uses_generic_keep_highest_expression():
    result = Dnd5ePlugin().resolve(
        _action("ability_check", ability="strength", dc=10, advantage=True),
        _context(),
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.rolls[0].expression == "2d20kh1+3"
    assert "2d20kh1" in result.rolls[0].expression


def test_disadvantage_uses_generic_keep_lowest_expression():
    result = Dnd5ePlugin().resolve(
        _action("ability_check", ability="strength", dc=10, disadvantage=True),
        _context(),
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.rolls[0].expression == "2d20kl1+3"


def test_advantage_and_disadvantage_cancel_to_a_normal_roll():
    result = Dnd5ePlugin().resolve(
        _action(
            "ability_check",
            ability="strength",
            dc=10,
            advantage=True,
            disadvantage=True,
        ),
        _context(),
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.rolls[0].expression == "1d20+3"


def test_damage_returns_hit_point_state_change_without_mutating_context():
    state = _hero_state()
    context = _context(state)

    result = Dnd5ePlugin().resolve(
        _targeted("apply_damage", target="goblin", amount=4),
        context,
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.outcome == {
        "damage_result": "applied",
        "amount": 4,
        "previous_hit_points": 7,
        "new_hit_points": 3,
    }
    assert len(result.state_changes) == 1
    change = result.state_changes[0]
    assert change.operation is StateOperation.SET
    assert change.path == ("entities", "goblin", "system", "hit_points")
    assert change.value == 3
    assert context.state["entities"]["goblin"]["system"]["hit_points"] == 7
    assert state["entities"]["goblin"]["system"]["hit_points"] == 7


def test_basic_condition_application_returns_state_change():
    result = Dnd5ePlugin().resolve(
        _targeted("apply_condition", target="goblin", condition="poisoned"),
        _context(),
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.outcome["condition"] == "poisoned"
    assert result.state_changes[0].path == (
        "entities",
        "goblin",
        "system",
        "conditions",
    )
    assert result.state_changes[0].value == ("poisoned",)


def test_unknown_basic_condition_is_unsupported():
    result = Dnd5ePlugin().resolve(
        _targeted("apply_condition", target="goblin", condition="exhaustion"),
        _context(),
    )

    assert result.status is ResolutionStatus.UNSUPPORTED
    assert result.explanation
    assert result.outcome == {}
    assert result.state_changes == ()


def test_initiative_orders_participants_by_roll_total(monkeypatch):
    _patch_rolls(monkeypatch, [18, 12])
    result = Dnd5ePlugin().resolve(
        GameAction(
            actor=EntityRef(id="hero"),
            action_type="roll_initiative",
            targets=(EntityRef(id="hero"), EntityRef(id="goblin")),
            parameters={},
        ),
        _context(),
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert len(result.rolls) == 2
    assert {roll.expression for roll in result.rolls} == {"1d20+2"}
    order = result.outcome["initiative_order"]
    assert order == ("hero", "goblin")
    change = result.state_changes[0]
    assert change.path == ("campaign", "system", "initiative_order")
    assert change.value == order


def test_initiative_writes_scene_path_when_scene_present(monkeypatch):
    _patch_rolls(monkeypatch, [10, 5])
    result = Dnd5ePlugin().resolve(
        GameAction(
            actor=EntityRef(id="hero"),
            action_type="roll_initiative",
            targets=(EntityRef(id="hero"), EntityRef(id="goblin")),
            parameters={},
        ),
        _context(scene_id="scene-1"),
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.state_changes[0].path == ("scene", "system", "initiative_order")


def test_initiative_tie_requires_ruling_not_dexterity_order(monkeypatch):
    _patch_rolls(monkeypatch, [15, 15])
    result = Dnd5ePlugin().resolve(
        GameAction(
            actor=EntityRef(id="hero"),
            action_type="roll_initiative",
            targets=(EntityRef(id="hero"), EntityRef(id="goblin")),
            parameters={},
        ),
        _context(),
    )

    assert result.status is ResolutionStatus.RULING_REQUIRED
    assert result.ruling_question
    assert "tied" in result.ruling_question.lower() or "tie" in result.ruling_question.lower()
    assert result.state_changes == ()
    assert len(result.rolls) == 2
    assert set(result.outcome["tied_participants"]) == {"hero", "goblin"}


def test_simple_movement_returns_position_state_change():
    result = Dnd5ePlugin().resolve(
        _action("move", destination={"x": 10, "y": 0}),
        _context(),
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.outcome["distance"] == 10
    assert result.state_changes[0].path == (
        "entities",
        "hero",
        "system",
        "position",
    )
    assert result.state_changes[0].value == {"x": 10, "y": 0}


def test_movement_beyond_speed_is_unresolved():
    result = Dnd5ePlugin().resolve(
        _action("move", destination={"x": 40, "y": 0}),
        _context(),
    )

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.explanation
    assert result.state_changes == ()


def test_short_rest_recovers_hit_points_via_hit_dice():
    state = _hero_state(hero=_entity_system(hit_points=12))
    result = Dnd5ePlugin().resolve(
        _action("short_rest", hit_dice_spent=1, hit_die_faces=8),
        _context(state),
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert len(result.rolls) == 1
    assert result.rolls[0].expression == "1d8+1"
    paths = {change.path for change in result.state_changes}
    assert ("entities", "hero", "system", "hit_points") in paths
    assert ("entities", "hero", "system", "resources", "hit_dice") in paths
    hit_dice_change = next(
        change
        for change in result.state_changes
        if change.path == ("entities", "hero", "system", "resources", "hit_dice")
    )
    assert hit_dice_change.value == 2


def test_short_rest_adds_constitution_modifier_once_per_hit_die():
    state = _hero_state(hero=_entity_system(hit_points=5))
    result = Dnd5ePlugin().resolve(
        _action("short_rest", hit_dice_spent=2, hit_die_faces=8),
        _context(state),
    )

    assert result.status is ResolutionStatus.RESOLVED
    # CON 12 -> +1, spent twice -> +2 on the pooled expression.
    assert result.rolls[0].expression == "2d8+2"
    hit_dice_change = next(
        change
        for change in result.state_changes
        if change.path == ("entities", "hero", "system", "resources", "hit_dice")
    )
    assert hit_dice_change.value == 1


def test_long_rest_restores_hit_points_and_half_hit_dice():
    state = _hero_state(hero=_entity_system(hit_points=5, resources={"hit_dice": 1}))
    result = Dnd5ePlugin().resolve(
        _action("long_rest", max_hit_dice=3),
        _context(state),
    )

    assert result.status is ResolutionStatus.RESOLVED
    hp_change = next(
        change
        for change in result.state_changes
        if change.path == ("entities", "hero", "system", "hit_points")
    )
    assert hp_change.value == 20
    hit_dice_change = next(
        change
        for change in result.state_changes
        if change.path == ("entities", "hero", "system", "resources", "hit_dice")
    )
    # 2014: regain hit dice equal to half the character's total (minimum 1)
    assert hit_dice_change.value == 2


def test_ambiguous_cover_requires_ruling():
    result = Dnd5ePlugin().resolve(
        _targeted("attack", target="goblin", attack_bonus=5, cover="partial"),
        _context(),
    )

    assert result.status is ResolutionStatus.RULING_REQUIRED
    assert result.ruling_question
    assert "cover" in result.ruling_question.lower()
    assert result.state_changes == ()


@pytest.mark.parametrize(
    "action_type",
    [
        "cast_spell",
        "class_feature",
        "monster_stat_block",
        "feat",
        "multiclass",
    ],
)
def test_out_of_scope_mechanics_return_unsupported(action_type: str):
    result = Dnd5ePlugin().resolve(_action(action_type), _context())

    assert result.status is ResolutionStatus.UNSUPPORTED
    assert result.explanation
    assert result.outcome == {}
    assert result.rolls == ()
    assert result.state_changes == ()


def test_no_5e_snake_case_vocabulary_leaked_into_api():
    api_root = _REPO_ROOT / "tabletop" / "api"
    leaks: list[str] = []
    for path in sorted(api_root.rglob("*.py")):
        text = path.read_text()
        for term in _FIVE_E_API_LEAKS:
            if re.search(rf"\b{re.escape(term)}\b", text):
                leaks.append(f"{path.relative_to(_REPO_ROOT)}:{term}")
    assert leaks == [], f"5e vocabulary leaked into tabletop/api: {leaks}"
