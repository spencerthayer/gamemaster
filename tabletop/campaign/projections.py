"""Pure folds from campaign events into operator-readable projections.

SQLite plus the event log remain the only authority. These projections rebuild
derived views; they never write campaign truth.
"""

from __future__ import annotations

import copy
import os
import tempfile
from dataclasses import asdict, dataclass, field, is_dataclass
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
    fact_scope: str | None = None
    campaign_id: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    source_document_id: str | None = None
    source_chunk_id: str | None = None


@dataclass(frozen=True)
class ProjectedRuling:
    """Ruling fields reconstructible from ruling lifecycle events."""

    ruling_id: str
    canon_state: str | None = None
    knowledge_state: str | None = None
    question: str | None = None
    decision: str | None = None
    scope: str | None = None
    supersedes: str | None = None
    source_references: tuple[Any, ...] = ()
    campaign_id: str | None = None
    system_id: str | None = None


@dataclass(frozen=True)
class ProjectedSession:
    """Session identity and bounds reconstructible from session events."""

    session_id: str
    started_at: str | None = None
    ended_at: str | None = None


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
    rulings: Mapping[str, ProjectedRuling] = field(default_factory=dict)
    sessions: Mapping[str, ProjectedSession] = field(default_factory=dict)
    archived_at: str | None = None
    participants: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    character_controls: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


def project_campaign(events: Iterable[PersistedEvent]) -> CampaignProjection:
    """Fold events into a ``CampaignProjection`` without side effects."""

    entities: dict[str, Any] = {}
    facts: dict[str, ProjectedFact] = {}
    scenes: dict[str, Any] = {}
    open_threads: list[Any] = []
    campaign_system: Any = {}
    rulings: dict[str, ProjectedRuling] = {}
    sessions: dict[str, ProjectedSession] = {}
    campaign_id: str | None = None
    sequence: int | None = None
    archived_at: str | None = None
    participants: dict[str, dict[str, Any]] = {}
    character_controls: dict[str, dict[str, Any]] = {}

    for event in events:
        if campaign_id is not None and event.campaign_id != campaign_id:
            raise ValueError("campaign projection cannot mix campaign ids")
        campaign_id = event.campaign_id
        sequence = event.sequence
        if event.event_schema_version not in (0, 1):
            raise ValueError(
                "unsupported event schema generation: "
                f"{event.event_schema_version}"
            )
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
                    if event.event_schema_version >= 1:
                        facts[raw_fact_id] = _fact_from_generation_one(event, raw_fact_id)
                    else:
                        facts[raw_fact_id] = _update_fact(
                            facts.get(raw_fact_id),
                            raw_fact_id,
                            subject_id=_optional_string(event.payload, "subject_id"),
                            predicate=_optional_string(event.payload, "predicate"),
                            value=_optional_string(event.payload, "value"),
                            visibility=_optional_string(event.payload, "visibility"),
                        )
            case EventType.QUEST_MUTATED:
                campaign_system = _apply_quest_mutated(event, campaign_system)
            case EventType.RULING_RECORDED:
                projected = _ruling_from_event(event)
                if projected is not None:
                    rulings[projected.ruling_id] = projected
            case EventType.RULING_PROMOTED:
                rulings = _promote_projected_ruling(event, rulings)
            case EventType.SESSION_STARTED:
                started = _session_from_started(event)
                if started is not None:
                    sessions[started.session_id] = started
            case EventType.SESSION_ENDED:
                sessions = _end_projected_session(event, sessions)
            case EventType.CAMPAIGN_ARCHIVED:
                raw = event.payload.get("archived_at")
                if isinstance(raw, str) and raw:
                    archived_at = raw
                elif event.event_schema_version >= 1:
                    raise ValueError("campaign.archived requires archived_at")
            case EventType.CAMPAIGN_RESTORED:
                archived_at = None
            case EventType.PARTICIPANT_ADDED:
                pid = event.payload.get("participant_id")
                if isinstance(pid, str) and pid:
                    participants[pid] = {
                        "participant_id": pid,
                        "display_name": event.payload.get("display_name"),
                        "role": event.payload.get("role"),
                        "created_at": event.payload.get("created_at"),
                    }
                elif event.event_schema_version >= 1:
                    raise ValueError("participant.added requires participant_id")
            case EventType.PARTICIPANT_REMOVED:
                pid = event.payload.get("participant_id")
                if isinstance(pid, str):
                    participants.pop(pid, None)
                elif event.event_schema_version >= 1:
                    raise ValueError("participant.removed requires participant_id")
            case EventType.CHARACTER_CONTROL_GRANTED:
                cid = event.payload.get("control_id")
                if isinstance(cid, str) and cid:
                    character_controls[cid] = {
                        "control_id": cid,
                        "participant_id": event.payload.get("participant_id"),
                        "entity_id": event.payload.get("entity_id"),
                        "control": event.payload.get("control"),
                        "created_at": event.payload.get("created_at"),
                        "ended_at": None,
                    }
                elif event.event_schema_version >= 1:
                    raise ValueError("character_control.granted requires control_id")
            case EventType.CHARACTER_CONTROL_ENDED:
                cid = event.payload.get("control_id")
                if isinstance(cid, str) and cid in character_controls:
                    existing = dict(character_controls[cid])
                    existing["ended_at"] = event.payload.get("ended_at")
                    character_controls[cid] = existing
                elif isinstance(cid, str):
                    character_controls[cid] = {
                        "control_id": cid,
                        "participant_id": event.payload.get("participant_id"),
                        "entity_id": event.payload.get("entity_id"),
                        "control": event.payload.get("control"),
                        "ended_at": event.payload.get("ended_at"),
                    }
                elif event.event_schema_version >= 1:
                    raise ValueError("character_control.ended requires control_id")
            case EventType.SCENE_OPENED | EventType.SCENE_CLOSED:
                pass
            case _:
                assert_never(event_type)

    if not isinstance(campaign_system, dict):
        campaign_system = {}
    raw_threads = campaign_system.get("open_threads", ())
    open_threads = tuple(raw_threads) if isinstance(raw_threads, list) else ()

    return CampaignProjection(
        campaign_id=campaign_id,
        sequence=sequence,
        entities={key: copy.deepcopy(value) for key, value in entities.items()},
        facts=dict(facts),
        scenes={key: copy.deepcopy(value) for key, value in scenes.items()},
        open_threads=tuple(open_threads),
        campaign_system=copy.deepcopy(campaign_system),
        rulings=dict(rulings),
        sessions=dict(sessions),
        archived_at=archived_at,
        participants=copy.deepcopy(participants),
        character_controls=copy.deepcopy(character_controls),
    )


