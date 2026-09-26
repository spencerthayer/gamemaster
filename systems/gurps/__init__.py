"""Minimal GURPS plugin. Mechanics stay in this package."""

from __future__ import annotations

import random

from tabletop.api.actions import GameAction
from tabletop.api.capabilities import Capability
from tabletop.api.plugin import (
    TABLETOP_PLUGIN_API_VERSION,
    GameSystemInfo,
    GameSystemPlugin,
)
from tabletop.api.resolution import (
    Resolution,
    ResolutionContext,
    ResolutionStatus,
    RollResult,
    StateChange,
    StateOperation,
)
from tabletop.dice import roller

_SUPPORTED_ACTIONS = frozenset(
    {
        "skill_check",
        "contest",
        "attack",
        "active_defense",
        "apply_damage",
        "hit_location",
        "spend_fatigue",
    }
)


class GurpsPlugin(GameSystemPlugin):
    @property
    def info(self) -> GameSystemInfo:
        return GameSystemInfo(
            id="gurps",
            name="GURPS Minimal Plugin",
            api_version=TABLETOP_PLUGIN_API_VERSION,
            version="0.1.0",
            description=(
                "3d6 roll-under, contests, active defense, damage resistance, "
                "hit location, and fatigue."
            ),
        )

    def capabilities(self) -> frozenset[Capability]:
        return frozenset(
            {
                Capability.DICE,
                Capability.ACTION_RESOLUTION,
                Capability.OPPOSED_RESOLUTION,
                Capability.DAMAGE,
                Capability.HIT_LOCATIONS,
                Capability.RESOURCE_TRACKING,
            }
        )

    def action_requirements(self, action_type: str) -> tuple[str, ...]:
        """Declare the rules-authoritative parameters each action needs.

        A skill check needs a target skill and difficulty, and an attack needs
        a skill and difficulty to roll against. These come from the rules or
        a GM ruling, never from the model guessing a number.
        """

        if action_type in ("skill_check", "contest", "attack", "active_defense"):
            return ("skill", "difficulty")
        return ()

    def handles_action(self, action_type: str) -> bool:
        """Report which mechanics this partial gurps plugin models."""

        return action_type in _SUPPORTED_ACTIONS

    def resolve(self, action: GameAction, context: ResolutionContext) -> Resolution:
        handler = {
            "skill_check": self._skill_check,
            "contest": self._contest,
            "attack": self._attack,
            "active_defense": self._defense,
            "apply_damage": self._damage,
            "hit_location": self._location,
            "spend_fatigue": self._fatigue,
        }.get(action.action_type)
        if handler is None:
            return Resolution(
                outcome={},
                status=ResolutionStatus.UNSUPPORTED,
                explanation=f"gurps does not implement {action.action_type!r}",
            )
        return handler(action, context)

    def _skill_check(self, action: GameAction, context: ResolutionContext) -> Resolution:
        skill = int(action.parameters["effective_skill"])
        rolled = _roll(action)
        margin = skill - rolled.total
        return Resolution(
            outcome={
                "success": rolled.total <= skill,
                "margin": margin,
                "effective_skill": skill,
            },
            status=ResolutionStatus.RESOLVED,
            rolls=(rolled,),
            explanation=f"3d6 {rolled.total} against {skill}",
        )

    def _contest(self, action: GameAction, context: ResolutionContext) -> Resolution:
        left = int(action.parameters["skill_a"])
        right = int(action.parameters["skill_b"])
        roll_a = _roll(action, seed_key="seed_a")
        roll_b = _roll(action, seed_key="seed_b")
        margin_a = left - roll_a.total
        margin_b = right - roll_b.total
        return Resolution(
            outcome={
                "margin_a": margin_a,
                "margin_b": margin_b,
                "winner": "a" if margin_a > margin_b else "b",
            },
            status=ResolutionStatus.RESOLVED,
            rolls=(roll_a, roll_b),
            explanation="contest resolved by margin of success",
        )

    def _attack(self, action: GameAction, context: ResolutionContext) -> Resolution:
        return Resolution(
            outcome={"status": "awaiting-response", "response_type": "active-defense"},
            status=ResolutionStatus.RESOLVED,
            explanation="awaiting an active defense",
        )

    def _defense(self, action: GameAction, context: ResolutionContext) -> Resolution:
        skill = int(action.parameters["defense_skill"])
        rolled = _roll(action)
        dodged = rolled.total <= skill
        changes: tuple[StateChange, ...] = ()
        if not dodged:
            changes = (
                StateChange(
                    operation=StateOperation.SET,
                    path=("entities", str(action.parameters["target_id"]), "system", "injury"),
                    value=int(action.parameters.get("injury", 0)),
                ),
            )
        return Resolution(
            outcome={"dodged": dodged},
            status=ResolutionStatus.RESOLVED,
            rolls=(rolled,),
            state_changes=changes,
            explanation="defense settled",
        )

    def _damage(self, action: GameAction, context: ResolutionContext) -> Resolution:
        target = str(action.parameters["target_id"])
        raw = int(action.parameters["damage"])
        dr = int(action.parameters.get("dr", 0))
        injury = max(raw - dr, 0)
        return Resolution(
            outcome={"injury": injury, "dr": dr},
            status=ResolutionStatus.RESOLVED,
            state_changes=(
                StateChange(
                    operation=StateOperation.SET,
                    path=("entities", target, "system", "injury"),
                    value=injury,
                ),
            ),
            explanation=f"injury {injury} after resistance",
        )

    def _location(self, action: GameAction, context: ResolutionContext) -> Resolution:
        rolled = _roll(action, expression="1d6")
        locations = ("torso", "arm", "arm", "leg", "leg", "head")
        location = locations[rolled.total - 1]
        return Resolution(
            outcome={"location": location},
            status=ResolutionStatus.RESOLVED,
            rolls=(rolled,),
            explanation=f"location {location}",
        )

    def _fatigue(self, action: GameAction, context: ResolutionContext) -> Resolution:
        target = str(action.parameters["target_id"])
        current = int(action.parameters.get("fp", 0))
        cost = int(action.parameters["cost"])
        return Resolution(
            outcome={"fp": current - cost},
            status=ResolutionStatus.RESOLVED,
            state_changes=(
                StateChange(
                    operation=StateOperation.SET,
                    path=("entities", target, "system", "fp"),
                    value=current - cost,
                ),
            ),
            explanation="fatigue spent",
        )


def _roll(action: GameAction, *, seed_key: str = "seed", expression: str = "3d6") -> RollResult:
    seed = int(action.parameters.get(seed_key, 0))
    return roller.roll(expression, rng=random.Random(seed))
