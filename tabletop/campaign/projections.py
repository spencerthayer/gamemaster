"""Pure folds from campaign events into operator-readable projections.

SQLite plus the event log remain the only authority. These projections rebuild
derived views; they never write campaign truth.
"""

from __future__ import annotations

import copy
import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, assert_never

import yaml

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
    subject_id: str | None = None
    predicate: str | None = None
    value: str | None = None
    visibility: str = "GM"


@dataclass(frozen=True)
class CampaignProjection:
    """Derived campaign snapshot folded from an event sequence."""

    campaign_id: str | None = field(default=None, compare=False)
    sequence: int | None = field(default=None, compare=False)
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
    campaign_id: str | None = None
    sequence: int | None = None

    for event in events:
        if campaign_id is not None and event.campaign_id != campaign_id:
            raise ValueError("campaign projection cannot mix campaign ids")
        campaign_id = event.campaign_id
        sequence = event.sequence
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
            case EventType.DOCUMENT_PURGED:
                pass
            case EventType.CANON_CONTRADICTION_DETECTED:
                pass
            case EventType.FACT_PROPOSED:
                raw_fact_id = event.payload.get("fact_id")
                if isinstance(raw_fact_id, str) and raw_fact_id:
                    facts[raw_fact_id] = _update_fact(
                        facts.get(raw_fact_id),
                        raw_fact_id,
                        subject_id=_optional_string(event.payload, "subject_id"),
                        predicate=_optional_string(event.payload, "predicate"),
                        value=_optional_string(event.payload, "value"),
                        visibility=_optional_string(event.payload, "visibility"),
                    )
            case (
                EventType.RULING_RECORDED
                | EventType.RULING_PROMOTED
                | EventType.SCENE_OPENED
                | EventType.SCENE_CLOSED
                | EventType.SESSION_STARTED
                | EventType.SESSION_ENDED
                | EventType.QUEST_MUTATED
            ):
                pass
            case _:
                assert_never(event_type)

    return CampaignProjection(
        campaign_id=campaign_id,
        sequence=sequence,
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
    subject_id: str | None = None,
    predicate: str | None = None,
    value: str | None = None,
    visibility: str | None = None,
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
        subject_id=base.subject_id if subject_id is None else subject_id,
        predicate=base.predicate if predicate is None else predicate,
        value=base.value if value is None else value,
        visibility=base.visibility if visibility is None else visibility,
    )


def _optional_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def write_projection(
    projection: CampaignProjection, directory: str | os.PathLike[str]
) -> None:
    """Write a deterministic, non-authoritative campaign directory view."""

    if not projection.campaign_id:
        raise ValueError("campaign projection requires a campaign id")
    if projection.sequence is None:
        raise ValueError("campaign projection requires a source sequence")

    root = Path(directory).resolve()
    files = _projection_files(projection)
    destinations = {
        relative_path: _safe_destination(root, relative_path)
        for relative_path in files
    }
    root.mkdir(parents=True, exist_ok=True)
    for relative_path, data in files.items():
        destination = destinations[relative_path]
        destination.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_yaml(
            destination,
            data,
            campaign_id=projection.campaign_id,
            sequence=projection.sequence,
        )


def _projection_files(
    projection: CampaignProjection,
) -> dict[Path, Mapping[str, Any]]:
    world_facts: list[dict[str, Any]] = []
    gm_facts: list[dict[str, Any]] = []
    for fact_id in sorted(projection.facts):
        fact = projection.facts[fact_id]
        serialized = _serialize_fact(fact)
        if (
            fact.knowledge_state is KnowledgeState.KNOWN
            and fact.visibility.upper() != "GM"
        ):
            world_facts.append(serialized)
        else:
            gm_facts.append(serialized)

    return {
        Path("campaign.yaml"): {
            "campaign_id": projection.campaign_id,
            "source_sequence": projection.sequence,
        },
        Path("state/campaign.yaml"): {
            "system": _to_yaml_data(projection.campaign_system)
        },
        Path("state/entities.yaml"): {
            "entities": _to_yaml_data(projection.entities)
        },
        Path("state/scenes.yaml"): {"scenes": _to_yaml_data(projection.scenes)},
        Path("world/facts.yaml"): {"facts": world_facts},
        Path("rulings/rulings.yaml"): {"rulings": []},
        Path("sessions/sessions.yaml"): {"sessions": []},
        Path("gm/facts.yaml"): {"facts": gm_facts},
        Path("gm/threads.yaml"): {
            "open_threads": _to_yaml_data(projection.open_threads)
        },
    }


def _serialize_fact(fact: ProjectedFact) -> dict[str, Any]:
    return {
        "canon_state": _to_yaml_data(fact.canon_state),
        "fact_id": fact.fact_id,
        "knowledge_state": _to_yaml_data(fact.knowledge_state),
        "predicate": fact.predicate,
        "source_ownership": fact.source_ownership,
        "subject_id": fact.subject_id,
        "value": fact.value,
        "visibility": fact.visibility,
    }


def _to_yaml_data(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {key: _to_yaml_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_yaml_data(item) for item in value]
    return copy.deepcopy(value)


def _safe_destination(root: Path, relative_path: Path) -> Path:
    if relative_path.is_absolute():
        raise ValueError("projection path resolves outside projection directory")
    destination = (root / relative_path).resolve(strict=False)
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "projection path resolves outside projection directory"
        ) from exc
    return destination


def _atomic_write_yaml(
    destination: Path,
    data: Mapping[str, Any],
    *,
    campaign_id: str,
    sequence: int,
) -> None:
    header = (
        f"# GENERATED ARTIFACT: campaign {campaign_id}, "
        f"source sequence {sequence}. Do not edit.\n"
    )
    body = yaml.safe_dump(data, sort_keys=True, allow_unicode=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(header)
            temporary.write(body)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


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
