"""Behavior tests for the freeform reference system."""

import os
import subprocess
import sys
from pathlib import Path

from systems.freeform import FreeformPlugin
from tabletop.api.actions import GameAction
from tabletop.api.capabilities import Capability
from tabletop.api.entities import EntityRef
from tabletop.api.resolution import (
    ResolutionContext,
    ResolutionStatus,
    StateOperation,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _action(action_type: str, **parameters: object) -> GameAction:
    return GameAction(
        actor=EntityRef(id="hero"),
        action_type=action_type,
        parameters=parameters,
    )


def _context(state: dict[str, object] | None = None) -> ResolutionContext:
    return ResolutionContext(
        campaign_id="campaign-1",
        system_id="freeform",
        state=state or {},
    )


def test_advertises_only_implemented_capabilities():
    assert FreeformPlugin().capabilities() == frozenset(
        {
            Capability.DICE,
            Capability.ACTION_RESOLUTION,
            Capability.OPPOSED_RESOLUTION,
            Capability.RESOURCE_TRACKING,
        }
    )


def test_simple_check_resolves_with_roller_result_and_explanation():
    result = FreeformPlugin().resolve(
        _action("check", expression="1d2+4", difficulty=5),
        _context(),
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert len(result.rolls) == 1
    roll = result.rolls[0]
    assert roll.expression == "1d2+4"
    assert roll.total in {5, 6}
    assert result.outcome == {
        "check_result": "success",
        "roll_total": roll.total,
        "difficulty": 5,
    }
    assert result.explanation


def test_opposed_check_resolves_both_sides_from_one_action():
    result = FreeformPlugin().resolve(
        GameAction(
            actor=EntityRef(id="hero"),
            action_type="opposed_check",
            targets=(EntityRef(id="rival"),),
            parameters={
                "actor_expression": "1d2+4",
                "target_expression": "1d2",
            },
        ),
        _context(),
    )

    assert result.status is ResolutionStatus.RESOLVED
    actor_roll, target_roll = result.rolls
    assert actor_roll.total in {5, 6}
    assert target_roll.total in {1, 2}
    assert result.outcome == {
        "opposed_result": "actor",
        "actor_total": actor_roll.total,
        "target_total": target_roll.total,
    }
    assert result.explanation


def test_resource_decrement_returns_state_change_without_mutating_context():
    state = {"resources": {"momentum": 3}}
    context = _context(state)

    result = FreeformPlugin().resolve(
        _action(
            "decrement_resource",
            path=["resources", "momentum"],
            amount=2,
        ),
        context,
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.outcome == {
        "resource_result": "decremented",
        "previous_value": 3,
        "new_value": 1,
    }
    assert len(result.state_changes) == 1
    change = result.state_changes[0]
    assert change.operation is StateOperation.SET
    assert change.path == ("resources", "momentum")
    assert change.value == 1
    assert context.state["resources"]["momentum"] == 3
    assert state["resources"]["momentum"] == 3


def test_unsupported_action_has_explanation_and_no_fabricated_result():
    result = FreeformPlugin().resolve(_action("cast_spell"), _context())

    assert result.status is ResolutionStatus.UNSUPPORTED
    assert result.explanation
    assert result.outcome == {}
    assert result.rolls == ()
    assert result.state_changes == ()


def test_check_without_difficulty_is_unresolved():
    result = FreeformPlugin().resolve(
        _action("check", expression="1d20"),
        _context(),
    )

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.explanation
    assert result.outcome == {}
    assert result.rolls == ()


def test_freeform_imports_no_omega_metta_or_orchestration_modules():
    probe = (
        "import sys\n"
        "import systems.freeform\n"
        "bad = [m for m in sys.modules\n"
        "       if m.split('.')[0].lower().startswith(('omega', 'metta'))\n"
        "       or m == 'tabletop.orchestration'\n"
        "       or m.startswith('tabletop.orchestration.')]\n"
        "assert not bad, f'freeform imported forbidden modules: {bad}'\n"
    )
    env = dict(os.environ, PYTHONPATH=str(_REPO_ROOT))
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_REPO_ROOT),
    )

    assert result.returncode == 0, result.stderr
