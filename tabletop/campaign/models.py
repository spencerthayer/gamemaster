"""Campaign data models.

Typed models for campaigns, sessions, scenes, entities, and facts shared by
the store, event log, and orchestration layers. Phase 11 of the execution
plan fills this in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from tabletop.api.errors import StorageError


class CanonState(str, Enum):
    """Whether a fact is proposed or accepted as canon."""

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"


class KnowledgeState(str, Enum):
    """Whether a confirmed fact has been revealed."""

    UNREVEALED = "unrevealed"
    KNOWN = "known"



class SceneStatus(str, Enum):
    """Whether a scene is still running."""

    OPEN = "open"
    CLOSED = "closed"


class PresenceType(str, Enum):
    """What kind of thing is present in a scene.

    Presence says nothing about who may learn about the entity. That is
    viewpoint policy, resolved by ``visible_facts_clause`` and the scene
    snapshot, never stored here.
    """

    PC = "pc"
    NPC = "npc"
    SUMMON = "summon"
    PROP = "prop"


@dataclass(frozen=True, kw_only=True)
class SceneMember:
    """One entity's presence in one scene."""

    scene_id: str
    entity_id: str
    presence_type: PresenceType
    entered_at: str
    exited_at: str | None = None

    @property
    def is_present(self) -> bool:
        """True while the entity is still in the scene."""

        return self.exited_at is None


@dataclass(frozen=True, kw_only=True)
class Scene:
    """An authoritative scene with its lifecycle bounds."""

    scene_id: str
    campaign_id: str
    name: str
    status: SceneStatus
    started_at: str
    session_id: str | None = None
    location_entity_id: str | None = None
    in_world_started_at: str | None = None
    in_world_ended_at: str | None = None
    ended_at: str | None = None
    system_state: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "system_state", MappingProxyType(dict(self.system_state)))

    @property
    def is_open(self) -> bool:
        """True while the scene is still running."""

        return self.status is SceneStatus.OPEN


@dataclass(frozen=True, kw_only=True)
class GameTime:
    """The campaign-wide in-world clock."""

    campaign_id: str
    updated_at: str
    in_world_label: str | None = None
    in_world_minutes: int | None = None


class SceneInvariantError(StorageError):
    """A scene operation would break a scene, presence, or clock invariant."""


@dataclass(frozen=True, kw_only=True)
class NpcRecord:
    """System-agnostic NPC state, including GM-only information."""

    entity_id: str
    identity: Mapping[str, Any]
    public: Mapping[str, Any]
    private: Mapping[str, Any]
    knowledge: Mapping[str, Any]
    agenda: Mapping[str, Any]
    relationships: tuple[Any, ...]
    clocks: Mapping[str, int]
    system_state: Mapping[str, Any]

    def __post_init__(self) -> None:
        for field_name in (
            "identity",
            "public",
            "private",
            "knowledge",
            "agenda",
            "clocks",
            "system_state",
        ):
            value = getattr(self, field_name)
            object.__setattr__(
                self,
                field_name,
                MappingProxyType(dict(value)),
            )
        object.__setattr__(self, "relationships", tuple(self.relationships))


class FactScope(str, Enum):
    """The setting or campaign that owns a fact."""

    SETTING = "setting"
    CAMPAIGN = "campaign"


@dataclass(frozen=True, kw_only=True)
class Fact:
    """A setting or campaign fact with independent canon and knowledge state."""

    fact_id: str
    fact_scope: FactScope
    setting_id: str | None
    campaign_id: str | None
    subject_id: str | None
    predicate: str
    value: str
    canon_state: CanonState = CanonState.PROPOSED
    knowledge_state: KnowledgeState = KnowledgeState.UNREVEALED
    visibility: str = "GM"
    valid_from: str | None = None
    valid_until: str | None = None
    source_document_id: str | None = None
    source_chunk_id: str | None = None
    import_job_id: str | None = None
    extraction_method: str | None = None
    source_ownership: str = "attached"
    created_at: str
