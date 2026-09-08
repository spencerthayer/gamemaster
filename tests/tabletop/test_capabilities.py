"""Capability declaration tests."""

import json

from tabletop.api.capabilities import Capability


def test_capability_values_are_stable_strings():
    assert Capability.DICE == "dice"
    assert Capability.ACTION_RESOLUTION == "action-resolution"
    assert Capability.OPPOSED_RESOLUTION == "opposed-resolution"
    assert Capability.TURN_ORDER == "turn-order"
    assert Capability.HIT_LOCATIONS == "hit-locations"
    assert Capability.SOCIAL_CONFLICT == "social-conflict"


def test_capability_serializes_predictably():
    assert json.dumps(Capability.DICE.value) == '"dice"'
    assert str(Capability.RESOURCE_TRACKING) == "Capability.RESOURCE_TRACKING"
    assert Capability.MAGIC.value == "magic"


def test_capabilities_are_enumerable_and_unique():
    values = [capability.value for capability in Capability]
    assert len(values) == len(set(values))
    assert len(values) == 13
