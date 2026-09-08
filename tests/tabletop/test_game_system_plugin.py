"""Phase 6 contract tests for the game-system plugin API."""

import pytest

from systems.dnd5e import Dnd5ePlugin
from systems.freeform import FreeformPlugin
from tabletop.api.capabilities import Capability
from tabletop.api.errors import (
    InvalidPluginConfigurationError,
    UnsupportedCapabilityError,
)
from tabletop.api.plugin import (
    TABLETOP_PLUGIN_API_VERSION,
    GameSystemInfo,
    GameSystemPlugin,
    ValidationIssue,
    ValidationResult,
)


class MinimalPlugin(GameSystemPlugin):
    """Smallest possible conforming plugin."""

    @property
    def info(self) -> GameSystemInfo:
        return GameSystemInfo(id="minimal", name="Minimal", api_version=TABLETOP_PLUGIN_API_VERSION)

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.DICE})

    def resolve(self, action, context):  # pragma: no cover - Phase 8 model
        raise NotImplementedError


class DicePlugin(MinimalPlugin):
    @property
    def info(self) -> GameSystemInfo:
        return GameSystemInfo(id="dicey", name="Dicey", api_version=TABLETOP_PLUGIN_API_VERSION)

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.DICE, Capability.SOCIAL_CONFLICT})


class IncompletePlugin(GameSystemPlugin):
    """Missing info, capabilities, and resolve."""


# Metadata


def test_api_version_is_central_and_exact_matched():
    assert TABLETOP_PLUGIN_API_VERSION == "tabletop/v1"
    from tabletop.api.plugin import is_compatible_api_version

    assert is_compatible_api_version("tabletop/v1")
    assert not is_compatible_api_version("tabletop/v2")
    assert not is_compatible_api_version("other/v1")


def test_metadata_is_immutable_and_validated():
    info = GameSystemInfo(
        id="freeform", name="Freeform", api_version=TABLETOP_PLUGIN_API_VERSION
    )
    with pytest.raises((AttributeError, TypeError)):
        info.id = "other"  # type: ignore[misc]
    with pytest.raises(InvalidPluginConfigurationError):
        GameSystemInfo(id="Bad Id", name="x", api_version=TABLETOP_PLUGIN_API_VERSION)
    with pytest.raises(InvalidPluginConfigurationError):
        GameSystemInfo(id="ok", name="x", api_version="1.0")


def test_plugin_version_is_distinct_from_api_version():
    info = GameSystemInfo(
        id="sys",
        name="Sys",
        api_version=TABLETOP_PLUGIN_API_VERSION,
        version="0.3.0",
    )
    assert info.version == "0.3.0"
    assert info.api_version == "tabletop/v1"


# Capabilities and negotiation


def test_plugin_reports_supported_and_unsupported_capabilities():
    plugin = DicePlugin()
    assert plugin.supports(Capability.DICE)
    assert plugin.supports(Capability.SOCIAL_CONFLICT)
    assert not plugin.supports(Capability.HIT_LOCATIONS)


def test_require_capability_succeeds_for_supported_capability():
    DicePlugin().require_capability(Capability.DICE)  # no exception


def test_require_capability_raises_for_unsupported_capability():
    plugin = DicePlugin()
    with pytest.raises(UnsupportedCapabilityError) as excinfo:
        plugin.require_capability(Capability.HIT_LOCATIONS)
    assert excinfo.value.system_id == "dicey"
    assert excinfo.value.capability is Capability.HIT_LOCATIONS


def test_negotiation_does_not_leak_state_between_plugins():
    dicey = DicePlugin()
    freeform = FreeformPlugin()
    assert dicey.supports(Capability.DICE)
    assert not freeform.supports(Capability.DICE)
    assert dicey.capabilities() != freeform.capabilities()
    with pytest.raises(UnsupportedCapabilityError):
        freeform.require_capability(Capability.DICE)
    dicey.require_capability(Capability.DICE)  # unchanged by freeform's failure


# Base contract


def test_incomplete_subclass_cannot_instantiate():
    with pytest.raises(TypeError):
        IncompletePlugin()


def test_minimal_plugin_instantiates_and_lifecycle_defaults_are_safe():
    plugin = MinimalPlugin()
    plugin.initialize()
    plugin.shutdown()


def test_freeform_plugin_conforms_and_advertises_nothing():
    plugin = FreeformPlugin()
    assert plugin.info.id == "freeform"
    assert plugin.info.api_version == TABLETOP_PLUGIN_API_VERSION
    assert plugin.capabilities() == frozenset()
    assert plugin.character_schema() == {}
    assert plugin.state_schema() == {}
    assert plugin.rule_namespaces() == frozenset()
    plugin.initialize()
    plugin.shutdown()


def test_dnd5e_stub_conforms_without_advertising_dnd_capabilities():
    plugin = Dnd5ePlugin()
    assert plugin.info.id == "dnd5e"
    assert plugin.capabilities() == frozenset()
    assert plugin.supports(Capability.MAGIC) is False


# Validation


def test_validation_defaults_to_accepting():
    assert MinimalPlugin().validate_state({"anything": ["goes"]}) == ValidationResult.ok()


def test_validation_reports_structured_issues():
    class StrictPlugin(MinimalPlugin):
        def validate_state(self, state):
            if "name" not in state:
                return ValidationResult(
                    valid=False,
                    issues=(
                        ValidationIssue(
                            path="name", code="missing", message="name is required"
                        ),
                    ),
                )
            return ValidationResult.ok()

    result = StrictPlugin().validate_state({"other": 1})
    assert result.valid is False
    assert result.issues[0].path == "name"
    assert result.issues[0].code == "missing"

    assert StrictPlugin().validate_state({"name": "x"}).valid is True
