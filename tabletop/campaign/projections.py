"""Pure folds from campaign events into operator-readable projections.

SQLite plus the event log remain the only authority. These projections rebuild
derived views; they never write campaign truth.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, MutableMapping, assert_never

from tabletop.api.resolution import StateChange, StateOperation
from tabletop.campaign.event_store import EventType, PersistedEvent
from tabletop.campaign.models import CanonState, KnowledgeState
from tabletop.campaign.state_paths import apply_json_change, route_state_change_path


@dataclass(frozen=True)
class ProjectedFact:
    """Fact fields reconstructible from lifecycle and purge events."""

    fact_id: str
    canon_state: CanonState | None = None
    knowledge_state: KnowledgeState | None = None
    source_ownership: str | None = None


@dataclass(frozen=True)
class CampaignProjection:
    """Derived campaign snapshot folded from an event sequence."""

    entities: Mapping[str, Any] = field(default_factory=dict)
    facts: Mapping[str, ProjectedFact] = field(default_factory=dict)
    scenes: Mapping[str, Any] = field(default_factory=dict)
    open_threads: tuple[Any, ...] = ()
    campaign_system: Mapping[str, Any] = field(default_factory=dict)


def project_campaign(events: Iterable[PersistedEvent]) -> CampaignProjection:
    """Fold events into a ``CampaignProjection`` without side effects."""

    entities: dict[str, Any] = {}
    facts: dict[str, ProjectedFact] = {}
    scenes: dict[str, Any] = {}
    open_threads: list[Any] = []
    campaign_system: Any = {}

    for event in events:
        try:
            event_type = EventType(event.event_type)
        except ValueError as exc:
            raise ValueError(f"unknown event type: {event.event_type!r}") from exc

        match event_type:
            case EventType.ACTION_RESOLVED:
                campaign_system, entities, scenes = _apply_action_resolved(
                    event,
                    campaign_system=campaign_system,
                    entities=entities,
                    scenes=scenes,
                )
            case EventType.FACT_PROMOTED:
                fact_id = _require_fact_id(event.payload)
                facts[fact_id] = _update_fact(
                    facts.get(fact_id),
                    fact_id,
                    canon_state=CanonState.CONFIRMED,
                )
            case EventType.FACT_REVEALED:
                fact_id = _require_fact_id(event.payload)
                facts[fact_id] = _update_fact(
                    facts.get(fact_id),
                    fact_id,
                    knowledge_state=KnowledgeState.KNOWN,
                )
            case EventType.FACT_DETACHED:
                fact_id = _require_fact_id(event.payload)
                facts[fact_id] = _update_fact(
                    facts.get(fact_id),
                    fact_id,
                    source_ownership="detached",
                )
            case EventType.PROVENANCE_PURGED:
                for fact_id in event.payload.get("fact_ids", ()):
                    facts.pop(str(fact_id), None)
            case EventType.CANON_CONTRADICTION_DETECTED:
                pass
            case (
                EventType.FACT_PROPOSED
                | EventType.RULING_RECORDED
                | EventType.SCENE_OPENED
                | EventType.SCENE_CLOSED
                | EventType.SESSION_STARTED
                | EventType.SESSION_ENDED
            ):
                pass
            case _:
                assert_never(event_type)

    return CampaignProjection(
        entities={key: copy.deepcopy(value) for key, value in entities.items()},
        facts=dict(facts),
        scenes={key: copy.deepcopy(value) for key, value in scenes.items()},
        open_threads=tuple(open_threads),
        campaign_system=copy.deepcopy(campaign_system),
    )


def _require_fact_id(payload: Mapping[str, Any]) -> str:
    fact_id = payload.get("fact_id")
    if not isinstance(fact_id, str) or not fact_id:
        raise ValueError("fact lifecycle event requires a non-empty fact_id")
    return fact_id


def _update_fact(
    existing: ProjectedFact | None,
    fact_id: str,
    *,
    canon_state: CanonState | None = None,
    knowledge_state: KnowledgeState | None = None,
    source_ownership: str | None = None,
) -> ProjectedFact:
    base = existing or ProjectedFact(fact_id=fact_id)
    return ProjectedFact(
        fact_id=fact_id,
        canon_state=base.canon_state if canon_state is None else canon_state,
        knowledge_state=(
            base.knowledge_state if knowledge_state is None else knowledge_state
        ),
        source_ownership=(
            base.source_ownership if source_ownership is None else source_ownership
        ),
    )


def _apply_action_resolved(
    event: PersistedEvent,
    *,
    campaign_system: Any,
    entities: MutableMapping[str, Any],
    scenes: MutableMapping[str, Any],
) -> tuple[Any, MutableMapping[str, Any], MutableMapping[str, Any]]:
    raw_changes = event.payload.get("state_changes", ())
    for raw in raw_changes:
        change = _state_change_from_dict(raw)
        campaign_system, entities, scenes = _apply_state_change(
            change,
            campaign_id=event.campaign_id,
            campaign_system=campaign_system,
            entities=entities,
            scenes=scenes,
            scene_id=event.scene_id,
        )
    return campaign_system, entities, scenes


def _state_change_from_dict(raw: Mapping[str, Any]) -> StateChange:
    operation = raw["operation"]
    path = tuple(raw["path"])
    if operation == StateOperation.DELETE.value or operation is StateOperation.DELETE:
        return StateChange(operation=StateOperation.DELETE, path=path)
    return StateChange(
        operation=operation,
        path=path,
        value=raw.get("value"),
    )


def _apply_state_change(
    change: StateChange,
    *,
    campaign_id: str,
    campaign_system: Any,
    entities: MutableMapping[str, Any],
    scenes: MutableMapping[str, Any],
    scene_id: str | None,
) -> tuple[Any, MutableMapping[str, Any], MutableMapping[str, Any]]:
    _, target, relative_path = route_state_change_path(
        campaign_id, change, scene_id
    )
    target_kind, target_id = target
    if target_kind == "campaign":
        campaign_system = apply_json_change(
            copy.deepcopy(campaign_system), relative_path, change
        )
    elif target_kind == "entity":
        current = copy.deepcopy(entities.get(target_id, {}))
        entities[target_id] = apply_json_change(current, relative_path, change)
    elif target_kind == "scene":
        current = copy.deepcopy(scenes.get(target_id, {}))
        scenes[target_id] = apply_json_change(current, relative_path, change)
    else:
        assert_never(target_kind)
    return campaign_system, entities, scenes
