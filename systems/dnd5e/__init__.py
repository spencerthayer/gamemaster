"""Partial D&D 5e reference plugin (2014 rules revision only).

Validates the generic plugin API against real mechanics without claiming a
complete 5e engine. Spellcasting, full class features, monster stat blocks,
feats, and multiclassing are explicitly unsupported.
"""

from __future__ import annotations

from typing import Any, Mapping

from tabletop.api.actions import GameAction
from tabletop.api.capabilities import Capability
from tabletop.api.errors import StateValidationError
from tabletop.api.plugin import (
    TABLETOP_PLUGIN_API_VERSION,
    GameSystemInfo,
    GameSystemPlugin,
    ValidationResult,
)
from tabletop.api.resolution import (
    Resolution,
    ResolutionContext,
    ResolutionStatus,
    StateChange,
    StateOperation,
)
from tabletop.dice import roller

_SYSTEM_ID = "dnd5e"
_RULES_REVISION = "2014"

_ABILITIES = frozenset(
    {
        "strength",
        "dexterity",
        "constitution",
        "intelligence",
        "wisdom",
        "charisma",
    }
)

# Small set of basic 2014 conditions this plugin models.
_BASIC_CONDITIONS = frozenset(
    {
        "blinded",
        "charmed",
        "deafened",
        "frightened",
        "grappled",
        "incapacitated",
        "invisible",
        "paralyzed",
        "petrified",
        "poisoned",
        "prone",
        "restrained",
        "stunned",
        "unconscious",
    }
)

_UNSUPPORTED_ACTIONS = frozenset(
    {
        "cast_spell",
        "class_feature",
        "monster_stat_block",
        "feat",
        "multiclass",
    }
)

_SUPPORTED_ACTIONS = frozenset(
    {
        "ability_check",
        "saving_throw",
        "attack",
        "apply_damage",
        "apply_condition",
        "roll_initiative",
        "move",
        "short_rest",
        "long_rest",
    }
)


