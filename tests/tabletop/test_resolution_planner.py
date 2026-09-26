"""The planner routes every proposal to one explicit disposition.

It is a pure decision function: no persistence, no dice, no retrieval, no
model call. Repeated calls on the same input must return equal plans, because
a plan that varied would be indistinguishable from a guess.
"""

from __future__ import annotations

from typing import Any

import pytest

from tabletop.api.actions import (
    ActionProposal,
    MechanicalParameter,
    ParameterSource,
)
from tabletop.api.capabilities import Capability
from tabletop.api.plugin import (
    TABLETOP_PLUGIN_API_VERSION,
    GameSystemInfo,
    GameSystemPlugin,
)
from tabletop.orchestration.planner import (
    Disposition,
    PlanContext,
    ResolutionPlan,
    plan_resolution,
)


class _Plugin(GameSystemPlugin):
    """A plugin declaring exactly the action types a test needs."""

    def __init__(self, requirements: dict[str, tuple[str, ...]]) -> None:
        self._requirements = requirements

    @property
    def info(self) -> GameSystemInfo:
        return GameSystemInfo(
            id="planner-test",
            name="Planner Test",
            api_version=TABLETOP_PLUGIN_API_VERSION,
        )

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.ACTION_RESOLUTION})

    def action_requirements(self, action_type: str) -> tuple[str, ...]:
        return self._requirements.get(action_type, ())

    def handles_action(self, action_type: str) -> bool:
        # The test plugin knows exactly the action types it was built with,
        # so anything else is genuinely unsupported.
        return action_type in self._requirements

    def resolve(self, action, context):  # pragma: no cover - never called here
        raise AssertionError("the planner must not call the plugin")


def _proposal(**overrides: Any) -> ActionProposal:
    payload: dict[str, Any] = {
        "actor_id": "pc-ada",
        "intent": "Ada forces the gate",
        "proposed_action_type": "ability_check",
        "target_refs": ("npc-gate",),
        "parameters": {"ability": "strength"},
        "uncertainty": None,
        "needs_resolution": True,
    }
    payload.update(overrides)
    return ActionProposal(**payload)


def _context(
    present: tuple[str, ...] = ("pc-ada", "npc-gate"),
    controlled: tuple[str, ...] = ("pc-ada",),
) -> PlanContext:
    return PlanContext(
        present_entity_ids=present,
        actor_controls_actor=True,
        player_controlled_entity_ids=controlled,
    )


# -- RESOLVE ----------------------------------------------------------------


def test_a_fully_authoritative_action_resolves() -> None:
    plugin = _Plugin({"ability_check": ("dc",)})
    plan = plan_resolution(
        _proposal(),
        plugin=plugin,
        context=_context(),
        parameters=(
            MechanicalParameter(
                name="dc", value=15, source=ParameterSource.ATTACHED_RULES
            ),
        ),
    )
    assert plan.disposition is Disposition.RESOLVE
    assert plan.action is not None
    assert plan.action.parameters["dc"] == 15
    assert plan.action.actor.id == "pc-ada"
    assert plan.missing_parameters == ()


def test_a_campaign_state_dc_is_also_authoritative() -> None:
    plugin = _Plugin({"ability_check": ("dc",)})
    plan = plan_resolution(
        _proposal(),
        plugin=plugin,
        context=_context(),
        parameters=(
            MechanicalParameter(
                name="dc", value=12, source=ParameterSource.CAMPAIGN_STATE
            ),
        ),
    )
    assert plan.disposition is Disposition.RESOLVE


def test_an_action_with_no_declared_requirements_resolves() -> None:
    plan = plan_resolution(
        _proposal(proposed_action_type="move"),
        plugin=_Plugin({"move": ()}),
        context=_context(),
        parameters=(),
    )
    assert plan.disposition is Disposition.RESOLVE
    assert plan.action is not None


# -- NARRATIVE --------------------------------------------------------------


def test_a_proposal_naming_no_mechanic_is_narrative() -> None:
    plan = plan_resolution(
        _proposal(proposed_action_type=None),
        plugin=_Plugin({}),
        context=_context(),
        parameters=(),
    )
    assert plan.disposition is Disposition.NARRATIVE
    assert plan.action is None