def _session_from_started(event: PersistedEvent) -> ProjectedSession | None:
    session_id = event.payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        if event.event_schema_version == 0:
            return None
        raise ValueError("session.started requires a non-empty session_id")
    started_at = event.payload.get("started_at")
    if event.event_schema_version >= 1 and not isinstance(started_at, str):
        raise ValueError("session.started requires started_at")
    return ProjectedSession(
        session_id=session_id,
        started_at=started_at if isinstance(started_at, str) else None,
    )


def _end_projected_session(
    event: PersistedEvent,
    sessions: dict[str, ProjectedSession],
) -> dict[str, ProjectedSession]:
    session_id = event.payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        if event.event_schema_version == 0:
            return sessions
        raise ValueError("session.ended requires a non-empty session_id")
    existing = sessions.get(session_id)
    if existing is None:
        raise ValueError(f"session.ended has no started session: {session_id}")
    ended_at = event.payload.get("ended_at")
    sessions[session_id] = ProjectedSession(
        session_id=existing.session_id,
        started_at=existing.started_at,
        ended_at=ended_at if isinstance(ended_at, str) else event.occurred_at,
    )
    return sessions


def _ruling_from_event(event: PersistedEvent) -> ProjectedRuling | None:
    payload = event.payload
    ruling_id = payload.get("ruling_id")
    if event.event_schema_version == 0:
        if not isinstance(ruling_id, str) or not ruling_id:
            return None
        return ProjectedRuling(
            ruling_id=ruling_id,
            canon_state=_optional_string(payload, "canon_state"),
        )
    if not isinstance(ruling_id, str) or not ruling_id:
        raise ValueError("ruling.recorded requires a non-empty ruling_id")
    if payload.get("campaign_id") != event.campaign_id:
        raise ValueError("ruling payload campaign_id does not match the event")
    references = payload.get("source_references", ())
    if not isinstance(references, list):
        raise ValueError("ruling source_references must be a list")
    return ProjectedRuling(
        ruling_id=ruling_id,
        canon_state=_optional_string(payload, "canon_state"),
        knowledge_state=_optional_string(payload, "knowledge_state"),
        question=_optional_string(payload, "question"),
        decision=_optional_string(payload, "decision"),
        scope=_optional_string(payload, "scope"),
        supersedes=payload.get("supersedes") if payload.get("supersedes") is None or isinstance(payload.get("supersedes"), str) else None,
        source_references=tuple(references),
        campaign_id=_optional_string(payload, "campaign_id"),
        system_id=_optional_string(payload, "system_id"),
    )


