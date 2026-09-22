"""Phase 8 contract tests for universal action and resolution models."""

from __future__ import annotations

import json
from dataclasses import fields

import pytest

from systems.freeform import FreeformPlugin
from tabletop.api._contract import to_jsonable
from tabletop.api.actions import GameAction
from tabletop.api.capabilities import Capability
from tabletop.api.entities import EntityRef
from tabletop.api.errors import InvalidActionError, InvalidResolutionError
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


# -- EntityRef ----------------------------------------------------------------


def test_entity_ref_constructs_with_opaque_id():
    ref = EntityRef(id="mara")
    assert ref.id == "mara"
    assert ref.entity_type is None


def test_entity_ref_optional_entity_type_is_not_mechanically_interpreted():
    npc = EntityRef(id="ithrazel", entity_type="npc")
    obj = EntityRef(id="door-17", entity_type="object")
    assert npc.entity_type == "npc"
    assert obj.entity_type == "object"


def test_entity_ref_is_immutable_equal_and_hashable():
    a = EntityRef(id="mara")
    b = EntityRef(id="mara")
    assert a == b
    assert hash(a) == hash(b)
    assert a != EntityRef(id="cultist-1")
    assert a != EntityRef(id="mara", entity_type="pc")
    with pytest.raises((AttributeError, TypeError)):
        a.id = "other"  # type: ignore[misc]


def test_entity_ref_rejects_empty_id():
    with pytest.raises(InvalidActionError):
        EntityRef(id="")
    with pytest.raises(InvalidActionError):
        EntityRef(id="   ")
    with pytest.raises(InvalidActionError):
        EntityRef(id="  mara  ")
    with pytest.raises(InvalidActionError):
        EntityRef(id=None)  # type: ignore[arg-type]


def test_entity_ref_serializes_to_json_compatible_dict():
    payload = EntityRef(id="ithrazel", entity_type="npc").to_dict()
    assert payload == {"id": "ithrazel", "entity_type": "npc"}
    assert json.dumps(payload) == '{"id": "ithrazel", "entity_type": "npc"}'
    assert EntityRef(id="mara").to_dict()["entity_type"] is None


# -- GameAction ---------------------------------------------------------------


def _actor() -> EntityRef:
    return EntityRef(id="mara")


def test_game_action_requires_actor_and_action_type():
    action = GameAction(actor=_actor(), action_type="wave")
    assert action.actor == _actor()
    assert action.action_type == "wave"
    assert action.targets == ()
    assert dict(action.parameters) == {}


def test_game_action_accepts_zero_one_or_many_targets():
    none = GameAction(actor=_actor(), action_type="wait")
    one = GameAction(
        actor=_actor(),
        action_type="open",
        targets=(EntityRef(id="door-17", entity_type="object"),),
    )
    many = GameAction(
        actor=_actor(),
        action_type="address",
        targets=(EntityRef(id="gatekeeper"), EntityRef(id="scribe")),
    )
    assert none.targets == ()
    assert len(one.targets) == 1
    assert [ref.id for ref in many.targets] == ["gatekeeper", "scribe"]


def test_game_action_parameters_are_system_owned_and_arbitrary():
    action = GameAction(
        actor=_actor(),
        action_type="throw-teacup-at-moon",
        parameters={"approach": "flattery", "force": 3},
    )
    assert action.action_type == "throw-teacup-at-moon"
    assert action.parameters["approach"] == "flattery"
    names = {field.name for field in fields(GameAction)}
    assert names == {"actor", "action_type", "targets", "parameters"}


def test_game_action_rejects_empty_action_type():
    with pytest.raises(InvalidActionError):
        GameAction(actor=_actor(), action_type="")
    with pytest.raises(InvalidActionError):
        GameAction(actor=_actor(), action_type="  ")
    with pytest.raises(InvalidActionError):
        GameAction(actor=_actor(), action_type="  wave  ")


def test_game_action_rejects_non_entity_actor_and_malformed_targets():
    with pytest.raises(InvalidActionError):
        GameAction(actor="mara", action_type="wave")  # type: ignore[arg-type]
    with pytest.raises(InvalidActionError):
        GameAction(actor=_actor(), action_type="wave", targets="mara")  # type: ignore[arg-type]
    with pytest.raises(InvalidActionError):
        GameAction(
            actor=_actor(),
            action_type="wave",
            targets=(_actor(), "cultist-1"),  # type: ignore[arg-type]
        )


def test_game_action_parameter_mutation_does_not_rewrite_constructed_action():
    params = {"x": 1, "nested": {"y": 2}}
    action = GameAction(actor=_actor(), action_type="wave", parameters=params)
    params["x"] = 99
    params["nested"]["y"] = 99
    assert action.parameters["x"] == 1
    assert action.parameters["nested"]["y"] == 2
    with pytest.raises(TypeError):
        action.parameters["x"] = 3  # type: ignore[index]