def test_a_narrative_proposal_never_becomes_an_action() -> None:
    plan = plan_resolution(
        _proposal(
            proposed_action_type=None,
            parameters={"ability": "strength"},
        ),
        plugin=_Plugin({"ability_check": ()}),
        context=_context(),
        parameters=(),
    )
    assert plan.disposition is Disposition.NARRATIVE
    assert plan.action is None


# -- PLAYER_CLARIFICATION ---------------------------------------------------


def test_multiple_present_targets_ask_the_player() -> None:
    plan = plan_resolution(
        _proposal(target_refs=()),
        plugin=_Plugin({"ability_check": ("dc",)}),
        context=_context(present=("pc-ada", "npc-gate", "npc-hound")),
        parameters=(
            MechanicalParameter(name="dc", value=15, source=ParameterSource.RULING),
        ),
    )
    assert plan.disposition is Disposition.PLAYER_CLARIFICATION
    assert plan.clarification is not None
    assert set(plan.clarification.candidate_refs) == {"npc-gate", "npc-hound"}


def test_an_ambiguous_target_is_never_escalated_to_the_gm() -> None:
    """Player ambiguity is not a rules question, so it must not become one."""

    plan = plan_resolution(
        _proposal(),
        plugin=_Plugin({"ability_check": ("dc",)}),
        context=_context(present=("pc-ada", "npc-gate", "npc-hound")),
        parameters=(
            MechanicalParameter(name="dc", value=15, source=ParameterSource.RULING),
        ),
    )
    assert plan.disposition is not Disposition.GM_RULING


def test_a_named_target_that_is_absent_asks_the_player() -> None:
    plan = plan_resolution(
        _proposal(target_refs=("npc-absent",)),
        plugin=_Plugin({"ability_check": ("dc",)}),
        context=_context(),
        parameters=(
            MechanicalParameter(name="dc", value=15, source=ParameterSource.RULING),
        ),
    )
    assert plan.disposition is Disposition.PLAYER_CLARIFICATION
    assert plan.clarification is not None
    # The player chooses from what is actually in the room, not from the
    # absent entity the proposal guessed at.
    assert plan.clarification.candidate_refs == ("npc-gate",)


# -- control ----------------------------------------------------------------


def test_a_player_acting_as_someone_they_do_not_control_is_clarified() -> None:
    plan = plan_resolution(
        _proposal(actor_id="npc-gate"),
        plugin=_Plugin({"ability_check": ()}),
        context=_context(controlled=("pc-ada",)),
        parameters=(),
    )
    assert plan.disposition is Disposition.PLAYER_CLARIFICATION
    assert plan.clarification is not None
    assert "npc-gate" in plan.clarification.question


# -- UNSUPPORTED ------------------------------------------------------------


def test_an_unknown_action_type_is_unsupported() -> None:
    plan = plan_resolution(
        _proposal(proposed_action_type="phase_moon"),
        plugin=_Plugin({}),
        context=_context(),
        parameters=(),
    )
    assert plan.disposition is Disposition.UNSUPPORTED
    assert plan.action is None


# -- parameter authority ----------------------------------------------------


def test_a_model_only_dc_cannot_reach_the_plugin() -> None:
    plan = plan_resolution(
        _proposal(),
        plugin=_Plugin({"ability_check": ("dc",)}),
        context=_context(),
        parameters=(
            MechanicalParameter(
                name="dc", value=15, source=ParameterSource.MODEL_PROPOSAL
            ),
        ),
    )
    assert plan.disposition is not Disposition.RESOLVE
    assert plan.action is None


def test_a_missing_dc_routes_to_a_lookup_not_a_ruling() -> None:
    plan = plan_resolution(
        _proposal(),
        plugin=_Plugin({"ability_check": ("dc",)}),
        context=_context(),
        parameters=(),
    )
    assert plan.disposition is Disposition.RULE_LOOKUP
    assert plan.missing_parameters == ("dc",)
    assert plan.action is None


def test_a_non_mechanical_missing_parameter_routes_to_state_lookup() -> None:
    plan = plan_resolution(
        _proposal(proposed_action_type="apply_damage"),
        plugin=_Plugin({"apply_damage": ("path",)}),
        context=_context(),
        parameters=(),
    )
    assert plan.disposition is Disposition.STATE_LOOKUP
    assert plan.missing_parameters == ("path",)


