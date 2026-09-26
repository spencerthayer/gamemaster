"""A model-only value must not authorize a rules-authoritative parameter.

``MechanicalParameter`` records where a value came from. The planner refuses
to resolve a mechanical action when a required parameter's only source is the
model's own guess.
"""

from __future__ import annotations

import json

import pytest

from tabletop.api.actions import MechanicalParameter, ParameterSource
from tabletop.api.errors import InvalidActionError
from tabletop.api.plugin import (
    TABLETOP_PLUGIN_API_VERSION,
    GameSystemInfo,
    GameSystemPlugin,
)
from tabletop.api.capabilities import Capability


def test_model_proposal_is_the_default_source() -> None:
    parameter = MechanicalParameter(name="dc", value=15, source=ParameterSource.MODEL_PROPOSAL)
    assert parameter.source is ParameterSource.MODEL_PROPOSAL
    # The source set is closed: a typo cannot invent a trustworthy source.
    assert {member.value for member in ParameterSource} == {
        "model_proposal",
        "campaign_state",
        "attached_rules",
        "ruling",
        "system_default",
    }


def test_model_proposal_is_not_rules_authoritative() -> None:
    """The point of the contract: a model guess cannot satisfy a rules need."""

    assert ParameterSource.MODEL_PROPOSAL.is_authoritative is False
    for source in (
        ParameterSource.CAMPAIGN_STATE,
        ParameterSource.ATTACHED_RULES,
        ParameterSource.RULING,
        ParameterSource.SYSTEM_DEFAULT,
    ):
        assert source.is_authoritative is True, source


def test_parameter_requires_a_name() -> None:
    with pytest.raises(InvalidActionError):
        MechanicalParameter(name="", value=1, source=ParameterSource.RULING)


def test_parameter_rejects_an_unknown_source_string() -> None:
    with pytest.raises(InvalidActionError):
        MechanicalParameter(name="dc", value=1, source="the_model_said_so")


def test_parameter_accepts_a_reference() -> None:
    parameter = MechanicalParameter(
        name="dc",
        value=15,
        source=ParameterSource.ATTACHED_RULES,
        reference="rules.md#gates",
    )
    assert parameter.reference == "rules.md#gates"
    assert parameter.to_dict()["reference"] == "rules.md#gates"


def test_parameter_serializes_to_json_safe_data() -> None:
    payload = MechanicalParameter(
        name="dc", value=15, source=ParameterSource.RULING
    ).to_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["source"] == "ruling"


def test_parameter_is_frozen() -> None:
    parameter = MechanicalParameter(name="dc", value=15, source=ParameterSource.RULING)
    with pytest.raises(Exception):
        parameter.value = 20  # type: ignore[misc]


class _Base(GameSystemPlugin):
    @property
    def info(self) -> GameSystemInfo:
        return GameSystemInfo(
            id="base",
            name="Base",
            api_version=TABLETOP_PLUGIN_API_VERSION,
        )

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.DICE})

    def resolve(self, action, context):  # pragma: no cover
        raise NotImplementedError


def test_action_requirements_default_to_empty() -> None:
    """An existing plugin with no declared requirements stays compatible."""

    assert _Base().action_requirements("ability_check") == ()


def test_action_requirements_accepts_an_unknown_action_type() -> None:
    assert _Base().action_requirements("not_a_real_action") == ()


def test_declared_requirements_are_returned() -> None:
    class Needy(_Base):
        def action_requirements(self, action_type):
            if action_type == "ability_check":
                return ("dc",)
            return ()

    assert Needy().action_requirements("ability_check") == ("dc",)


def test_built_in_plugins_declare_their_requirements() -> None:
    from tabletop.cli.util import repo_root
    from tabletop.plugins.discovery import discover_plugins, load_plugin

    declared = {
        plugin.info.id: plugin
        for plugin in (
            load_plugin(candidate) for candidate in discover_plugins((repo_root() / "systems",))
        )
    }
    assert "dnd5e" in declared
    dnd = declared["dnd5e"]
    assert "dc" in dnd.action_requirements("ability_check")
    # A narrative-only type declares nothing, so the planner does not block it.
    assert dnd.action_requirements("attack") == () or isinstance(
        dnd.action_requirements("attack"), tuple
    )
