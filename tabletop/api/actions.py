"""Universal action model.

``GameAction`` is the system-agnostic representation of an actor's declared
intent. The generic runtime transports ``action_type``, ``targets``, and
``parameters`` without interpreting game mechanics. Armor class, saving
throws, hit locations, spell slots, mana, refresh, and sanity are plugin
meanings, never core fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from tabletop.api._contract import freeze_mapping, freeze_tuple, require_non_empty_str, to_jsonable
from tabletop.api.entities import EntityRef
from tabletop.api.errors import InvalidActionError


@dataclass(frozen=True)
class GameAction:
    """An attempted action awaiting plugin resolution.

    ``parameters`` are owned by the game-system plugin. The generic core
    stores them and must not interpret their keys. Callers must treat the
    mapping as immutable; construction takes a defensive deep copy and
    wraps it in ``MappingProxyType``.
    """

    actor: EntityRef
    action_type: str
    targets: tuple[EntityRef, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.actor, EntityRef):
            raise InvalidActionError(
                f"actor must be an EntityRef, got {type(self.actor).__name__}"
            )
        require_non_empty_str(self.action_type, "action_type", InvalidActionError)
        object.__setattr__(
            self,
            "targets",
            freeze_tuple(self.targets, EntityRef, "targets", InvalidActionError),
        )
        object.__setattr__(
            self,
            "parameters",
            freeze_mapping(self.parameters, "parameters", InvalidActionError),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor": self.actor.to_dict(),
            "action_type": self.action_type,
            "targets": [target.to_dict() for target in self.targets],
            "parameters": to_jsonable(self.parameters),
        }
