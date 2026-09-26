"""Decide what a proposal means before anything mechanical happens.

``plan_resolution`` is a pure function. Given the same proposal, plugin,
context, and parameters it returns an equal plan every time. It does not
persist, roll, retrieve, or call a model, and it never constructs a
``GameAction`` unless every value in that action is already authoritative.

The order of the checks is the contract:

1. No proposed mechanic -> narrative. Most player input is talking.
2. An action type the plugin does not implement -> unsupported.
3. The actor is not the player's to act -> ask the player.
4. A target is absent, or more than one candidate fits -> ask the player.
   Player ambiguity is a question for the player, never for the GM.
5. A required parameter has no authoritative value -> look it up, then
   escalate to the GM only when no lookup can answer.
6. Otherwise -> resolve, with a sanitized ``GameAction``.

Only step 6 produces a ``GameAction``. Every earlier step returns a
disposition and no action, so a model proposal can never become an
authoritative mechanical action by accident.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from tabletop.api.actions import (
    ActionProposal,
    GameAction,
    MechanicalParameter,
)
from tabletop.api.capabilities import Capability
from tabletop.api.entities import EntityRef
from tabletop.api.plugin import GameSystemPlugin


class Disposition(str, Enum):
    """What the planner decided to do with one proposal."""

    RESOLVE = "resolve"
    NARRATIVE = "narrative"
    PLAYER_CLARIFICATION = "player_clarification"
    GM_RULING = "gm_ruling"
    RULE_LOOKUP = "rule_lookup"
    STATE_LOOKUP = "state_lookup"
    UNSUPPORTED = "unsupported"


#: Parameters a rules document can answer. Anything else is a question about
#: the current game state, which a rules text cannot settle.
_RULE_PARAMETERS = frozenset(
    {
        "dc",
        "difficulty",
        "attack_bonus",
        "skill",
        "expression",
        "damage",
    }
)


@dataclass(frozen=True)
class PlanContext:
    """What the planner needs to know about the world right now.

    Deliberately plain data: the planner reads it and never writes it.
    """

    present_entity_ids: tuple[str, ...] = ()
    actor_controls_actor: bool = True
    player_controlled_entity_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClarificationRequest:
    """A question for the player, not for the GM.

    Ambiguity about who did what is something the player can answer. Sending
    it to the GM would interrupt play to ask a question the player already
    knows the answer to.
    """

    question: str
    candidate_refs: tuple[str, ...] = ()
    subject: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "candidate_refs": list(self.candidate_refs),
            "subject": self.subject,
        }


@dataclass(frozen=True)
class ResolutionPlan:
    """One explicit decision, with the action only when it is safe to build."""

    disposition: Disposition
    reason: str
    action: GameAction | None = None
    clarification: ClarificationRequest | None = None
    missing_parameters: tuple[str, ...] = ()
    parameters: Mapping[str, MechanicalParameter] = field(default_factory=dict)

    @property
    def is_resolvable(self) -> bool:
        """True only when a sanitized action is ready for the plugin."""

        return self.disposition is Disposition.RESOLVE and self.action is not None


def plan_resolution(
    proposal: ActionProposal,
    *,
    plugin: GameSystemPlugin,
    context: PlanContext,
    parameters: Sequence[MechanicalParameter] = (),
    has_lookups: bool = True,
) -> ResolutionPlan:
    """Route one proposal to exactly one disposition.

    ``has_lookups`` says whether this turn can consult attached rules and
    campaign state. It is an input, not an internal call: the planner itself
    never retrieves, so that behavior stays testable and the escalation order
    stays visible here.
    """
    if not isinstance(proposal, ActionProposal):
        raise TypeError("plan_resolution requires an ActionProposal")

    by_name = {parameter.name: parameter for parameter in parameters}

    action_type = proposal.proposed_action_type
    if action_type is None:
        return ResolutionPlan(
            disposition=Disposition.NARRATIVE,
            reason="the proposal names no mechanical action",
        )

    if not _plugin_handles(plugin, action_type):
        return ResolutionPlan(
            disposition=Disposition.UNSUPPORTED,
            reason=f"the active system plugin does not implement {action_type!r}",
        )

    if not _actor_is_controlled(proposal, context):
        return ResolutionPlan(
            disposition=Disposition.PLAYER_CLARIFICATION,
            reason="the acting player does not control that actor",
            clarification=ClarificationRequest(
                question=(
                    f"Which of your characters is {proposal.actor_id}? "
                    "That character is not one you control here."
                ),
                subject=proposal.actor_id,
            ),
        )

    target_issue = _check_targets(proposal, context)
    if target_issue is not None:
        return target_issue

    required = tuple(plugin.action_requirements(action_type))
    missing = _missing_requirements(required, by_name)
    if missing:
        return _route_missing(missing, has_lookups=has_lookups)

    return ResolutionPlan(
        disposition=Disposition.RESOLVE,
        reason="every required parameter has an authoritative value",
        action=_build_action(proposal, action_type, by_name),
        parameters=dict(by_name),
    )


def _plugin_handles(plugin: GameSystemPlugin, action_type: str) -> bool:
    """True when the plugin claims this action type.

    A plugin is authoritative about what it implements. An action type it
    does not know is unsupported, not a question for the GM.
    """
    return plugin.supports(Capability.ACTION_RESOLUTION) and plugin.handles_action(
        action_type
    )


def _missing_requirements(
    required: Sequence[str],
    by_name: Mapping[str, MechanicalParameter],
) -> tuple[str, ...]:
    """Required names with no authoritative value.

    A parameter present only as ``model_proposal`` counts as missing: the
    model offering a number is not a source of rules truth.
    """
    missing: list[str] = []
    for name in required:
        parameter = by_name.get(name)
        if parameter is None or not parameter.source.is_authoritative:
            missing.append(name)
    return tuple(missing)


def _route_missing(missing: Sequence[str], *, has_lookups: bool) -> ResolutionPlan:
    if not has_lookups:
        return ResolutionPlan(
            disposition=Disposition.GM_RULING,
            reason=(
                "no lookup is available for "
                f"{', '.join(missing)}; the GM must decide"
            ),
            missing_parameters=tuple(missing),
        )
    rule_needed = tuple(name for name in missing if name in _RULE_PARAMETERS)
    if rule_needed:
        return ResolutionPlan(
            disposition=Disposition.RULE_LOOKUP,
            reason=(
                "attached rules or a ruling can supply "
                f"{', '.join(rule_needed)}"
            ),
            missing_parameters=rule_needed,
        )
    state_needed = tuple(name for name in missing if name not in _RULE_PARAMETERS)
    return ResolutionPlan(
        disposition=Disposition.STATE_LOOKUP,
        reason=(
            "current campaign state can supply "
            f"{', '.join(state_needed)}"
        ),
        missing_parameters=state_needed,
    )



def _actor_is_controlled(proposal: ActionProposal, context: PlanContext) -> bool:
    """True when the acting player may act as this actor.

    The explicit flag wins when cleared. Otherwise control is read from the
    player's controlled characters, so a caller cannot pass an actor they do
    not own by leaving the flag at its permissive default.
    """
    if not context.actor_controls_actor:
        return False
    if not context.player_controlled_entity_ids:
        return True
    return proposal.actor_id in context.player_controlled_entity_ids


def _check_targets(
    proposal: ActionProposal, context: PlanContext
) -> ResolutionPlan | None:
    """Return a clarification plan when the target is absent or ambiguous."""
    present = tuple(
        ref for ref in context.present_entity_ids if ref != proposal.actor_id
    )

    if not proposal.target_refs:
        # "Attack" with three hostiles in the room is the common real case of
        # this ambiguity. One candidate is unambiguous; several are not, and
        # the player, not the GM, is the one who can say which.
        if len(present) > 1:
            return ResolutionPlan(
                disposition=Disposition.PLAYER_CLARIFICATION,
                reason="no target named and several entities are present",
                clarification=ClarificationRequest(
                    question="Which one are you acting on: "
                    + ", ".join(present)
                    + "?",
                    candidate_refs=present,
                    subject=proposal.actor_id,
                ),
            )
        return None

    present_set = set(context.present_entity_ids)
    candidates = tuple(ref for ref in proposal.target_refs if ref in present_set)

    if not candidates:
        return ResolutionPlan(
            disposition=Disposition.PLAYER_CLARIFICATION,
            reason="no named target is present in the current scene",
            clarification=ClarificationRequest(
                question=(
                    f"{', '.join(proposal.target_refs)} is not here. "
                    "What are you acting on?"
                ),
                # The player picks from what is actually here.
                candidate_refs=present,
                subject=proposal.actor_id,
            ),
        )

    if len(candidates) > 1:
        return ResolutionPlan(
            disposition=Disposition.PLAYER_CLARIFICATION,
            reason="more than one present entity matches the proposal",
            clarification=ClarificationRequest(
                question=(
                    "Which one are you acting on: "
                    + ", ".join(candidates)
                    + "?"
                ),
                candidate_refs=candidates,
                subject=proposal.actor_id,
            ),
        )
    return None


def _build_action(
    proposal: ActionProposal,
    action_type: str,
    by_name: Mapping[str, MechanicalParameter],
) -> GameAction:
    """Build a sanitized ``GameAction`` from authoritative values only.

    Model-proposed parameters are dropped rather than forwarded: the
    plugin receives what the rules and the GM established, not what the model
    guessed.
    """
    parameters: dict[str, Any] = {
        name: value
        for name, value in proposal.parameters.items()
        if name not in _MODEL_ONLY_PARAMETERS
    }
    # Authoritative values overwrite whatever the proposal carried under the
    # same name: the rules and the GM, not the model, decide a difficulty.
    for name, parameter in by_name.items():
        if parameter.source.is_authoritative:
            parameters[name] = parameter.value
    return GameAction(
        actor=EntityRef(id=proposal.actor_id),
        action_type=action_type,
        targets=tuple(EntityRef(id=ref) for ref in proposal.target_refs),
        parameters=parameters,
    )


#: Parameter names that are only ever meaningful with a rules-authoritative
#: value. They are never forwarded from a model proposal.
_MODEL_ONLY_PARAMETERS = frozenset({"dc", "difficulty", "attack_bonus"})
