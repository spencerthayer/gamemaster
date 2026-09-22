"""Turn handling.

Drives one turn: action intake, deterministic resolution via the active
system plugin, event emission, and response assembly. The LLM proposes
intent; the runtime and plugins resolve mechanics.

Phase 9 fills in the resolution step and its guard. Event emission and
response assembly stay with Phases 12 and 30.

The guard is the point of this module: when the active system advertises
``Capability.ACTION_RESOLUTION``, :func:`resolve_action` must call that
plugin, and it returns the plugin's ``Resolution`` unchanged. There is no
branch here that manufactures a mechanical result, and callers have no
other supported path to one.
"""

from __future__ import annotations

from tabletop.api.actions import GameAction
from tabletop.api.capabilities import Capability
from tabletop.api.errors import InvalidResolutionError
from tabletop.api.resolution import Resolution, ResolutionContext, ResolutionStatus
from tabletop.plugins.registry import PluginRegistry


def resolve_action(
    registry: PluginRegistry,
    action: GameAction,
    context: ResolutionContext,
) -> Resolution:
    """Resolve one action through the system named by ``context.system_id``.

    Returns ``UNSUPPORTED`` when the active system does not advertise
    ``ACTION_RESOLUTION``. Otherwise the plugin decides, including its own
    ``UNSUPPORTED`` for action categories it does not cover.

    An unknown system id is a configuration failure, not a game outcome, so
    ``PluginNotFoundError`` propagates. A plugin returning a non-``Resolution``
    fails closed rather than being coerced into one.
    """
    plugin = registry.get(context.system_id)
    if not plugin.supports(Capability.ACTION_RESOLUTION):
        return Resolution(
            outcome={},
            status=ResolutionStatus.UNSUPPORTED,
            explanation=(
                f"system {context.system_id!r} does not implement action resolution"
            ),
        )
    resolution = plugin.resolve(action, context)
    if not isinstance(resolution, Resolution):
        raise InvalidResolutionError(
            f"system {context.system_id!r} returned "
            f"{type(resolution).__name__}, expected Resolution"
        )
    return resolution