# -- ResolutionContext --------------------------------------------------------


def test_resolution_context_carries_ids_and_opaque_state():
    context = ResolutionContext(
        campaign_id="night-market",
        system_id="freeform",
        scene_id="docks",
        state={"scene": {"clock": 2}},
    )
    assert context.campaign_id == "night-market"
    assert context.system_id == "freeform"
    assert context.scene_id == "docks"
    assert context.state["scene"]["clock"] == 2
    names = {field.name for field in fields(ResolutionContext)}
    assert names == {"campaign_id", "system_id", "scene_id", "state"}


def test_resolution_context_scene_is_optional_and_state_is_defensively_copied():
    state = {"hp": 12}
    context = ResolutionContext(campaign_id="c1", system_id="s1", state=state)
    assert context.scene_id is None
    state["hp"] = 1
    assert context.state["hp"] == 12
    with pytest.raises(InvalidResolutionError):
        ResolutionContext(campaign_id="", system_id="s1")
    with pytest.raises(InvalidResolutionError):
        ResolutionContext(campaign_id="c1", system_id="")


def test_resolution_context_serializes_without_runtime_services():
    payload = ResolutionContext(
        campaign_id="c1",
        system_id="freeform",
        state={"note": "opaque"},
    ).to_dict()
    assert payload["campaign_id"] == "c1"
    assert payload["scene_id"] is None
    assert payload["state"] == {"note": "opaque"}
    json.dumps(payload)


# -- RollResult, StateChange, RuleReference, GameEvent ------------------------


def test_roll_result_is_a_transport_contract_not_a_roller():
    roll = RollResult(expression="3d6", total=11, details={"dice": [3, 4, 4]})
    assert roll.expression == "3d6"
    assert roll.total == 11
    names = {field.name for field in fields(RollResult)}
    assert "critical_success" not in names
    assert "advantage" not in names
    assert "success_margin" not in names


def test_state_change_describes_desired_mutation_without_applying_it():
    change = StateChange(
        operation=StateOperation.SET,
        path=("entities", "cultist-1", "system", "hp"),
        value=7,
    )
    assert change.operation is StateOperation.SET
    assert change.path == ("entities", "cultist-1", "system", "hp")
    assert change.value == 7
    names = {field.name for field in fields(StateChange)}
    assert names == {"operation", "path", "value"}
    assert "previous_value" not in names


def test_state_change_path_keeps_dotted_keys_as_single_components():
    change = StateChange(
        operation=StateOperation.SET,
        path=("entities", "cultist.1", "system.hp"),
        value=7,
    )
    assert change.path == ("entities", "cultist.1", "system.hp")
    assert "." in change.path[1]
    assert change.to_dict()["path"] == ["entities", "cultist.1", "system.hp"]
    json.dumps(change.to_dict())
    indexed = StateChange(
        operation=StateOperation.SET,
        path=("inventory", 0, "qty"),
        value=1,
    )
    assert indexed.path == ("inventory", 0, "qty")
    with pytest.raises(InvalidResolutionError):
        StateChange(
            operation=StateOperation.SET,
            path="entities.cultist.1.system.hp",  # type: ignore[arg-type]
        )
    with pytest.raises(InvalidResolutionError):
        StateChange(operation=StateOperation.SET, path=())
    with pytest.raises(InvalidResolutionError):
        StateChange(operation=StateOperation.SET, path=("  entities  ",))


def test_state_change_rejects_unknown_operations_and_delete_values():
    with pytest.raises(InvalidResolutionError):
        StateChange(
            operation="summon-capybara-and-increment-moon",  # type: ignore[arg-type]
            path=("whatever",),
        )
    deleted = StateChange(operation=StateOperation.DELETE, path=("flags", "door"))
    assert deleted.operation is StateOperation.DELETE
    assert deleted.value is None
    with pytest.raises(InvalidResolutionError):
        StateChange(
            operation=StateOperation.DELETE,
            path=("flags", "door"),
            value=True,
        )


def test_to_jsonable_string_enums_become_plain_strings():
    converted = to_jsonable(StateOperation.SET)
    assert converted == "set"
    assert type(converted) is str


