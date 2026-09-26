"""Authoritative mutation, event, and replay contract.

A new ``EventType`` must be classified in exactly one set. A model-facing
SQLite write must name its event. Relationship library writes and
document-import entity inserts are intentionally absent: no registered skill
writes them. A new skill that does must add a row to ``WRITERS``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tabletop.api.resolution import StateOperation
from tabletop.api.workspace import Workspace
from tabletop.campaign.models import CanonState, KnowledgeState
from tabletop.campaign.projections import project_campaign
from tabletop.campaign.rulings import Ruling, RulingStore
from tabletop.documents.extraction import ProposedFact
from tabletop.documents.importer import _insert_fact
from tabletop.campaign.event_store import (
    EventStore,
    EventType,
    PersistedEvent,
    detach_fact,
    promote_fact,
    reveal_fact,
)
from tabletop.campaign.setting_events import SettingEventType
from tabletop.campaign.store import CampaignStore
from tabletop.api.rules import RuleReference
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate, transaction

REPLAY_REQUIRED = frozenset(
    {
        EventType.FACT_PROPOSED,
        EventType.FACT_PROMOTED,
        EventType.FACT_REVEALED,
        EventType.FACT_DETACHED,
        EventType.PROVENANCE_PURGED,
        EventType.ACTION_RESOLVED,
        EventType.RULING_RECORDED,
        EventType.RULING_PROMOTED,
        EventType.SESSION_STARTED,
        EventType.SESSION_ENDED,
        EventType.QUEST_MUTATED,
        EventType.CAMPAIGN_ARCHIVED,
        EventType.CAMPAIGN_RESTORED,
        EventType.SCENE_OPENED,
        EventType.SCENE_CLOSED,
        EventType.SCENE_ENTITY_ENTERED,
        EventType.SCENE_ENTITY_EXITED,
        EventType.SCENE_LOCATION_CHANGED,
        EventType.SCENE_TIME_CHANGED,
        EventType.PARTICIPANT_ADDED,
        EventType.PARTICIPANT_REMOVED,
        EventType.CHARACTER_CONTROL_GRANTED,
        EventType.CHARACTER_CONTROL_ENDED,
    }
)
AUDIT_ONLY = frozenset(
    {
        EventType.DOCUMENT_PURGED,
        EventType.CANON_CONTRADICTION_DETECTED,
        EventType.CAMPAIGN_FORKED,
    }
)
DECLARED_BUT_UNEMITTED = frozenset()

# Dependent events without their parent record raise. Fact axis events keep
# the historical synthesize-a-partial-record behavior so a promote that
# follows a purged propose still names the fact id. Ruling promotion and
# session end do not invent a row that was never recorded.
ORPHAN_RAISES = frozenset(
    {
        EventType.RULING_PROMOTED,
        EventType.SESSION_ENDED,
        EventType.SCENE_CLOSED,
        EventType.SCENE_ENTITY_ENTERED,
        EventType.SCENE_ENTITY_EXITED,
        EventType.SCENE_LOCATION_CHANGED,
    }
)

DEFERRED_WRITERS: frozenset[str] = frozenset()

WRITERS = (
    ("mutate_quest", True, "quest.mutated", "campaign_system"),
    ("record_ruling", True, "ruling.recorded", "rulings"),
    ("RulingStore.promote", True, "ruling.promoted", "rulings"),
    ("end_session", True, "session.ended", "sessions"),
    ("edit_setting", True, "setting.edited", "setting"),
    ("upsert_world_entity", True, "world_entity.upserted", "setting_entity"),
    ("record_world_history", True, "world_fact.recorded", "setting_fact"),
    ("promote_fact", True, "fact.promoted", "facts"),
    ("reveal_fact", True, "fact.revealed", "facts"),
    ("detach_fact", True, "fact.detached", "facts"),
    ("apply_resolved_action", True, "action.resolved", "campaign_system"),
)


SETTING_REPLAY_REQUIRED = frozenset(SettingEventType)
SETTING_AUDIT_ONLY: frozenset[SettingEventType] = frozenset()


def test_setting_event_types_are_classified_exactly_once() -> None:
    assert SETTING_REPLAY_REQUIRED | SETTING_AUDIT_ONLY == set(SettingEventType)
    assert not (SETTING_REPLAY_REQUIRED & SETTING_AUDIT_ONLY)


def test_event_types_are_classified_exactly_once() -> None:
    classified = REPLAY_REQUIRED | AUDIT_ONLY | DECLARED_BUT_UNEMITTED
    assert classified == set(EventType)
    assert not (REPLAY_REQUIRED & AUDIT_ONLY)
    assert not (REPLAY_REQUIRED & DECLARED_BUT_UNEMITTED)
    assert not (AUDIT_ONLY & DECLARED_BUT_UNEMITTED)
    assert AUDIT_ONLY == {
        EventType.DOCUMENT_PURGED,
        EventType.CANON_CONTRADICTION_DETECTED,
        EventType.CAMPAIGN_FORKED,
    }


def test_audit_events_do_not_invent_campaign_system_keys() -> None:
    events = (
        event(EventType.CANON_CONTRADICTION_DETECTED, {"reason": "conflict"}),
        event(EventType.DOCUMENT_PURGED, {"document_id": "doc-1"}),
        event(EventType.CAMPAIGN_FORKED, {"source_campaign_id": "campaign-0"}),
    )
    projection = project_campaign(events)
    assert projection.campaign_system == {}
    assert projection.facts == {}
    assert projection.entities == {}
    assert projection.scenes == {}


def event(
    event_type: EventType,
    payload: dict[str, object],
    *,
    sequence: int = 1,
    scene_id: str | None = None,
) -> PersistedEvent:
    return PersistedEvent(
        campaign_id="campaign-1",
        sequence=sequence,
        event_type=event_type.value,
        session_id=None,
        scene_id=scene_id,
        actor_id=None,
        target_id=None,
        payload=payload,
        occurred_at="2026-09-22T00:00:00Z",
    )


def _fact_proposed(sequence: int = 1) -> PersistedEvent:
    return event(
        EventType.FACT_PROPOSED,
        {
            "fact_id": "fact-1",
            "subject_id": "hero",
            "predicate": "has-title",
            "value": "Warden",
            "visibility": "GM",
        },
        sequence=sequence,
    )



def _scene_opened(sequence: int = 1) -> PersistedEvent:
    return event(
        EventType.SCENE_OPENED,
        {
            "scene_id": "scene-1",
            "name": "Crossroads",
            "session_id": None,
            "location_entity_id": None,
            "in_world_started_at": None,
            "started_at": "2026-09-22T00:00:00Z",
        },
        sequence=sequence,
        scene_id="scene-1",
    )


def _scene_entity_entered(sequence: int = 2) -> PersistedEvent:
    return event(
        EventType.SCENE_ENTITY_ENTERED,
        {
            "scene_id": "scene-1",
            "entity_id": "hero",
            "presence_type": "pc",
            "entered_at": "2026-09-22T00:10:00Z",
        },
        sequence=sequence,
        scene_id="scene-1",
    )

def _sequence(event_type: EventType) -> tuple[tuple[PersistedEvent, ...], tuple[PersistedEvent, ...]]:
    if event_type is EventType.FACT_PROPOSED:
        return (), (_fact_proposed(),)
    if event_type is EventType.FACT_PROMOTED:
        proposed = _fact_proposed()
        return (proposed,), (
            proposed,
            event(EventType.FACT_PROMOTED, {"fact_id": "fact-1"}, sequence=2),
        )
    if event_type is EventType.FACT_REVEALED:
        proposed = _fact_proposed()
        return (proposed,), (
            proposed,
            event(EventType.FACT_REVEALED, {"fact_id": "fact-1"}, sequence=2),
        )
    if event_type is EventType.FACT_DETACHED:
        proposed = _fact_proposed()
        return (proposed,), (
            proposed,
            event(EventType.FACT_DETACHED, {"fact_id": "fact-1"}, sequence=2),
        )
    if event_type is EventType.PROVENANCE_PURGED:
        proposed = _fact_proposed()
        return (proposed,), (
            proposed,
            event(
                EventType.PROVENANCE_PURGED,
                {"fact_ids": ["fact-1"]},
                sequence=2,
            ),
        )
    if event_type is EventType.ACTION_RESOLVED:
        resolved = event(
            EventType.ACTION_RESOLVED,
            {
                "state_changes": [
                    {
                        "operation": StateOperation.SET.value,
                        "path": ["campaign", "system", "weather"],
                        "value": "rain",
                    }
                ]
            },
        )
        return (), (resolved,)
    if event_type is EventType.QUEST_MUTATED:
        return (), (
            event(
                EventType.QUEST_MUTATED,
                {"quest_id": "lantern", "quest": {"quest_id": "lantern", "title": "Find"}},
            ),
        )
    if event_type is EventType.RULING_RECORDED:
        return (), (
            event(
                EventType.RULING_RECORDED,
                {
                    "ruling_id": "ruling-1",
                    "campaign_id": "campaign-1",
                    "canon_state": "proposed",
                    "knowledge_state": "unrevealed",
                    "question": "q",
                    "decision": "d",
                    "scope": "s",
                    "source_references": [],
                    "supersedes": None,
                },
            ),
        )
    if event_type is EventType.RULING_PROMOTED:
        recorded = _sequence(EventType.RULING_RECORDED)[1][0]
        return (recorded,), (
            recorded,
            event(EventType.RULING_PROMOTED, {"ruling_id": "ruling-1"}, sequence=2),
        )
    if event_type is EventType.SESSION_STARTED:
        return (), (
            event(
                EventType.SESSION_STARTED,
                {"session_id": "s1", "started_at": "2026-09-22T00:00:00Z"},
            ),
        )
    if event_type is EventType.SESSION_ENDED:
        started = _sequence(EventType.SESSION_STARTED)[1][0]
        return (started,), (
            started,
            event(
                EventType.SESSION_ENDED,
                {"session_id": "s1", "ended_at": "2026-09-22T01:00:00Z"},
                sequence=2,
            ),
        )
    if event_type is EventType.CAMPAIGN_ARCHIVED:
        return (), (
            event(
                EventType.CAMPAIGN_ARCHIVED,
                {"archived_at": "2026-09-22T02:00:00Z"},
            ),
        )
    if event_type is EventType.CAMPAIGN_RESTORED:
        archived = _sequence(EventType.CAMPAIGN_ARCHIVED)[1][0]
        return (archived,), (
            archived,
            event(
                EventType.CAMPAIGN_RESTORED,
                {"restored_at": "2026-09-22T03:00:00Z"},
                sequence=2,
            ),
        )
    if event_type is EventType.PARTICIPANT_ADDED:
        return (), (
            event(
                EventType.PARTICIPANT_ADDED,
                {
                    "participant_id": "p1",
                    "display_name": "Ada",
                    "role": "player",
                    "created_at": "2026-09-22T00:00:00Z",
                },
            ),
        )
    if event_type is EventType.PARTICIPANT_REMOVED:
        added = _sequence(EventType.PARTICIPANT_ADDED)[1][0]
        return (added,), (
            added,
            event(
                EventType.PARTICIPANT_REMOVED,
                {"participant_id": "p1", "removed_at": "2026-09-22T01:00:00Z"},
                sequence=2,
            ),
        )
    if event_type is EventType.CHARACTER_CONTROL_GRANTED:
        return (), (
            event(
                EventType.CHARACTER_CONTROL_GRANTED,
                {
                    "control_id": "c1",
                    "participant_id": "p1",
                    "entity_id": "ada",
                    "control": "owner",
                    "created_at": "2026-09-22T00:00:00Z",
                },
            ),
        )
    if event_type is EventType.CHARACTER_CONTROL_ENDED:
        granted = _sequence(EventType.CHARACTER_CONTROL_GRANTED)[1][0]
        return (granted,), (
            granted,
            event(
                EventType.CHARACTER_CONTROL_ENDED,
                {
                    "control_id": "c1",
                    "participant_id": "p1",
                    "entity_id": "ada",
                    "control": "owner",
                    "ended_at": "2026-09-22T01:00:00Z",
                },
                sequence=2,
            ),
        )
    if event_type is EventType.SCENE_OPENED:
        return (), (_scene_opened(),)
    if event_type is EventType.SCENE_CLOSED:
        opened = _scene_opened()
        return (opened,), (
            opened,
            event(
                EventType.SCENE_CLOSED,
                {
                    "scene_id": "scene-1",
                    "ended_at": "2026-09-22T01:00:00Z",
                    "exited_entity_ids": [],
                },
                sequence=2,
                scene_id="scene-1",
            ),
        )
    if event_type is EventType.SCENE_ENTITY_ENTERED:
        opened = _scene_opened()
        return (opened,), (opened, _scene_entity_entered())
    if event_type is EventType.SCENE_ENTITY_EXITED:
        opened = _scene_opened()
        entered = _scene_entity_entered()
        return (opened, entered), (
            opened,
            entered,
            event(
                EventType.SCENE_ENTITY_EXITED,
                {
                    "scene_id": "scene-1",
                    "entity_id": "hero",
                    "exited_at": "2026-09-22T00:30:00Z",
                },
                sequence=3,
                scene_id="scene-1",
            ),
        )
    if event_type is EventType.SCENE_LOCATION_CHANGED:
        opened = _scene_opened()
        return (opened,), (
            opened,
            event(
                EventType.SCENE_LOCATION_CHANGED,
                {
                    "scene_id": "scene-1",
                    "location_entity_id": "hero",
                    "changed_at": "2026-09-22T00:05:00Z",
                },
                sequence=2,
                scene_id="scene-1",
            ),
        )
    if event_type is EventType.SCENE_TIME_CHANGED:
        return (), (
            event(
                EventType.SCENE_TIME_CHANGED,
                {
                    "in_world_label": "Day 1",
                    "in_world_minutes": 0,
                    "changed_at": "2026-09-22T00:00:00Z",
                },
            ),
        )
    raise AssertionError(f"no sequence for {event_type}")

def test_replay_required_events_change_projection() -> None:
    """Each replay-required event changes the projection versus its prefix."""

    failures: list[str] = []
    for event_type in sorted(REPLAY_REQUIRED, key=lambda item: item.value):
        prefix, full = _sequence(event_type)
        before = project_campaign(prefix)
        after = project_campaign(full)
        if before == after:
            failures.append(event_type.value)
    assert failures == [], f"replay did not change projection for {failures}"


def test_orphan_dependent_events_follow_the_declared_policy() -> None:
    for event_type in ORPHAN_RAISES:
        orphan = event(event_type, {"ruling_id": "missing", "session_id": "missing"})
        with pytest.raises(ValueError):
            project_campaign((orphan,))


def test_quest_replay_matches_sqlite(tmp_path: Path) -> None:
    connection = connect(tmp_path / "quests.db")
    migrate(connection)
    CampaignStore(connection).create_campaign("campaign-1", "Owned", "freeform")
    runtime = TabletopRuntime(
        tmp_path,
        campaign_roots=[],
        plugin_roots=[],
        connection=connection,
        workspace=Workspace.CAMPAIGN,
        active_campaign="campaign-1",
    )
    assert runtime.mutate_quest(
        json.dumps({"quest_id": "lantern", "title": "Find the lantern"})
    )["ok"]
    assert runtime.mutate_quest(
        json.dumps({"quest_id": "lantern", "title": "Find the brass lantern"})
    )["ok"]
    assert runtime.mutate_quest(
        json.dumps({"quest_id": "gate", "title": "Open the gate"})
    )["ok"]
    row = connection.execute(
        "SELECT system_state FROM campaigns WHERE campaign_id = ?",
        ("campaign-1",),
    ).fetchone()
    stored = json.loads(row["system_state"])["quests"]
    projection = project_campaign(EventStore(connection).read("campaign-1"))
    assert projection.campaign_system["quests"] == stored
    assert projection.open_threads == tuple(
        projection.campaign_system.get("open_threads", ())
    )
    connection.close()


def test_ruling_replay_matches_sqlite(tmp_path: Path) -> None:
    connection = connect(tmp_path / "rulings.db")
    migrate(connection)
    CampaignStore(connection).create_campaign("campaign-1", "Owned", "freeform")
    store = RulingStore(connection)
    first = Ruling(
        ruling_id="ruling-1",
        campaign_id="campaign-1",
        system_id="freeform",
        question="May the lantern be carried?",
        decision="Yes.",
        scope="lantern",
        source_references=(RuleReference(source_id="core"),),
        session_id=None,
        created_at="2026-09-22T00:00:00Z",
        canon_state=CanonState.PROPOSED,
        knowledge_state=KnowledgeState.UNREVEALED,
    )
    store.record(first)
    store.promote("ruling-1")
    second = Ruling(
        ruling_id="ruling-2",
        campaign_id="campaign-1",
        system_id="freeform",
        question="May the lantern be carried?",
        decision="Only at night.",
        scope="lantern",
        source_references=(RuleReference(source_id="core"),),
        session_id=None,
        created_at="2026-09-22T01:00:00Z",
        supersedes="ruling-1",
        canon_state=CanonState.PROPOSED,
        knowledge_state=KnowledgeState.UNREVEALED,
    )
    store.record(second)
    projection = project_campaign(EventStore(connection).read("campaign-1"))
    promoted = projection.rulings["ruling-1"]
    assert promoted.canon_state == "confirmed"
    assert promoted.knowledge_state == "unrevealed"
    assert promoted.question == first.question
    assert promoted.decision == first.decision
    assert promoted.scope == first.scope
    assert promoted.supersedes is None
    assert promoted.source_references == tuple(
        reference.to_dict() for reference in first.source_references
    )
    assert "ruling-1" in projection.rulings
    assert projection.rulings["ruling-2"].supersedes == "ruling-1"
    assert projection.rulings["ruling-2"].decision == "Only at night."
    connection.close()


def test_new_campaign_fact_replay_matches_sqlite(tmp_path: Path) -> None:
    connection = connect(tmp_path / "facts.db")
    migrate(connection)
    CampaignStore(connection).create_campaign("campaign-1", "Owned", "freeform")
    with transaction(connection):
        stored = _insert_fact(
            connection,
            proposed=ProposedFact(
                fact_id="fact-1",
                fact_scope="campaign",
                setting_id=None,
                campaign_id="campaign-1",
                subject_id="hero",
                predicate="has-title",
                value="Warden",
                visibility="GM",
                valid_from="2026-09-22T00:00:00Z",
                valid_until=None,
                source_document_id="doc-1",
                source_chunk_id="chunk-1",
            ),
            import_job_id="job-1",
            extraction_method="test",
            created_at="2026-09-22T00:00:00Z",
        )
    promoted = promote_fact(connection, stored)
    revealed = reveal_fact(connection, promoted)
    detach_fact(connection, revealed)
    projected = project_campaign(EventStore(connection).read("campaign-1")).facts["fact-1"]
    row = connection.execute(
        "SELECT fact_scope, campaign_id, subject_id, predicate, value, canon_state, "
        "knowledge_state, visibility, valid_from, valid_until, source_document_id, "
        "source_chunk_id, source_ownership FROM facts WHERE fact_id = ?",
        ("fact-1",),
    ).fetchone()
    assert projected.fact_scope == row["fact_scope"]
    assert projected.campaign_id == row["campaign_id"]
    assert projected.subject_id == row["subject_id"]
    assert projected.predicate == row["predicate"]
    assert projected.value == row["value"]
    assert projected.canon_state is CanonState.CONFIRMED
    assert projected.canon_state.value == row["canon_state"]
    assert projected.knowledge_state is KnowledgeState.KNOWN
    assert projected.knowledge_state.value == row["knowledge_state"]
    assert projected.visibility == row["visibility"]
    assert projected.valid_from == row["valid_from"]
    assert projected.valid_until == row["valid_until"]
    assert projected.source_document_id == row["source_document_id"]
    assert projected.source_chunk_id == row["source_chunk_id"]
    assert projected.source_ownership == row["source_ownership"]
    connection.close()


def test_start_session_replays_and_rejects_a_second_open_session(tmp_path: Path) -> None:
    connection = connect(tmp_path / "sessions.db")
    migrate(connection)
    CampaignStore(connection).create_campaign("campaign-1", "Owned", "freeform")
    runtime = TabletopRuntime(
        tmp_path,
        campaign_roots=[tmp_path / "campaigns"],
        plugin_roots=[],
        connection=connection,
        workspace=Workspace.CAMPAIGN,
        active_campaign="campaign-1",
    )
    opened = runtime.start_session(
        json.dumps({"session_id": "s1", "started_at": "2026-09-22T00:00:00Z"})
    )
    assert opened["ok"] is True
    events = EventStore(connection).read("campaign-1")
    assert [event.event_type for event in events] == [EventType.SESSION_STARTED.value]
    assert events[0].payload["session_id"] == "s1"
    assert events[0].payload["started_at"] == "2026-09-22T00:00:00Z"
    duplicate = runtime.start_session(json.dumps({"session_id": "s2"}))
    assert duplicate["ok"] is False
    assert len(EventStore(connection).read("campaign-1")) == 1
    missing = TabletopRuntime(
        tmp_path,
        campaign_roots=[tmp_path / "campaigns"],
        plugin_roots=[],
        connection=connection,
        workspace=Workspace.CAMPAIGN,
        active_campaign=None,
    ).start_session(json.dumps({"session_id": "s3"}))
    assert missing["ok"] is False
    ended = runtime.end_session()
    assert ended["ok"] is True
    projection = project_campaign(EventStore(connection).read("campaign-1"))
    assert projection.sessions["s1"].started_at == "2026-09-22T00:00:00Z"
    assert projection.sessions["s1"].ended_at is not None
    with pytest.raises(Exception):
        connection.execute(
            "INSERT INTO sessions (session_id, campaign_id, started_at) "
            "VALUES (?, ?, ?)",
            ("open-a", "campaign-1", "2026-09-22T02:00:00Z"),
        )
        connection.execute(
            "INSERT INTO sessions (session_id, campaign_id, started_at) "
            "VALUES (?, ?, ?)",
            ("open-b", "campaign-1", "2026-09-22T02:00:01Z"),
        )
    connection.close()


def test_model_facing_writers_are_in_the_matrix() -> None:
    names = [row[0] for row in WRITERS]
    assert names == [
        "mutate_quest",
        "record_ruling",
        "RulingStore.promote",
        "end_session",
        "edit_setting",
        "upsert_world_entity",
        "record_world_history",
        "promote_fact",
        "reveal_fact",
        "detach_fact",
        "apply_resolved_action",
    ]
    missing_events = [
        name
        for name, writes, event_name, _field in WRITERS
        if writes and event_name is None and name not in DEFERRED_WRITERS
    ]
    assert missing_events == []
    assert DEFERRED_WRITERS <= {name for name, *_rest in WRITERS}