class Dnd5ePlugin(GameSystemPlugin):
    """Partial 2014 D&D 5e mechanics as StateChange requests."""

    @property
    def info(self) -> GameSystemInfo:
        return GameSystemInfo(
            id=_SYSTEM_ID,
            name="D&D 5e Reference Plugin",
            api_version=TABLETOP_PLUGIN_API_VERSION,
            version="0.1.0",
            description=(
                "Partial D&D 5e reference plugin using the 2014 rules revision "
                "only. Ability checks, saving throws, attacks, advantage and "
                "disadvantage, damage, conditions, initiative, movement, and "
                "basic rests. Never mixes later revisions."
            ),
        )

    def capabilities(self) -> frozenset[Capability]:
        """Advertise only the mechanics this plugin currently implements."""
        return frozenset(
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

    def action_requirements(self, action_type: str) -> tuple[str, ...]:
        """Declare the rules-authoritative parameters each action needs.

        A difficulty class is a rules fact. Without this declaration the
        planner cannot tell a DC taken from the rules or a GM ruling apart
        from one the model invented.
        """

        # Only parameters that are a *mechanical decision* are declared.
        # Which ability, which condition, and which destination are the
        # player's free choices, so they are deliberately not declared.
        if action_type in ("ability_check", "saving_throw"):
            return ("dc",)
        if action_type == "attack":
            return ("attack_bonus",)
        if action_type == "apply_damage":
            # The damage number is a mechanical decision. A model may not
            # pick it, so it is declared rather than left to the model.
            return ("amount",)
        return ()

    def handles_action(self, action_type: str) -> bool:
        """Report which 2014 mechanics this partial plugin actually models."""

        if action_type in _UNSUPPORTED_ACTIONS:
            return False
        return action_type in _SUPPORTED_ACTIONS

    def resolve(self, action: GameAction, context: ResolutionContext) -> Resolution:
        """Resolve a supported 2014 mechanic without mutating context state."""
        if action.action_type in _UNSUPPORTED_ACTIONS:
            return _unsupported(
                f"This partial 2014 dnd5e plugin does not implement "
                f"{action.action_type!r}."
            )

        handlers = {
            "ability_check": self._resolve_ability_check,
            "saving_throw": self._resolve_saving_throw,
            "attack": self._resolve_attack,
            "apply_damage": self._resolve_apply_damage,
            "apply_condition": self._resolve_apply_condition,
            "roll_initiative": self._resolve_initiative,
            "move": self._resolve_move,
            "short_rest": self._resolve_short_rest,
            "long_rest": self._resolve_long_rest,
        }
        handler = handlers.get(action.action_type)
        if handler is None:
            return _unsupported(
                f"This partial 2014 dnd5e plugin does not model action type "
                f"{action.action_type!r}."
            )
        return handler(action, context)

    def validate_state(self, state: Mapping[str, Any]) -> ValidationResult:
        """Accept any mapping; detailed sheet validation is out of scope."""
        if not isinstance(state, Mapping):
            raise StateValidationError("dnd5e state must be a mapping")
        return ValidationResult.ok()

    def state_schema(self) -> Mapping[str, Any]:
        """Opaque entity-oriented state used by the partial 2014 mechanics."""
        entity_system = {
            "armor_class": "integer",
            "hit_points": "integer",
            "max_hit_points": "integer",
            "speed": "integer",
            "position": {"x": "number", "y": "number"},
            "conditions": ["string"],
            "abilities": {ability: "integer" for ability in sorted(_ABILITIES)},
            "resources": {"hit_dice": "integer"},
        }
        return {
            "rules_revision": _RULES_REVISION,
            "entities": {
                "<entity_id>": {"system": entity_system},
            },
            "campaign": {"system": {"initiative_order": ["entity_id"]}},
            "scene": {"system": {"initiative_order": ["entity_id"]}},
        }

    def _resolve_ability_check(
        self, action: GameAction, context: ResolutionContext
    ) -> Resolution:
        ability = action.parameters.get("ability")
        dc = action.parameters.get("dc")
        if ability not in _ABILITIES:
            return _unresolved("An ability check requires a known ability name.")
        if not _is_number(dc):
            return _unresolved("An ability check requires a numeric DC.")

        modifier = _ability_modifier(context, action.actor.id, ability)
        if modifier is None:
            return _unresolved(
                f"Entity {action.actor.id!r} is missing ability score {ability!r}."
            )

        expression = _d20_expression(
            modifier,
            advantage=bool(action.parameters.get("advantage")),
            disadvantage=bool(action.parameters.get("disadvantage")),
        )
        roll = roller.roll(expression)
        check_result = "success" if roll.total >= dc else "failure"
        return Resolution(
            outcome={
                "check_result": check_result,
                "roll_total": roll.total,
                "dc": dc,
                "ability": ability,
            },
            rolls=(roll,),
            explanation=(
                f"Ability check ({ability}): rolled {roll.total} against DC "
                f"{dc}: {check_result}."
            ),
        )

    def _resolve_saving_throw(
        self, action: GameAction, context: ResolutionContext
    ) -> Resolution:
        ability = action.parameters.get("ability")
        dc = action.parameters.get("dc")
        if ability not in _ABILITIES:
            return _unresolved("A saving throw requires a known ability name.")
        if not _is_number(dc):
            return _unresolved("A saving throw requires a numeric DC.")

        modifier = _ability_modifier(context, action.actor.id, ability)
        if modifier is None:
            return _unresolved(
                f"Entity {action.actor.id!r} is missing ability score {ability!r}."
            )

        expression = _d20_expression(
            modifier,
            advantage=bool(action.parameters.get("advantage")),
            disadvantage=bool(action.parameters.get("disadvantage")),
        )
        roll = roller.roll(expression)
        save_result = "success" if roll.total >= dc else "failure"
        return Resolution(
            outcome={
                "save_result": save_result,
                "roll_total": roll.total,
                "dc": dc,
                "ability": ability,
            },
            rolls=(roll,),
            explanation=(
                f"Saving throw ({ability}): rolled {roll.total} against DC "
                f"{dc}: {save_result}."
            ),
        )

    def _resolve_attack(
        self, action: GameAction, context: ResolutionContext
    ) -> Resolution:
        if len(action.targets) != 1:
            return _unresolved("An attack requires exactly one target.")

        cover = action.parameters.get("cover")
        if cover == "partial":
            return Resolution(
                outcome={},
                status=ResolutionStatus.RULING_REQUIRED,
                ruling_question=(
                    "What cover does the target have (half, three-quarters, "
                    "or total), and which armor class bonus applies?"
                ),
                explanation=(
                    "Cover described only as partial is not a 2014 degree of "
                    "cover; the GM must choose half, three-quarters, or total."
                ),
            )

        attack_bonus = action.parameters.get("attack_bonus")
        if not _is_number(attack_bonus):
            return _unresolved("An attack requires a numeric attack_bonus.")

        target_id = action.targets[0].id
        target = _entity_system(context, target_id)
        if target is None:
            return _unresolved(f"Target {target_id!r} is not present in state.")
        armor_class = target.get("armor_class")
        if not _is_number(armor_class):
            return _unresolved(
                f"Target {target_id!r} is missing a numeric armor_class."
            )

        expression = _d20_expression(
            int(attack_bonus),
            advantage=bool(action.parameters.get("advantage")),
            disadvantage=bool(action.parameters.get("disadvantage")),
        )
        roll = roller.roll(expression)
        attack_result = "hit" if roll.total >= armor_class else "miss"
        return Resolution(
            outcome={
                "attack_result": attack_result,
                "roll_total": roll.total,
                "target_armor_class": armor_class,
            },
            rolls=(roll,),
            explanation=(
                f"Attack against {target_id}: rolled {roll.total} vs armor "
                f"class {armor_class}: {attack_result}."
            ),
        )

    def _resolve_apply_damage(
        self, action: GameAction, context: ResolutionContext
    ) -> Resolution:
        if len(action.targets) != 1:
            return _unresolved("Applying damage requires exactly one target.")
        amount = action.parameters.get("amount")
        if not _is_number(amount) or amount < 0:
            return _unresolved("Damage requires a non-negative numeric amount.")

        target_id = action.targets[0].id
        target = _entity_system(context, target_id)
        if target is None:
            return _unresolved(f"Target {target_id!r} is not present in state.")
        current = target.get("hit_points")
        if not _is_number(current):
            return _unresolved(
                f"Target {target_id!r} is missing numeric hit_points."
            )

        new_value = max(0, int(current) - int(amount))
        return Resolution(
            outcome={
                "damage_result": "applied",
                "amount": int(amount),
                "previous_hit_points": int(current),
                "new_hit_points": new_value,
            },
            state_changes=(
                StateChange(
                    operation=StateOperation.SET,
                    path=("entities", target_id, "system", "hit_points"),
                    value=new_value,
                ),
            ),
            explanation=(
                f"Applied {int(amount)} damage to {target_id}: hit points "
                f"{int(current)} -> {new_value}."
            ),
        )

    def _resolve_apply_condition(
        self, action: GameAction, context: ResolutionContext
    ) -> Resolution:
        if len(action.targets) != 1:
            return _unresolved("Applying a condition requires exactly one target.")
        condition = action.parameters.get("condition")
        if not isinstance(condition, str) or not condition:
            return _unresolved("A condition name string is required.")
        if condition not in _BASIC_CONDITIONS:
            return _unsupported(
                f"Condition {condition!r} is outside the small basic set "
                f"modeled by this partial 2014 plugin."
            )

        target_id = action.targets[0].id
        target = _entity_system(context, target_id)
        if target is None:
            return _unresolved(f"Target {target_id!r} is not present in state.")

        existing = target.get("conditions", ())
        if isinstance(existing, (list, tuple)):
            current = [str(item) for item in existing]
        else:
            return _unresolved(
                f"Target {target_id!r} conditions must be a list."
            )
        if condition not in current:
            current.append(condition)
        new_conditions = tuple(current)
        return Resolution(
            outcome={"condition_result": "applied", "condition": condition},
            state_changes=(
                StateChange(
                    operation=StateOperation.SET,
                    path=("entities", target_id, "system", "conditions"),
                    value=new_conditions,
                ),
            ),
            explanation=f"Applied condition {condition!r} to {target_id}.",
        )

    def _resolve_initiative(
        self, action: GameAction, context: ResolutionContext
    ) -> Resolution:
        participants = action.targets or (action.actor,)
        if not participants:
            return _unresolved("Initiative requires at least one participant.")

        rolls = []
        scored: list[tuple[int, str]] = []
        for participant in participants:
            entity = _entity_system(context, participant.id)
            if entity is None:
                return _unresolved(
                    f"Participant {participant.id!r} is not present in state."
                )
            dex_mod = _ability_modifier(context, participant.id, "dexterity")
            if dex_mod is None:
                return _unresolved(
                    f"Participant {participant.id!r} is missing dexterity."
                )
            expression = _d20_expression(dex_mod)
            roll = roller.roll(expression)
            rolls.append(roll)
            scored.append((roll.total, participant.id))

        totals = [total for total, _ in scored]
        if len(totals) != len(set(totals)):
            tied = sorted(
                {
                    entity_id
                    for total, entity_id in scored
                    if totals.count(total) > 1
                }
            )
            return Resolution(
                outcome={
                    "initiative_totals": {
                        entity_id: total for total, entity_id in scored
                    },
                    "tied_participants": tuple(tied),
                },
                status=ResolutionStatus.RULING_REQUIRED,
                rolls=tuple(rolls),
                ruling_question=(
                    "Initiative totals are tied between "
                    f"{', '.join(tied)}. Under the 2014 rules, who goes "
                    "first?"
                ),
                explanation=(
                    "Equal initiative totals are not broken by Dexterity in "
                    "the 2014 rules; the GM decides order among ties."
                ),
            )

        scored.sort(key=lambda item: (-item[0], item[1]))
        order = tuple(entity_id for _, entity_id in scored)
        return Resolution(
            outcome={"initiative_order": order},
            rolls=tuple(rolls),
            state_changes=(
                StateChange(
                    operation=StateOperation.SET,
                    path=_initiative_path(context),
                    value=order,
                ),
            ),
            explanation=f"Initiative order: {', '.join(order)}.",
        )

    def _resolve_move(
        self, action: GameAction, context: ResolutionContext
    ) -> Resolution:
        destination = action.parameters.get("destination")
        if not isinstance(destination, Mapping):
            return _unresolved("Movement requires a destination mapping with x and y.")
        if not _is_number(destination.get("x")) or not _is_number(destination.get("y")):
            return _unresolved("Destination requires numeric x and y.")

        entity = _entity_system(context, action.actor.id)
        if entity is None:
            return _unresolved(f"Actor {action.actor.id!r} is not present in state.")
        position = entity.get("position")
        speed = entity.get("speed")
        if not isinstance(position, Mapping):
            return _unresolved(f"Actor {action.actor.id!r} is missing position.")
        if not _is_number(position.get("x")) or not _is_number(position.get("y")):
            return _unresolved(f"Actor {action.actor.id!r} has an invalid position.")
        if not _is_number(speed) or speed < 0:
            return _unresolved(f"Actor {action.actor.id!r} is missing a numeric speed.")

        dx = abs(float(destination["x"]) - float(position["x"]))
        dy = abs(float(destination["y"]) - float(position["y"]))
        distance = int(dx + dy)
        if distance > int(speed):
            return _unresolved(
                f"Destination is {distance} feet away; speed is only {int(speed)}."
            )

        new_position = {
            "x": _as_coordinate(destination["x"]),
            "y": _as_coordinate(destination["y"]),
        }

        return Resolution(
            outcome={
                "move_result": "moved",
                "distance": distance,
                "destination": new_position,
            },
            state_changes=(
                StateChange(
                    operation=StateOperation.SET,
                    path=("entities", action.actor.id, "system", "position"),
                    value=new_position,
                ),
            ),
            explanation=(
                f"Moved {action.actor.id} {distance} feet to "
                f"({new_position['x']}, {new_position['y']})."
            ),
        )

    def _resolve_short_rest(
        self, action: GameAction, context: ResolutionContext
    ) -> Resolution:
        entity = _entity_system(context, action.actor.id)
        if entity is None:
            return _unresolved(f"Actor {action.actor.id!r} is not present in state.")

        hit_dice_spent = action.parameters.get("hit_dice_spent", 1)
        hit_die_faces = action.parameters.get("hit_die_faces")
        if not _is_number(hit_dice_spent) or int(hit_dice_spent) < 1:
            return _unresolved("short_rest requires a positive hit_dice_spent.")
        if not _is_number(hit_die_faces) or int(hit_die_faces) < 2:
            return _unresolved("short_rest requires hit_die_faces of at least 2.")

        resources = entity.get("resources")
        if not isinstance(resources, Mapping):
            return _unresolved(f"Actor {action.actor.id!r} is missing resources.")
        hit_dice = resources.get("hit_dice")
        if not _is_number(hit_dice):
            return _unresolved(f"Actor {action.actor.id!r} is missing hit_dice.")
        if int(hit_dice) < int(hit_dice_spent):
            return _unresolved(
                f"Actor {action.actor.id!r} has only {int(hit_dice)} hit dice."
            )

        current_hp = entity.get("hit_points")
        max_hp = entity.get("max_hit_points")
        if not _is_number(current_hp) or not _is_number(max_hp):
            return _unresolved(
                f"Actor {action.actor.id!r} is missing hit_points or max_hit_points."
            )

        con_mod = _ability_modifier(context, action.actor.id, "constitution")
        if con_mod is None:
            return _unresolved(
                f"Actor {action.actor.id!r} is missing constitution."
            )

        # 2014: add Constitution modifier once per Hit Die spent.
        spent = int(hit_dice_spent)
        expression = _dice_with_modifier(
            spent, int(hit_die_faces), con_mod * spent
        )
        roll = roller.roll(expression)
        healed = max(0, int(roll.total))
        new_hp = min(int(max_hp), int(current_hp) + healed)
        new_hit_dice = int(hit_dice) - spent
        return Resolution(
            outcome={
                "rest_result": "short_rest",
                "hit_points_recovered": new_hp - int(current_hp),
                "new_hit_points": new_hp,
                "hit_dice_remaining": new_hit_dice,
            },
            rolls=(roll,),
            state_changes=(
                StateChange(
                    operation=StateOperation.SET,
                    path=("entities", action.actor.id, "system", "hit_points"),
                    value=new_hp,
                ),
                StateChange(
                    operation=StateOperation.SET,
                    path=(
                        "entities",
                        action.actor.id,
                        "system",
                        "resources",
                        "hit_dice",
                    ),
                    value=new_hit_dice,
                ),
            ),
            explanation=(
                f"Short rest for {action.actor.id}: recovered "
                f"{new_hp - int(current_hp)} hit points; hit dice now "
                f"{new_hit_dice}."
            ),
        )

    def _resolve_long_rest(
        self, action: GameAction, context: ResolutionContext
    ) -> Resolution:
        entity = _entity_system(context, action.actor.id)
        if entity is None:
            return _unresolved(f"Actor {action.actor.id!r} is not present in state.")

        max_hit_dice = action.parameters.get("max_hit_dice")
        if not _is_number(max_hit_dice) or int(max_hit_dice) < 1:
            return _unresolved("long_rest requires a positive max_hit_dice.")

        resources = entity.get("resources")
        if not isinstance(resources, Mapping):
            return _unresolved(f"Actor {action.actor.id!r} is missing resources.")
        hit_dice = resources.get("hit_dice")
        if not _is_number(hit_dice):
            return _unresolved(f"Actor {action.actor.id!r} is missing hit_dice.")

        max_hp = entity.get("max_hit_points")
        if not _is_number(max_hp):
            return _unresolved(
                f"Actor {action.actor.id!r} is missing max_hit_points."
            )

        # 2014: regain spent Hit Dice up to half the character's total
        # number of them (minimum one die).
        regain = max(1, int(max_hit_dice) // 2)
        new_hit_dice = min(int(max_hit_dice), int(hit_dice) + regain)
        return Resolution(
            outcome={
                "rest_result": "long_rest",
                "new_hit_points": int(max_hp),
                "hit_dice_regained": new_hit_dice - int(hit_dice),
                "hit_dice_remaining": new_hit_dice,
            },
            state_changes=(
                StateChange(
                    operation=StateOperation.SET,
                    path=("entities", action.actor.id, "system", "hit_points"),
                    value=int(max_hp),
                ),
                StateChange(
                    operation=StateOperation.SET,
                    path=(
                        "entities",
                        action.actor.id,
                        "system",
                        "resources",
                        "hit_dice",
                    ),
                    value=new_hit_dice,
                ),
            ),
            explanation=(
                f"Long rest for {action.actor.id}: hit points restored to "
                f"{int(max_hp)}; hit dice now {new_hit_dice}."
            ),
        )


def _entity_system(
    context: ResolutionContext, entity_id: str
) -> Mapping[str, Any] | None:
    entities = context.state.get("entities")
    if not isinstance(entities, Mapping):
        return None
    entity = entities.get(entity_id)
    if not isinstance(entity, Mapping):
        return None
    system = entity.get("system")
    if not isinstance(system, Mapping):
        return None
    return system


def _ability_modifier(
    context: ResolutionContext, entity_id: str, ability: str
) -> int | None:
    system = _entity_system(context, entity_id)
    if system is None:
        return None
    abilities = system.get("abilities")
    if not isinstance(abilities, Mapping):
        return None
    score = abilities.get(ability)
    if not _is_number(score):
        return None
    return (int(score) - 10) // 2


def _initiative_path(context: ResolutionContext) -> tuple[str, ...]:
    if context.scene_id is not None:
        return ("scene", "system", "initiative_order")
    return ("campaign", "system", "initiative_order")


def _d20_expression(
    modifier: int, *, advantage: bool = False, disadvantage: bool = False
) -> str:
    if advantage and disadvantage:
        base = "1d20"
    elif advantage:
        base = "2d20kh1"
    elif disadvantage:
        base = "2d20kl1"
    else:
        base = "1d20"
    return _append_modifier(base, modifier)


def _dice_with_modifier(count: int, faces: int, modifier: int) -> str:
    return _append_modifier(f"{count}d{faces}", modifier)


def _append_modifier(base: str, modifier: int) -> str:
    if modifier > 0:
        return f"{base}+{modifier}"
    if modifier < 0:
        return f"{base}{modifier}"
    return base


def _as_coordinate(value: object) -> int | float:
    number = float(value)  # type: ignore[arg-type]
    if number == int(number):
        return int(number)
    return number


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _unsupported(explanation: str) -> Resolution:
    return Resolution(
        outcome={},
        status=ResolutionStatus.UNSUPPORTED,
        explanation=explanation,
    )


def _unresolved(explanation: str) -> Resolution:
    return Resolution(
        outcome={},
        status=ResolutionStatus.UNRESOLVED,
        explanation=explanation,
    )