def test_opaque_mappings_reject_non_json_values_at_construction():
    with pytest.raises(InvalidActionError):
        GameAction(actor=_actor(), action_type="wave", parameters={"bad": {1, 2}})
    with pytest.raises(InvalidResolutionError):
        Resolution(outcome={"bad": object()})
    with pytest.raises(InvalidResolutionError):
        ResolutionContext(campaign_id="c1", system_id="s1", state={"bad": object()})
    with pytest.raises(InvalidResolutionError):
        RollResult(expression="1d6", total=3, details={"bad": float("nan")})
    with pytest.raises(InvalidResolutionError):
        RollResult(expression="1d6", total=float("inf"))
    with pytest.raises(InvalidResolutionError):
        RollResult(expression="1d6", total=3, details={"bad": float("-inf")})


def test_rule_reference_is_transport_only():
    ref = RuleReference(
        source_id="basic-set",
        title="Active Defense",
        section="B374",
        page=374,
        document_path="library/processed/gurps/basic-set.md",
        chunk_id="chunk-88",
    )
    assert ref.source_id == "basic-set"
    assert ref.page == 374
    assert ref.to_dict()["chunk_id"] == "chunk-88"
    with pytest.raises(InvalidResolutionError):
        RuleReference(source_id="")
    names = {field.name for field in fields(RuleReference)}
    assert names == {
        "source_id",
        "title",
        "section",
        "page",
        "document_path",
        "chunk_id",
    }


@pytest.mark.parametrize(
    "event_type,payload",
    [
        ("resource_changed", {"resource": "social-obligation", "delta": 1}),
        ("relationship_changed", {"from": "mara", "to": "gatekeeper", "stance": "wary"}),
        ("door_opened", {"door_id": "door-17"}),
    ],
)
def test_game_event_is_a_proposed_event_not_a_persisted_row(event_type, payload):
    event = GameEvent(
        event_type=event_type,
        payload=payload,
        actor=_actor(),
        target=EntityRef(id="door-17", entity_type="object"),
    )
    names = {field.name for field in fields(GameEvent)}
    assert "sequence" not in names
    assert "campaign_id" not in names
    assert "session_id" not in names
    assert "timestamp" not in names
    assert event.event_type == event_type
    assert event.payload == payload


# -- Resolution ---------------------------------------------------------------


def test_minimal_resolution_has_opaque_outcome_and_optional_dice():
    resolution = Resolution(outcome={})
    assert dict(resolution.outcome) == {}
    assert resolution.rolls == ()
    assert resolution.state_changes == ()
    assert resolution.rule_references == ()
    assert resolution.events == ()
    assert resolution.requires_ruling is False
    assert resolution.ruling_question is None
    names = {field.name for field in fields(Resolution)}
    assert "success" not in names
    assert "damage" not in names
    assert "margin" not in names


def test_resolution_accepts_multiple_rolls_changes_refs_and_events():
    resolution = Resolution(
        outcome={"status": "complete"},
        rolls=(
            RollResult(expression="1d6", total=4),
            RollResult(expression="1d6", total=2.5),
        ),
        state_changes=(
            StateChange(operation=StateOperation.SET, path=("flags", "door"), value="open"),
        ),
        rule_references=(RuleReference(source_id="freeform-guide", title="Doors"),),
        events=(GameEvent(event_type="door_opened"),),
        explanation="Roll total 14 meets target 12.",
    )
    assert len(resolution.rolls) == 2
    assert resolution.state_changes[0].path == ("flags", "door")
    assert resolution.rule_references[0].source_id == "freeform-guide"
    assert resolution.events[0].event_type == "door_opened"
    assert resolution.explanation == "Roll total 14 meets target 12."


def test_resolution_ruling_required_needs_a_question():
    ruling = Resolution(
        outcome={"status": "ambiguous"},
        status=ResolutionStatus.RULING_REQUIRED,
        ruling_question="Does the gatekeeper accept flattery as payment?",
    )
    assert ruling.requires_ruling is True
    with pytest.raises(InvalidResolutionError):
        Resolution(outcome={}, status=ResolutionStatus.RULING_REQUIRED)
    with pytest.raises(InvalidResolutionError):
        Resolution(
            outcome={}, status=ResolutionStatus.RULING_REQUIRED, ruling_question=""
        )
    with pytest.raises(InvalidResolutionError):
        Resolution(outcome={}, ruling_question="why?")


def test_resolution_outcome_mutation_does_not_rewrite_constructed_result():
    outcome = {"status": "ok"}
    resolution = Resolution(outcome=outcome)
    outcome["status"] = "mutated"
    assert resolution.outcome["status"] == "ok"


