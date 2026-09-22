"""Typed event model for resolution output.

``GameEvent`` is a proposed event emitted by resolution. It is not a
persisted event-store row. Sequence numbers, persistent event ids,
campaign/session ids, timestamps, and store offsets belong to Phase 12.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from tabletop.api._contract import freeze_mapping, require_non_empty_str, to_jsonable
from tabletop.api.entities import EntityRef
from tabletop.api.errors import InvalidResolutionError


@dataclass(frozen=True)
class GameEvent:
    """Something that happened, as described by a resolver.

    Distinct from ``StateChange``, which describes a desired mutation.
    ``payload`` is plugin-owned and opaque to the generic runtime.
    Construction copies mappings so later caller mutation cannot rewrite
    an already-built event.
    """

    event_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    actor: EntityRef | None = None
    target: EntityRef | None = None

    def __post_init__(self) -> None:
        require_non_empty_str(self.event_type, "event_type", InvalidResolutionError)
        object.__setattr__(
            self,
            "payload",
            freeze_mapping(self.payload, "payload", InvalidResolutionError),
        )
        _require_optional_entity(self.actor, "actor")
        _require_optional_entity(self.target, "target")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "payload": to_jsonable(self.payload),
            "actor": None if self.actor is None else self.actor.to_dict(),
            "target": None if self.target is None else self.target.to_dict(),
        }


def _require_optional_entity(value: EntityRef | None, field_name: str) -> None:
    if value is not None and not isinstance(value, EntityRef):
        raise InvalidResolutionError(
            f"{field_name} must be an EntityRef or None, got {type(value).__name__}"
        )
