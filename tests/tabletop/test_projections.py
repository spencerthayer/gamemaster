"""Tests for campaign event-log projections and atomic public mutations."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tabletop.api.actions import GameAction
from tabletop.api.entities import EntityRef
from tabletop.api.resolution import Resolution, StateChange, StateOperation
from tabletop.api.visibility import Viewpoint, parse_scope
from tabletop.campaign.event_store import (
    EventStore,
    EventType,
    PersistedEvent,
    apply_resolved_action,
    detach_fact,
    promote_fact,
    reveal_fact,
)
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState
from tabletop.campaign.projections import CampaignProjection, project_campaign
from tabletop.campaign.store import CampaignStore
from tabletop.documents.provenance import purge_facts_for_document
from tabletop.storage.sqlite import connect, migrate

GM_VIEWPOINT = Viewpoint(scope=parse_scope("GM"))


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "projections.db")
    migrate(connection)
    store = CampaignStore(connection)
    store.create_campaign("campaign-1", "First", "test")
    store.upsert_entity(
        campaign_id="campaign-1",
        entity_id="hero",
        name="Mara",
        system_state={"resources": {"hp": 12}},
    )
    connection.execute(
        "INSERT INTO scenes "
        "(scene_id, campaign_id, name, opened_at, system_state) "
        "VALUES (?, ?, ?, ?, ?)",
        ("scene-1", "campaign-1", "Crossroads", "2026-09-22T00:00:00Z", "{}"),
    )
    try:
        yield connection
    finally:
        connection.close()


def _fact(
    *,
    fact_id: str = "fact-1",
    canon_state: CanonState = CanonState.PROPOSED,
    knowledge_state: KnowledgeState = KnowledgeState.UNREVEALED,
    source_document_id: str | None = "doc-1",
    source_ownership: str = "attached",
) -> Fact:
    return Fact(
        fact_id=fact_id,
        fact_scope=FactScope.CAMPAIGN,
        setting_id=None,
        campaign_id="campaign-1",
        subject_id="hero",
        predicate="has-title",
        value="Warden",
        canon_state=canon_state,
        knowledge_state=knowledge_state,
        source_document_id=source_document_id,
        import_job_id=None if source_document_id is None else "job-1",
        source_ownership=source_ownership,
        created_at="2026-09-22T00:00:00Z",
    )


def _action() -> GameAction:
    return GameAction(
        actor=EntityRef("hero"),
        action_type="strike",
        targets=(EntityRef("goblin"),),
    )


def _resolution(*changes: StateChange) -> Resolution:
    return Resolution(
        outcome={"result": "hit"},
        state_changes=changes,
    )


def _event_rows(conn) -> list[tuple[int, str]]:
    return [
        (event.sequence, event.event_type)
        for event in EventStore(conn).read("campaign-1")
    ]


def test_replay_matches_store_entity_system_state(conn) -> None:
    store = CampaignStore(conn)
    apply_resolved_action(
        conn,
        "campaign-1",
        _action(),
        _resolution(
            StateChange(
                operation=StateOperation.SET,
                path=("entities", "hero", "system", "resources", "hp"),
                value=9,
            )
        ),
    )
    apply_resolved_action(
        conn,
        "campaign-1",
        _action(),
        _resolution(
            StateChange(
                operation=StateOperation.SET,
                path=("campaign", "system", "weather"),
                value="rain",
            ),
            StateChange(
                operation=StateOperation.SET,
                path=("scene", "system", "round"),
                value=2,
            ),
        ),
        scene_id="scene-1",
    )

    events = EventStore(conn).read("campaign-1")
    projection = project_campaign(events)

    assert projection.entities["hero"] == store.get_entity("campaign-1", "hero")[
        "system_state"
    ]
    assert projection.campaign_system == store.get_campaign("campaign-1")[
        "system_state"
    ]
    scene_row = conn.execute(
        "SELECT system_state FROM scenes WHERE scene_id = ?", ("scene-1",)
    ).fetchone()
    assert projection.scenes["scene-1"] == {"round": 2}
    assert scene_row["system_state"] == '{"round":2}'


def test_replay_is_idempotent(conn) -> None:
    apply_resolved_action(
        conn,
        "campaign-1",
        _action(),
        _resolution(
            StateChange(
                operation=StateOperation.SET,
                path=("entities", "hero", "system", "resources", "hp"),
                value=7,
            )
        ),
    )
    events = EventStore(conn).read("campaign-1")

    first = project_campaign(events)
    second = project_campaign(events)

    assert first == second
    assert first.entities["hero"] == {"resources": {"hp": 7}}


def test_offset_replay_matches_full_replay_for_tail(conn) -> None:
    apply_resolved_action(
        conn,
        "campaign-1",
        _action(),
        _resolution(
            StateChange(
                operation=StateOperation.SET,
                path=("entities", "hero", "system", "resources", "hp"),
                value=10,
            )
        ),
    )
    apply_resolved_action(
        conn,
        "campaign-1",
        _action(),
        _resolution(
            StateChange(
                operation=StateOperation.SET,
                path=("entities", "hero", "system", "resources", "hp"),
                value=4,
            )
        ),
    )
    all_events = EventStore(conn).read("campaign-1")
    offset = all_events[0].sequence
    tail = EventStore(conn).read("campaign-1", since=offset)

    full = project_campaign(all_events)
    from_offset = project_campaign(tail)

    assert from_offset.entities["hero"] == full.entities["hero"]
    assert from_offset.entities["hero"] == {"resources": {"hp": 4}}


def test_unknown_event_type_fails_loudly() -> None:
    events = (
        PersistedEvent(
            campaign_id="campaign-1",
            sequence=1,
            event_type="not.a.real.event",
            session_id=None,
            scene_id=None,
            actor_id=None,
            target_id=None,
            payload={},
            occurred_at="2026-09-22T00:00:00Z",
        ),
    )

    with pytest.raises(ValueError, match="unknown event type"):
        project_campaign(events)


def test_fact_lifecycle_and_purge_update_projection_facts(conn) -> None:
    store = CampaignStore(conn)
    fact = _fact(canon_state=CanonState.PROPOSED, source_document_id=None)
    store.add_fact(fact)
    purge_target = _fact(
        fact_id="imported-fact",
        canon_state=CanonState.PROPOSED,
        source_document_id="doc-1",
    )
    store.add_fact(purge_target)

    promote_fact(conn, fact)
    confirmed = replace(fact, canon_state=CanonState.CONFIRMED)
    reveal_fact(conn, confirmed)
    known = replace(confirmed, knowledge_state=KnowledgeState.KNOWN)
    detach_fact(conn, known)

    mid = project_campaign(EventStore(conn).read("campaign-1"))
    assert mid.facts["fact-1"].canon_state is CanonState.CONFIRMED
    assert mid.facts["fact-1"].knowledge_state is KnowledgeState.KNOWN
    assert mid.facts["fact-1"].source_ownership == "detached"

    promote_fact(conn, purge_target)
    assert "imported-fact" in project_campaign(EventStore(conn).read("campaign-1")).facts

    purge_facts_for_document(conn, "doc-1")
    after_purge = project_campaign(EventStore(conn).read("campaign-1"))
    assert "imported-fact" not in after_purge.facts
    assert "fact-1" in after_purge.facts


def test_contradiction_and_payloadless_play_events_are_recognized() -> None:
    events = (
        PersistedEvent(
            campaign_id="campaign-1",
            sequence=1,
            event_type=EventType.CANON_CONTRADICTION_DETECTED.value,
            session_id=None,
            scene_id=None,
            actor_id=None,
            target_id=None,
            payload={"reason": "conflict"},
            occurred_at="2026-09-22T00:00:00Z",
        ),
        PersistedEvent(
            campaign_id="campaign-1",
            sequence=2,
            event_type=EventType.SCENE_OPENED.value,
            session_id=None,
            scene_id="scene-1",
            actor_id=None,
            target_id=None,
            payload={},
            occurred_at="2026-09-22T00:00:01Z",
        ),
        PersistedEvent(
            campaign_id="campaign-1",
            sequence=3,
            event_type=EventType.SCENE_CLOSED.value,
            session_id=None,
            scene_id="scene-1",
            actor_id=None,
            target_id=None,
            payload={},
            occurred_at="2026-09-22T00:00:02Z",
        ),
        PersistedEvent(
            campaign_id="campaign-1",
            sequence=4,
            event_type=EventType.SESSION_STARTED.value,
            session_id="session-1",
            scene_id=None,
            actor_id=None,
            target_id=None,
            payload={},
            occurred_at="2026-09-22T00:00:03Z",
        ),
        PersistedEvent(
            campaign_id="campaign-1",
            sequence=5,
            event_type=EventType.SESSION_ENDED.value,
            session_id="session-1",
            scene_id=None,
            actor_id=None,
            target_id=None,
            payload={},
            occurred_at="2026-09-22T00:00:04Z",
        ),
        PersistedEvent(
            campaign_id="campaign-1",
            sequence=6,
            event_type=EventType.RULING_RECORDED.value,
            session_id=None,
            scene_id=None,
            actor_id=None,
            target_id=None,
            payload={},
            occurred_at="2026-09-22T00:00:05Z",
        ),
        PersistedEvent(
            campaign_id="campaign-1",
            sequence=7,
            event_type=EventType.FACT_PROPOSED.value,
            session_id=None,
            scene_id=None,
            actor_id=None,
            target_id=None,
            payload={},
            occurred_at="2026-09-22T00:00:06Z",
        ),
        PersistedEvent(
            campaign_id="campaign-1",
            sequence=8,
            event_type=EventType.DOCUMENT_PURGED.value,
            session_id=None,
            scene_id=None,
            actor_id=None,
            target_id=None,
            payload={"fact_ids": [], "document_id": "doc-1"},
            occurred_at="2026-09-22T00:00:07Z",
        ),
    )

    projection = project_campaign(events)

    assert projection == CampaignProjection(
        entities={},
        facts={},
        scenes={},
        open_threads=(),
        campaign_system={},
    )


def test_public_mutations_append_matching_events(conn) -> None:
    store = CampaignStore(conn)
    fact = _fact(fact_id="lifecycle-fact", source_document_id=None)
    store.add_fact(fact)
    purge_target = _fact(fact_id="purge-fact", source_document_id="doc-1")
    store.add_fact(purge_target)
    assert _event_rows(conn) == []

    before = _event_rows(conn)
    apply_resolved_action(
        conn,
        "campaign-1",
        _action(),
        _resolution(
            StateChange(
                operation=StateOperation.SET,
                path=("entities", "hero", "system", "resources", "hp"),
                value=8,
            )
        ),
    )
    after_action = _event_rows(conn)
    assert len(after_action) == len(before) + 1
    assert after_action[-1][1] == EventType.ACTION_RESOLVED.value

    before = after_action
    promote_fact(conn, fact)
    after_promote = _event_rows(conn)
    assert len(after_promote) == len(before) + 1
    assert after_promote[-1][1] == EventType.FACT_PROMOTED.value

    confirmed = replace(fact, canon_state=CanonState.CONFIRMED)
    before = after_promote
    reveal_fact(conn, confirmed)
    after_reveal = _event_rows(conn)
    assert len(after_reveal) == len(before) + 1
    assert after_reveal[-1][1] == EventType.FACT_REVEALED.value

    known = replace(confirmed, knowledge_state=KnowledgeState.KNOWN)
    before = after_reveal
    detach_fact(conn, known)
    after_detach = _event_rows(conn)
    assert len(after_detach) == len(before) + 1
    assert after_detach[-1][1] == EventType.FACT_DETACHED.value

    before = after_detach
    purge_facts_for_document(conn, "doc-1")
    after_purge = _event_rows(conn)
    assert len(after_purge) == len(before) + 1
    assert after_purge[-1][1] == EventType.PROVENANCE_PURGED.value

    assert store.get_entity("campaign-1", "hero")["system_state"] == {
        "resources": {"hp": 8}
    }
    assert {
        fact.fact_id
        for fact in store.get_facts("campaign-1", viewpoint=GM_VIEWPOINT)
    } == {
        "lifecycle-fact"
    }


def test_apply_resolved_action_uses_one_transaction(conn) -> None:
    statements: list[str] = []
    conn.set_trace_callback(statements.append)

    apply_resolved_action(
        conn,
        "campaign-1",
        _action(),
        _resolution(
            StateChange(
                operation=StateOperation.SET,
                path=("entities", "hero", "system", "flag"),
                value=True,
            )
        ),
    )

    conn.set_trace_callback(None)
    assert sum(statement.startswith("BEGIN") for statement in statements) == 1
    events = EventStore(conn).read("campaign-1")
    assert len(events) == 1
    assert events[0].event_type == EventType.ACTION_RESOLVED.value
    assert CampaignStore(conn).get_entity("campaign-1", "hero")["system_state"][
        "flag"
    ] is True
