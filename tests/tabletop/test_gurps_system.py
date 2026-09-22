"""Minimal GURPS plugin stays out of the generic API."""

from __future__ import annotations

import ast
import random
import re
from pathlib import Path

from tabletop.api.actions import GameAction
from tabletop.api.capabilities import Capability
from tabletop.api.entities import EntityRef
from tabletop.api.resolution import ResolutionContext, ResolutionStatus
from tabletop.dice import roller
from systems.gurps import GurpsPlugin

_REPO = Path(__file__).resolve().parents[2]
_VOCABULARY = re.compile(r"gurps|dodge|parry|fatigue|hit location", re.IGNORECASE)


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


def _action(action_type: str, **parameters: object) -> GameAction:
    return GameAction(
        actor=EntityRef("hero"),
        action_type=action_type,
        parameters=parameters,
    )


def _context() -> ResolutionContext:
    return ResolutionContext(campaign_id="campaign-1", system_id="gurps", state={})


def test_gurps_capabilities_and_roll_under() -> None:
    plugin = GurpsPlugin()
    assert plugin.capabilities() == {
        Capability.DICE,
        Capability.ACTION_RESOLUTION,
        Capability.OPPOSED_RESOLUTION,
        Capability.DAMAGE,
        Capability.HIT_LOCATIONS,
        Capability.RESOURCE_TRACKING,
    }
    first = plugin.resolve(
        _action("skill_check", effective_skill=12, seed=3),
        _context(),
    )
    second = plugin.resolve(
        _action("skill_check", effective_skill=12, seed=3),
        _context(),
    )
    assert first.rolls[0].total == second.rolls[0].total
    assert first.rolls[0].expression == "3d6"
    expected = roller.roll("3d6", rng=random.Random(3)).total
    assert first.rolls[0].total == expected
    assert first.outcome["success"] is (expected <= 12)


def test_contest_defense_damage_location_and_fatigue() -> None:
    plugin = GurpsPlugin()
    contest = plugin.resolve(
        _action("contest", skill_a=14, skill_b=10, seed_a=1, seed_b=2),
        _context(),
    )
    assert contest.status is ResolutionStatus.RESOLVED
    assert set(contest.outcome) >= {"margin_a", "margin_b", "winner"}
    attack = plugin.resolve(_action("attack"), _context())
    assert attack.outcome["response_type"] == "active-defense"
    assert attack.status is not ResolutionStatus.RULING_REQUIRED
    defense = plugin.resolve(
        _action("active_defense", defense_skill=6, seed=1, target_id="hero", injury=4),
        _context(),
    )
    assert "dodged" in defense.outcome
    damage = plugin.resolve(
        _action("apply_damage", target_id="hero", damage=8, dr=3),
        _context(),
    )
    assert damage.outcome["injury"] == 5
    location = plugin.resolve(_action("hit_location", seed=2), _context())
    assert location.outcome["location"] in {"torso", "arm", "leg", "head"}
    fatigue = plugin.resolve(
        _action("spend_fatigue", target_id="hero", fp=10, cost=2),
        _context(),
    )
    assert fatigue.outcome["fp"] == 8
    assert fatigue.state_changes[0].path[-1] == "fp"


def test_tabletop_api_has_no_gurps_vocabulary() -> None:
    matches: list[str] = []
    for path in sorted((_REPO / "tabletop" / "api").rglob("*.py")):
        if _VOCABULARY.search(_without_docstrings(path.read_text())):
            matches.append(str(path))
    assert matches == []