def _promote_projected_ruling(
    event: PersistedEvent,
    rulings: dict[str, ProjectedRuling],
) -> dict[str, ProjectedRuling]:
    ruling_id = event.payload.get("ruling_id")
    if not isinstance(ruling_id, str) or not ruling_id:
        raise ValueError("ruling.promoted requires a non-empty ruling_id")
    existing = rulings.get(ruling_id)
    if existing is None:
        raise ValueError(f"ruling.promoted has no recorded ruling: {ruling_id}")
    rulings[ruling_id] = ProjectedRuling(
        ruling_id=existing.ruling_id,
        canon_state=CanonState.CONFIRMED.value,
        knowledge_state=existing.knowledge_state,
        question=existing.question,
        decision=existing.decision,
        scope=existing.scope,
        supersedes=existing.supersedes,
        source_references=existing.source_references,
        campaign_id=existing.campaign_id,
        system_id=existing.system_id,
    )
    return rulings


def _apply_quest_mutated(event: PersistedEvent, campaign_system: Any) -> Any:
    quest_id = event.payload.get("quest_id")
    quest = event.payload.get("quest")
    if not isinstance(quest_id, str) or not quest_id:
        raise ValueError("quest.mutated requires a non-empty quest_id")
    if not isinstance(quest, dict):
        raise ValueError("quest.mutated requires a quest object")
    change = StateChange(
        operation=StateOperation.SET,
        path=("campaign", "system", "quests", quest_id),
        value=dict(quest),
    )
    campaign_system, _entities, _scenes = _apply_state_change(
        change,
        campaign_id=event.campaign_id,
        campaign_system=campaign_system,
        entities={},
        scenes={},
        scene_id=None,
    )
    return campaign_system


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
        fact_scope=base.fact_scope,
        campaign_id=base.campaign_id,
        valid_from=base.valid_from,
        valid_until=base.valid_until,
        source_document_id=base.source_document_id,
        source_chunk_id=base.source_chunk_id,
    )


def _fact_from_generation_one(event: PersistedEvent, fact_id: str) -> ProjectedFact:
    payload = event.payload
    if payload.get("campaign_id") != event.campaign_id:
        raise ValueError("fact payload campaign_id does not match the event")
    return ProjectedFact(
        fact_id=fact_id,
        subject_id=_optional_string(payload, "subject_id"),
        predicate=_optional_string(payload, "predicate"),
        value=_optional_string(payload, "value"),
        visibility=_optional_string(payload, "visibility") or "GM",
        fact_scope=_optional_string(payload, "fact_scope"),
        campaign_id=_optional_string(payload, "campaign_id"),
        valid_from=_optional_string(payload, "valid_from"),
        valid_until=_optional_string(payload, "valid_until"),
        source_document_id=_optional_string(payload, "source_document_id"),
        source_chunk_id=_optional_string(payload, "source_chunk_id"),
        source_ownership=_optional_string(payload, "source_ownership"),
        canon_state=CanonState.PROPOSED,
        knowledge_state=KnowledgeState.UNREVEALED,
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
        Path("rulings/rulings.yaml"): {
            "rulings": [
                _to_yaml_data(ruling)
                for _ruling_id, ruling in sorted(projection.rulings.items())
            ]
        },
        Path("sessions/sessions.yaml"): {
            "sessions": [
                _to_yaml_data(session)
                for _session_id, session in sorted(projection.sessions.items())
            ]
        },
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
    if is_dataclass(value) and not isinstance(value, type):
        return _to_yaml_data(asdict(value))
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
