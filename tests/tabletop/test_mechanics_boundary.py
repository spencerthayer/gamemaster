"""Phase 9: the deterministic mechanics boundary.

These tests exist to stop one specific failure: something other than the
active system plugin producing a mechanical result. They check the closed
status taxonomy, its invariants, the orchestrator guard, and the
adjudication boundary.
"""

from __future__ import annotations

import pytest

from tabletop.api.actions import GameAction
from tabletop.api.capabilities import Capability
from tabletop.api.entities import EntityRef
from tabletop.api.errors import InvalidResolutionError, PluginNotFoundError
from tabletop.api.events import GameEvent
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
from tabletop.api.rules import RuleReference
from tabletop.orchestration.adjudication import (
    AdjudicationRequest,
    AdjudicationResult,
    adjudication_request,
    requires_adjudication,
)
from tabletop.orchestration.turn import resolve_action
from tabletop.plugins.registry import PluginRegistry


def _action(action_type: str = "shove") -> GameAction:
    return GameAction(actor=EntityRef(id="pc-1"), action_type=action_type)


def _context(system_id: str) -> ResolutionContext:
    return ResolutionContext(campaign_id="c1", system_id=system_id)


class _Plugin(GameSystemPlugin):
    """Test double recording whether resolve() was reached."""

    def __init__(
        self,
        system_id: str,
        capabilities: frozenset[Capability],
        result: Resolution | object = None,
    ) -> None:
        self._id = system_id
        self._capabilities = capabilities
        self._result = result
        self.calls: list[GameAction] = []

    @property
    def info(self) -> GameSystemInfo:
        return GameSystemInfo(
            id=self._id, name=self._id, api_version=TABLETOP_PLUGIN_API_VERSION
        )

    def capabilities(self) -> frozenset[Capability]:
        return self._capabilities

    def resolve(self, action: GameAction, context: ResolutionContext) -> Resolution:
        self.calls.append(action)
        return self._result


def _registry(plugin: GameSystemPlugin) -> PluginRegistry:
    registry = PluginRegistry()
    registry.register(plugin)
    return registry


# --- status taxonomy -------------------------------------------------------


def test_status_is_a_closed_four_member_set():
    assert {status.value for status in ResolutionStatus} == {
        "resolved",
        "ruling-required",
        "unresolved",
        "unsupported",
    }


def test_default_status_is_resolved_and_requires_ruling_is_derived():
    resolution = Resolution(outcome={"hit": True})
    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.requires_ruling is False
    assert "requires_ruling" not in resolution.__dict__


def test_requires_ruling_cannot_disagree_with_status():
    ruling = Resolution(
        outcome={},
        status=ResolutionStatus.RULING_REQUIRED,
        ruling_question="Does cover apply here?",
    )
    assert ruling.requires_ruling is True
    with pytest.raises(AttributeError):
        ruling.requires_ruling = False  # type: ignore[misc]


def test_status_accepts_its_wire_string_and_rejects_anything_else():
    assert Resolution(outcome={}, status="resolved").status is ResolutionStatus.RESOLVED
    for bad in ("RESOLVED", "ruling_required", "pending", 1, None):
        with pytest.raises(InvalidResolutionError):
            Resolution(outcome={}, status=bad)


def test_status_serializes_alongside_the_derived_flag():
    payload = Resolution(
        outcome={},
        status=ResolutionStatus.UNRESOLVED,
        explanation="Target difficulty is missing.",
    ).to_dict()
    assert payload["status"] == "unresolved"
    assert payload["requires_ruling"] is False
    assert payload["explanation"] == "Target difficulty is missing."


# --- invariants: no mechanical result without RESOLVED ---------------------


