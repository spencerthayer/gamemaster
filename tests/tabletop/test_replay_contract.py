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
from tabletop.campaign.event_store import EventStore, EventType, PersistedEvent
from tabletop.campaign.projections import project_campaign
from tabletop.campaign.store import CampaignStore
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate

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
    }
)
AUDIT_ONLY = frozenset(
    {
        EventType.DOCUMENT_PURGED,
        EventType.CANON_CONTRADICTION_DETECTED,
    }
)
DECLARED_BUT_UNEMITTED = frozenset(
    {
        EventType.SCENE_OPENED,
        EventType.SCENE_CLOSED,
    }
)

# Dependent events without their parent record raise. Fact axis events keep
# the historical synthesize-a-partial-record behavior so a promote that
# follows a purged propose still names the fact id. Ruling promotion and
# session end do not invent a row that was never recorded.
ORPHAN_RAISES = frozenset(
    {
        EventType.RULING_PROMOTED,
        EventType.SESSION_ENDED,
    }
)

# Setting writers gain events in task 06. This set must become empty then.
DEFERRED_WRITERS = frozenset(
    {
        "edit_setting",
        "upsert_world_entity",
        "record_world_history",
    }
)

WRITERS = (
    ("mutate_quest", True, "quest.mutated", "campaign_system"),
    ("record_ruling", True, "ruling.recorded", "rulings"),
    ("RulingStore.promote", True, "ruling.promoted", "rulings"),
    ("end_session", True, "session.ended", "sessions"),
    ("edit_setting", True, None, "setting"),
    ("upsert_world_entity", True, None, "setting_entity"),
    ("record_world_history", True, None, "setting_fact"),
    ("promote_fact", True, "fact.promoted", "facts"),
    ("reveal_fact", True, "fact.revealed", "facts"),
    ("detach_fact", True, "fact.detached", "facts"),
    ("apply_resolved_action", True, "action.resolved", "campaign_system"),
)


def test_event_types_are_classified_exactly_once() -> None:
    classified = REPLAY_REQUIRED | AUDIT_ONLY | DECLARED_BUT_UNEMITTED
    assert classified == set(EventType)
    assert not (REPLAY_REQUIRED & AUDIT_ONLY)
    assert not (REPLAY_REQUIRED & DECLARED_BUT_UNEMITTED)
    assert not (AUDIT_ONLY & DECLARED_BUT_UNEMITTED)
    assert AUDIT_ONLY == {
        EventType.DOCUMENT_PURGED,
        EventType.CANON_CONTRADICTION_DETECTED,
    }


def test_audit_events_do_not_invent_campaign_system_keys() -> None:
    events = (
        event(EventType.CANON_CONTRADICTION_DETECTED, {"reason": "conflict"}),
        event(EventType.DOCUMENT_PURGED, {"document_id": "doc-1"}),
        event(EventType.SCENE_OPENED, {}, scene_id="scene-1"),
        event(EventType.SCENE_CLOSED, {}, scene_id="scene-1"),
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
    raise AssertionError(f"no sequence for {event_type}")


@pytest.mark.xfail(
    reason="quest, ruling, and session replay land in tasks 03-05",
    strict=True,
)
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


@pytest.mark.xfail(
    reason="orphan ruling and session events raise once those arms replay",
    strict=True,
)
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
