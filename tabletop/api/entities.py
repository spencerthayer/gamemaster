"""Entity identity and references.

System-agnostic identity for characters, NPCs, factions, objects, and other
campaign participants. IDs are opaque strings. The generic runtime does not
treat ``entity_type`` as mechanically meaningful and stores no game
statistics here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tabletop.api._contract import optional_non_empty_str, require_non_empty_str
from tabletop.api.errors import InvalidActionError


@dataclass(frozen=True)
class EntityRef:
    """Lightweight immutable reference to a campaign entity.

    ``id`` is opaque to the generic runtime. There is no UUID requirement
    and no character/NPC distinction in the core. ``entity_type`` is an
    optional caller label; the runtime never branches on it.
    """

    id: str
    entity_type: str | None = None

    def __post_init__(self) -> None:
        require_non_empty_str(self.id, "id", InvalidActionError)
        optional_non_empty_str(self.entity_type, "entity_type", InvalidActionError)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "entity_type": self.entity_type}
