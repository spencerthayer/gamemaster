"""``ActionProposal`` is model intent; ``GameAction`` is mechanical authority.

A proposal may be vague, uncertain, or targeted at nothing. It must be
expressible without weakening ``GameAction``, and parsing one must never
create authoritative state.
"""

from __future__ import annotations

import json

import pytest

from tabletop.api.actions import ActionProposal, GameAction, parse_action_proposal
from tabletop.api.entities import EntityRef
from tabletop.api.errors import InvalidActionError


def _proposal(**overrides: object) -> ActionProposal:
    payload: dict[str, object] = {
        "actor_id": "pc-ada",
        "intent": "Ada tries to force the rusted gate open",
        "proposed_action_type": None,
        "target_refs": ["npc-gate"],
        "parameters": {},
        "uncertainty": "Which side of the gate is she on?",
        "needs_resolution": True,
    }
    payload.update(overrides)
    return ActionProposal(**payload)  # type: ignore[arg-type]


def test_proposal_carries_intent_without_mechanical_certainty() -> None:
    proposal = _proposal()
    assert proposal.actor_id == "pc-ada"
    assert proposal.intent.startswith("Ada tries")
    assert proposal.proposed_action_type is None
    assert proposal.target_refs == ("npc-gate",)
    assert proposal.needs_resolution is True


def test_proposal_is_frozen() -> None:
    proposal = _proposal()
    with pytest.raises(Exception):
        proposal.intent = "something else"  # type: ignore[misc]


def test_proposal_copies_its_collections() -> None:
    refs = ["npc-gate"]
    parameters = {"path": ["scene", "system", "lock"]}
    proposal = _proposal(target_refs=refs, parameters=parameters)
    refs.append("npc-other")
    parameters["path"].append("tampered")
    assert proposal.target_refs == ("npc-gate",)
    # Nested sequences are frozen to tuples, so a later caller-side append
    # cannot reach into an already-built proposal.
    assert proposal.parameters == {"path": ("scene", "system", "lock")}


def test_proposal_rejects_an_empty_actor() -> None:
    with pytest.raises(InvalidActionError):
        _proposal(actor_id="")


def test_proposal_rejects_a_non_string_intent() -> None:
    with pytest.raises(InvalidActionError):
        _proposal(intent=42)


def test_proposal_rejects_an_empty_target_ref() -> None:
    with pytest.raises(InvalidActionError):
        _proposal(target_refs=[""])


def test_proposal_needs_resolution_defaults_to_true() -> None:
    proposal = ActionProposal(
        actor_id="pc-ada",
        intent="Ada waits and watches",
        uncertainty=None,
    )
    assert proposal.needs_resolution is True
    assert proposal.proposed_action_type is None
    assert proposal.target_refs == ()
    assert proposal.parameters == {}


def test_uncertainty_may_be_omitted_when_the_intent_is_certain() -> None:
    proposal = _proposal(uncertainty=None, needs_resolution=False)
    assert proposal.uncertainty is None
    assert proposal.needs_resolution is False


def test_proposal_serializes_to_json_safe_data() -> None:
    payload = _proposal().to_dict()
    assert json.loads(json.dumps(payload)) == payload


def test_parse_action_proposal_reads_a_valid_mapping() -> None:
    proposal = parse_action_proposal(
        {
            "actor_id": "pc-ada",
            "intent": "Ada tries to force the rusted gate open",
            "proposed_action_type": "ability_check",
            "target_refs": ["npc-gate"],
            "parameters": {"ability": "strength"},
            "uncertainty": "No difficulty class is known yet.",
            "needs_resolution": True,
        }
    )
    assert isinstance(proposal, ActionProposal)
    assert proposal.proposed_action_type == "ability_check"
    assert proposal.parameters == {"ability": "strength"}


def test_parse_action_proposal_omits_optional_fields() -> None:
    proposal = parse_action_proposal(
        {"actor_id": "pc-ada", "intent": "Ada waits and watches"}
    )
    assert proposal.proposed_action_type is None
    assert proposal.target_refs == ()
    assert proposal.uncertainty is None
    assert proposal.needs_resolution is True


def test_parse_action_proposal_rejects_a_non_mapping() -> None:
    with pytest.raises(InvalidActionError):
        parse_action_proposal(["not", "a", "mapping"])


def test_parse_action_proposal_rejects_a_missing_actor() -> None:
    with pytest.raises(InvalidActionError, match="actor_id"):
        parse_action_proposal({"intent": "Ada waits"})


def test_parse_action_proposal_rejects_unknown_fields() -> None:
    """An unrecognized key is a model mistake worth surfacing, not dropping."""

    with pytest.raises(InvalidActionError, match="unexpected"):
        parse_action_proposal(
            {
                "actor_id": "pc-ada",
                "intent": "Ada waits",
                "difficulty_class": 15,
            }
        )


def test_parse_action_proposal_rejects_non_json_values() -> None:
    with pytest.raises(InvalidActionError):
        parse_action_proposal(
            {"actor_id": "pc-ada", "intent": "Ada waits", "parameters": {"fn": object()}}
        )


def test_a_proposal_does_not_become_a_game_action_by_construction() -> None:
    """Only the planner may build a GameAction, and only from resolved input."""

    proposal = _proposal()
    assert not isinstance(proposal, GameAction)
    assert proposal.proposed_action_type is None


def test_game_action_contract_is_unchanged() -> None:
    action = GameAction(
        actor=EntityRef(id="pc-ada"),
        action_type="ability_check",
        parameters={"ability": "strength"},
    )
    assert action.to_dict()["action_type"] == "ability_check"