@pytest.mark.parametrize(
    "status", [ResolutionStatus.UNRESOLVED, ResolutionStatus.UNSUPPORTED]
)
def test_non_resolved_statuses_carry_no_mechanical_result(status):
    reason = "Target difficulty is missing."
    assert Resolution(outcome={}, status=status, explanation=reason).outcome == {}
    with pytest.raises(InvalidResolutionError):
        Resolution(outcome={"damage": 6}, status=status, explanation=reason)
    with pytest.raises(InvalidResolutionError):
        Resolution(
            outcome={},
            status=status,
            explanation=reason,
            rolls=(RollResult(expression="1d20", total=17),),
        )
    with pytest.raises(InvalidResolutionError):
        Resolution(
            outcome={},
            status=status,
            explanation=reason,
            state_changes=(
                StateChange(
                    operation=StateOperation.SET, path=("hp",), value=3
                ),
            ),
        )
    with pytest.raises(InvalidResolutionError):
        Resolution(
            outcome={},
            status=status,
            explanation=reason,
            events=(GameEvent(event_type="hit"),),
        )


@pytest.mark.parametrize(
    "status", [ResolutionStatus.UNRESOLVED, ResolutionStatus.UNSUPPORTED]
)
def test_non_resolved_statuses_must_say_what_is_missing(status):
    with pytest.raises(InvalidResolutionError):
        Resolution(outcome={}, status=status)
    with pytest.raises(InvalidResolutionError):
        Resolution(outcome={}, status=status, explanation="")


@pytest.mark.parametrize(
    "status", [ResolutionStatus.UNRESOLVED, ResolutionStatus.UNSUPPORTED]
)
def test_non_resolved_statuses_may_still_cite_rules(status):
    resolution = Resolution(
        outcome={},
        status=status,
        explanation="No target number for improvised climbing.",
        rule_references=(RuleReference(source_id="core", title="Climbing"),),
    )
    assert resolution.rule_references[0].title == "Climbing"


def test_ruling_required_may_roll_but_may_not_change_state_or_emit_events():
    ruling = Resolution(
        outcome={"total": 17},
        status=ResolutionStatus.RULING_REQUIRED,
        ruling_question="Does a 17 beat improvised cover?",
        rolls=(RollResult(expression="1d20", total=17),),
    )
    assert ruling.rolls[0].total == 17
    with pytest.raises(InvalidResolutionError):
        Resolution(
            outcome={},
            status=ResolutionStatus.RULING_REQUIRED,
            ruling_question="Does it?",
            state_changes=(
                StateChange(operation=StateOperation.SET, path=("hp",), value=3),
            ),
        )
    with pytest.raises(InvalidResolutionError):
        Resolution(
            outcome={},
            status=ResolutionStatus.RULING_REQUIRED,
            ruling_question="Does it?",
            events=(GameEvent(event_type="hit"),),
        )


def test_only_ruling_required_carries_a_question():
    for status in (
        ResolutionStatus.RESOLVED,
        ResolutionStatus.UNRESOLVED,
        ResolutionStatus.UNSUPPORTED,
    ):
        with pytest.raises(InvalidResolutionError):
            Resolution(
                outcome={},
                status=status,
                explanation="reason",
                ruling_question="why?",
            )


# --- orchestrator guard ----------------------------------------------------


def test_declared_capability_means_the_plugin_is_always_called():
    expected = Resolution(outcome={"hit": True})
    plugin = _Plugin("wave", frozenset({Capability.ACTION_RESOLUTION}), expected)
    action = _action()

    result = resolve_action(_registry(plugin), action, _context("wave"))

    assert plugin.calls == [action]
    assert result is expected


def test_plugin_result_is_returned_unchanged_including_refusals():
    refusal = Resolution(
        outcome={},
        status=ResolutionStatus.UNRESOLVED,
        explanation="Target difficulty is missing.",
    )
    plugin = _Plugin("wave", frozenset({Capability.ACTION_RESOLUTION}), refusal)

    result = resolve_action(_registry(plugin), _action(), _context("wave"))

    assert result is refusal
    assert result.outcome == {}


def test_system_without_the_capability_yields_unsupported_without_calling_it():
    plugin = _Plugin("lore", frozenset(), Resolution(outcome={"hit": True}))

    result = resolve_action(_registry(plugin), _action(), _context("lore"))

    assert plugin.calls == []
    assert result.status is ResolutionStatus.UNSUPPORTED
    assert result.outcome == {}
    assert result.rolls == ()
    assert "lore" in result.explanation