def test_complete_resolution_serializes_to_json_primitives():
    resolution = Resolution(
        outcome={"status": "awaiting-response", "response_type": "defense"},
        rolls=(RollResult(expression="3d6", total=9, details={"dice": [2, 3, 4]}),),
        state_changes=(
            StateChange(
                operation=StateOperation.SET,
                path=("entities", "cultist-1", "system", "fp"),
                value=5,
            ),
        ),
        rule_references=(
            RuleReference(source_id="basic-set", section="B374", page=374),
        ),
        events=(
            GameEvent(
                event_type="resource_changed",
                payload={"resource": "fp", "value": 5},
                actor=_actor(),
                target=EntityRef(id="cultist-1"),
            ),
        ),
        explanation="Attack roll 9 is a hit; defense is now required.",
    )
    payload = resolution.to_dict()
    encoded = json.dumps(payload)
    restored = json.loads(encoded)
    assert restored["rolls"][0]["total"] == 9
    assert restored["state_changes"][0]["path"] == ["entities", "cultist-1", "system", "fp"]
    assert restored["state_changes"][0]["operation"] == "set"
    assert restored["rule_references"][0]["page"] == 374
    assert restored["events"][0]["actor"]["id"] == "mara"
    assert restored["outcome"]["response_type"] == "defense"


# -- Plugin resolve signature -------------------------------------------------


class WavePlugin(GameSystemPlugin):
    """Tiny plugin proving resolve() without any combat semantics."""

    @property
    def info(self) -> GameSystemInfo:
        return GameSystemInfo(
            id="wave",
            name="Wave",
            api_version=TABLETOP_PLUGIN_API_VERSION,
        )

    def capabilities(self) -> frozenset[Capability]:
        return frozenset()

    def resolve(self, action: GameAction, context: ResolutionContext) -> Resolution:
        assert context.campaign_id == "c1"
        if action.action_type == "wave":
            return Resolution(outcome={"acknowledged": True})
        return Resolution(
            outcome={"status": "unknown-action"},
            status=ResolutionStatus.RULING_REQUIRED,
            ruling_question="What does this action mean in play?",
        )


def test_plugin_resolve_can_be_called_and_returns_resolution():
    plugin = WavePlugin()
    result = plugin.resolve(
        GameAction(actor=_actor(), action_type="wave"),
        ResolutionContext(campaign_id="c1", system_id="wave"),
    )
    assert isinstance(result, Resolution)
    assert result.outcome["acknowledged"] is True
    assert result.rolls == ()


def test_freeform_advertises_dice_and_action_resolution():
    plugin = FreeformPlugin()
    assert plugin.supports(Capability.ACTION_RESOLUTION)
    assert plugin.supports(Capability.DICE)


# -- Falsification ------------------------------------------------------------


def test_dnd_shaped_payload_fits_without_core_combat_fields():
    action = GameAction(
        actor=_actor(),
        action_type="attack",
        targets=(EntityRef(id="cultist-1"),),
        parameters={"weapon_id": "longsword-1"},
    )
    resolution = Resolution(
        outcome={"success": True, "attack_total": 18, "damage": 7},
        state_changes=(
            StateChange(
                operation=StateOperation.SET,
                path=("entities", "cultist-1", "system", "hp"),
                value=7,
            ),
        ),
        events=(GameEvent(event_type="resource_changed", payload={"path": "hp"}),),
    )
    assert "armor_class" not in action.parameters
    assert "success" in resolution.outcome
    assert "success" not in {field.name for field in fields(Resolution)}


def test_gurps_shaped_payload_can_require_a_follow_up_without_binary_success():
    action = GameAction(
        actor=_actor(),
        action_type="attack",
        targets=(EntityRef(id="cultist-1"),),
        parameters={"maneuver": "all-out-attack", "hit_location": "torso"},
    )
    resolution = Resolution(
        outcome={
            "status": "awaiting-response",
            "response_type": "defense",
            "margin": 3,
        },
        rolls=(RollResult(expression="3d6", total=8),),
    )
    assert action.parameters["maneuver"] == "all-out-attack"
    assert resolution.outcome["status"] == "awaiting-response"
    assert not hasattr(resolution, "defense_required")


def test_dying_earth_social_exchange_fits_without_combat_fields():
    action = GameAction(
        actor=_actor(),
        action_type="persuade",
        targets=(EntityRef(id="gatekeeper"),),
        parameters={"approach": "flattery", "pool": "persuasion"},
    )
    resolution = Resolution(
        outcome={"result": "mixed-success", "cost": "social-obligation"},
        events=(
            GameEvent(
                event_type="relationship_changed",
                payload={"stance": "amused-debt"},
            ),
        ),
    )
    assert action.action_type == "persuade"
    assert "damage" not in resolution.outcome
    assert resolution.rolls == ()


def test_diceless_resolution_is_valid_with_zero_rolls():
    resolution = Resolution(outcome={"result": "accepted"})
    assert resolution.rolls == ()
    json.dumps(resolution.to_dict())
