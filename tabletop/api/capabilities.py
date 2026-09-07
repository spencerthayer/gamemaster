"""Capability declarations for game-system plugins.

Capabilities are explicit identifiers advertising what a plugin currently
implements. They never execute behavior and the runtime never infers one
capability from another. String-backed for predictable serialization;
adding a future capability means adding one enum member here, not touching
every consumer.
"""

from __future__ import annotations

from enum import Enum


class Capability(str, Enum):
    """Capabilities a game-system plugin can advertise as implemented."""

    DICE = "dice"
    ACTION_RESOLUTION = "action-resolution"
    OPPOSED_RESOLUTION = "opposed-resolution"
    TURN_ORDER = "turn-order"
    DAMAGE = "damage"
    HEALING = "healing"
    CONDITIONS = "conditions"
    RESOURCE_TRACKING = "resource-tracking"
    EQUIPMENT = "equipment"
    MAGIC = "magic"
    CHARACTER_ADVANCEMENT = "character-advancement"
    HIT_LOCATIONS = "hit-locations"
    SOCIAL_CONFLICT = "social-conflict"