def test_orchestrator_never_manufactures_a_resolved_result():
    """The guard has no branch that invents mechanics.

    Whatever the plugin refuses to decide stays refused, and a system that
    cannot resolve actions gets UNSUPPORTED, never a fabricated outcome.
    """
    refusing = _Plugin(
        "wave",
        frozenset({Capability.ACTION_RESOLUTION}),
        Resolution(
            outcome={},
            status=ResolutionStatus.UNSUPPORTED,
            explanation="This system plugin does not implement vehicle chases.",
        ),
    )
    incapable = _Plugin("lore", frozenset())
    for plugin in (refusing, incapable):
        result = resolve_action(
            _registry(plugin), _action("vehicle-chase"), _context(plugin.info.id)
        )
        assert result.status is not ResolutionStatus.RESOLVED
        assert result.outcome == {}


def test_unknown_system_is_a_configuration_failure_not_a_game_outcome():
    with pytest.raises(PluginNotFoundError):
        resolve_action(PluginRegistry(), _action(), _context("absent"))


def test_a_plugin_returning_a_non_resolution_fails_closed():
    plugin = _Plugin(
        "wave", frozenset({Capability.ACTION_RESOLUTION}), {"hit": True}
    )
    with pytest.raises(InvalidResolutionError):
        resolve_action(_registry(plugin), _action(), _context("wave"))


# --- adjudication boundary -------------------------------------------------


def test_only_non_resolved_results_reach_adjudication():
    resolved = Resolution(outcome={"hit": True})
    assert requires_adjudication(resolved) is False
    with pytest.raises(InvalidResolutionError):
        adjudication_request(resolved, _action(), _context("wave"))


@pytest.mark.parametrize(
    ("resolution", "expected_detail"),
    [
        (
            Resolution(
                outcome={},
                status=ResolutionStatus.RULING_REQUIRED,
                ruling_question="Does flattery count as payment?",
            ),
            "Does flattery count as payment?",
        ),
        (
            Resolution(
                outcome={},
                status=ResolutionStatus.UNRESOLVED,
                explanation="Target difficulty is missing.",
            ),
            "Target difficulty is missing.",
        ),
        (
            Resolution(
                outcome={},
                status=ResolutionStatus.UNSUPPORTED,
                explanation="This system plugin does not implement vehicle chases.",
            ),
            "This system plugin does not implement vehicle chases.",
        ),
    ],
)
def test_each_non_resolved_status_crosses_the_boundary_with_its_own_detail(
    resolution, expected_detail
):
    action, context = _action(), _context("wave")
    request = adjudication_request(resolution, action, context)
    assert isinstance(request, AdjudicationRequest)
    assert request.status is resolution.status
    assert request.detail == expected_detail
    assert request.action is action
    assert request.context is context


def test_the_three_problems_are_not_presented_identically():
    headlines = {
        adjudication_request(resolution, _action(), _context("wave")).headline
        for resolution in (
            Resolution(
                outcome={},
                status=ResolutionStatus.RULING_REQUIRED,
                ruling_question="Does cover apply?",
            ),
            Resolution(
                outcome={},
                status=ResolutionStatus.UNRESOLVED,
                explanation="Target difficulty is missing.",
            ),
            Resolution(
                outcome={},
                status=ResolutionStatus.UNSUPPORTED,
                explanation="Vehicle chases are not implemented.",
            ),
        )
    }
    assert len(headlines) == 3


def test_a_ruling_ends_the_question_rather_than_deferring_it():
    request = adjudication_request(
        Resolution(
            outcome={},
            status=ResolutionStatus.RULING_REQUIRED,
            ruling_question="Does cover apply?",
        ),
        _action(),
        _context("wave"),
    )
    accepted = AdjudicationResult(
        request=request,
        decision="Half cover applies.",
        resolution=Resolution(outcome={"cover": "half"}),
    )
    assert accepted.resolution.status is ResolutionStatus.RESOLVED

    with pytest.raises(InvalidResolutionError):
        AdjudicationResult(request=request, decision="   ")
    with pytest.raises(InvalidResolutionError):
        AdjudicationResult(
            request=request,
            decision="Ask again later.",
            resolution=Resolution(
                outcome={},
                status=ResolutionStatus.UNRESOLVED,
                explanation="Still missing a target number.",
            ),
        )