def test_a_gm_ruling_disposition_is_reachable_for_a_genuine_judgment() -> None:
    plan = plan_resolution(
        _proposal(),
        plugin=_Plugin({"ability_check": ("dc",)}),
        context=_context(),
        parameters=(),
        has_lookups=False,
    )
    assert plan.disposition is Disposition.GM_RULING
    assert plan.missing_parameters == ("dc",)


# -- purity and determinism -------------------------------------------------


def test_the_planner_does_not_persist_or_call_the_plugin() -> None:
    """The plugin's resolve raises if called, so a pass here proves no call."""

    plan = plan_resolution(
        _proposal(),
        plugin=_Plugin({"ability_check": ()}),
        context=_context(),
        parameters=(),
    )
    assert plan.disposition is Disposition.RESOLVE


def test_repeated_calls_return_equal_plans() -> None:
    proposal = _proposal()
    plugin = _Plugin({"ability_check": ("dc",)})
    context = _context()
    parameters = (
        MechanicalParameter(name="dc", value=15, source=ParameterSource.RULING),
    )
    first = plan_resolution(
        proposal, plugin=plugin, context=context, parameters=parameters
    )
    second = plan_resolution(
        proposal, plugin=plugin, context=context, parameters=parameters
    )
    assert first == second


def test_a_plan_reports_why_it_stopped() -> None:
    plan = plan_resolution(
        _proposal(),
        plugin=_Plugin({"ability_check": ("dc",)}),
        context=_context(),
        parameters=(),
    )
    assert plan.reason
    assert "dc" in plan.reason


def test_a_partly_absent_target_asks_the_player_instead_of_acting() -> None:
    """Naming two targets when only one is present is ambiguous, not a detail.

    Silently narrowing to the present target would apply the effect to fewer
    entities than the player asked for. Carrying the absent one into the action
    would act on an entity that is not in the scene at all.
    """
    plan = plan_resolution(
        _proposal(target_refs=("npc-gate", "npc-hound")),
        plugin=_Plugin({"ability_check": ("dc",)}),
        context=_context(present=("pc-ada", "npc-gate")),
        parameters=(
            MechanicalParameter(name="dc", value=15, source=ParameterSource.RULING),
        ),
    )
    assert plan.disposition is Disposition.PLAYER_CLARIFICATION
    assert plan.action is None
    assert plan.clarification is not None
    assert "npc-hound" in plan.clarification.question
    # The player chooses from what is actually present.
    assert plan.clarification.candidate_refs == ("npc-gate",)


def test_a_declared_requirement_can_never_come_from_the_model() -> None:
    """The blocklist was hand-maintained, so an undeclared name slipped through.

    A plugin that had not declared its mechanical parameters let the model
    choose its own numbers. The authoritative set now comes from the plugin's
    own declaration, and a model-proposed value is dropped rather than
    forwarded.
    """
    class Needy(_Plugin):
        def action_requirements(self, action_type):
            if action_type == "ability_check":
                return ("dc", "amount")
            return ()

    plan = plan_resolution(
        _proposal(parameters={"amount": 9999}),
        plugin=Needy({"ability_check": ("dc", "amount")}),
        context=_context(),
        parameters=(
            MechanicalParameter(name="dc", value=15, source=ParameterSource.RULING),
        ),
    )
    # `amount` is declared, so the model's 9999 cannot satisfy it.
    assert plan.disposition is not Disposition.RESOLVE
    assert "amount" in plan.missing_parameters


def test_an_undeclared_free_choice_is_still_forwarded() -> None:
    """Which ability you use is a player's choice, not a rules fact.

    Only parameters a plugin declares are withheld, so a free choice reaches
    the plugin as the player made it.
    """
    plan = plan_resolution(
        _proposal(parameters={"ability": "strength"}),
        plugin=_Plugin({"ability_check": ("dc",)}),
        context=_context(),
        parameters=(
            MechanicalParameter(name="dc", value=15, source=ParameterSource.RULING),
        ),
    )
    assert plan.disposition is Disposition.RESOLVE
    assert plan.action is not None
    assert plan.action.parameters["ability"] == "strength"
