"""Tests for distinct canon lifecycle event types."""

from __future__ import annotations

from pathlib import Path

import pytest

from tabletop.api.actions import GameAction
from tabletop.api.entities import EntityRef
from tabletop.api.errors import FactInvariantError
from tabletop.api.resolution import (
    ResolutionStatus,
    RollResult,
    StateChange,
    StateOperation,
)
from tabletop.api.rules import RuleReference
from tabletop.campaign.event_store import (
    EventStore,
    EventType,
    action_resolved_payload,
    detach_fact,
    event_domain,
    promote_fact,
    record_contradiction,
    reveal_fact,
)
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "lifecycle.db")
    migrate(connection)
    CampaignStore(connection).create_campaign("campaign-1", "First", "test")
    try:
        yield connection
    finally:
        connection.close()


def _fact(
    *,
    fact_id: str = "fact-1",
    canon_state: CanonState = CanonState.PROPOSED,
    knowledge_state: KnowledgeState = KnowledgeState.UNREVEALED,
    source_document_id: str | None = None,
    import_job_id: str | None = None,
    source_ownership: str = "attached",
) -> Fact:
    return Fact(
        fact_id=fact_id,
        fact_scope=FactScope.CAMPAIGN,
        setting_id=None,
        campaign_id="campaign-1",
        subject_id="entity-1",
        predicate="is_allied_with",
        value="entity-2",
        canon_state=canon_state,
        knowledge_state=knowledge_state,
        source_document_id=source_document_id,
        import_job_id=import_job_id,
        source_ownership=source_ownership,
        created_at="2026-09-22T00:00:00Z",
    )


def _event_types(conn) -> list[str]:
    return [event.event_type for event in EventStore(conn).read("campaign-1")]


def test_promote_appends_only_fact_promoted(conn) -> None:
    store = CampaignStore(conn)
    fact = _fact()
    store.add_fact(fact)

    promoted = promote_fact(conn, fact)

    assert promoted.canon_state is CanonState.CONFIRMED
    assert promoted.knowledge_state is KnowledgeState.UNREVEALED
    assert store.get_facts("campaign-1")[0].canon_state is CanonState.CONFIRMED
    assert _event_types(conn) == [EventType.FACT_PROMOTED.value]
    assert EventType.FACT_REVEALED.value not in _event_types(conn)


def test_reveal_appends_only_fact_revealed(conn) -> None:
    store = CampaignStore(conn)
    fact = _fact(canon_state=CanonState.CONFIRMED)
    store.add_fact(fact)

    revealed = reveal_fact(conn, fact)

    assert revealed.knowledge_state is KnowledgeState.KNOWN
    assert revealed.canon_state is CanonState.CONFIRMED
    assert store.get_facts("campaign-1")[0].knowledge_state is KnowledgeState.KNOWN
    assert _event_types(conn) == [EventType.FACT_REVEALED.value]
    assert EventType.FACT_PROMOTED.value not in _event_types(conn)


def test_detach_changes_only_source_ownership(conn) -> None:
    store = CampaignStore(conn)
    fact = _fact(
        canon_state=CanonState.CONFIRMED,
        source_document_id="doc-1",
        import_job_id="job-1",
        source_ownership="attached",
    )
    store.add_fact(fact)

    detached = detach_fact(conn, fact)

    stored = store.get_facts("campaign-1")[0]
    assert detached.source_ownership == "detached"
    assert stored.source_ownership == "detached"
    assert stored.canon_state is CanonState.CONFIRMED
    assert stored.knowledge_state is KnowledgeState.UNREVEALED
    assert stored.source_document_id == "doc-1"
    assert stored.import_job_id == "job-1"
    assert _event_types(conn) == [EventType.FACT_DETACHED.value]


def test_promote_imported_fact_preserves_provenance(conn) -> None:
    store = CampaignStore(conn)
    fact = _fact(
        source_document_id="doc-1",
        import_job_id="job-1",
        source_ownership="attached",
    )
    store.add_fact(fact)

    promoted = promote_fact(conn, fact)

    stored = store.get_facts("campaign-1")[0]
    assert promoted.source_document_id == "doc-1"
    assert promoted.import_job_id == "job-1"
    assert stored.source_document_id == "doc-1"
    assert stored.import_job_id == "job-1"
    assert stored.source_ownership == "attached"
    assert _event_types(conn) == [EventType.FACT_PROMOTED.value]


def test_reveal_proposed_fact_raises_and_appends_no_event(conn) -> None:
    store = CampaignStore(conn)
    fact = _fact(canon_state=CanonState.PROPOSED)
    store.add_fact(fact)

    with pytest.raises(FactInvariantError):
        reveal_fact(conn, fact)

    assert store.get_facts("campaign-1") == [fact]
    assert EventStore(conn).read("campaign-1") == []


def test_contradiction_detected_never_changes_facts(conn) -> None:
    store = CampaignStore(conn)
    fact = _fact(canon_state=CanonState.CONFIRMED)
    store.add_fact(fact)
    before = store.get_facts("campaign-1")

    record_contradiction(
        conn,
        "campaign-1",
        {"subject_id": "entity-1", "reason": "conflicting claim"},
    )

    assert store.get_facts("campaign-1") == before
    events = EventStore(conn).read("campaign-1")
    assert len(events) == 1
    assert events[0].event_type == EventType.CANON_CONTRADICTION_DETECTED.value
    assert events[0].payload == {
        "subject_id": "entity-1",
        "reason": "conflicting claim",
    }


def test_action_resolved_payload_shape() -> None:
    action = GameAction(
        actor=EntityRef("hero"),
        action_type="attack",
        targets=(EntityRef("goblin"),),
        parameters={"weapon": "sword"},
    )
    rolls = (RollResult(expression="1d20", total=15, details={"sides": 20}),)
    state_changes = (
        StateChange(
            operation=StateOperation.SET,
            path=("entity", "goblin", "hp"),
            value=3,
        ),
    )
    rule_references = (RuleReference(source_id="core", title="Combat"),)

    payload = action_resolved_payload(
        action=action,
        status=ResolutionStatus.RESOLVED,
        outcome={"hit": True},
        rolls=rolls,
        state_changes=state_changes,
        rule_references=rule_references,
    )

    assert payload == {
        "action": action.to_dict(),
        "status": ResolutionStatus.RESOLVED.value,
        "outcome": {"hit": True},
        "rolls": [rolls[0].to_dict()],
        "state_changes": [state_changes[0].to_dict()],
        "rule_references": [rule_references[0].to_dict()],
    }
    assert "state.changed" not in payload


def test_event_type_members_and_exhaustive_domain() -> None:
    expected = {
        "fact.proposed",
        "fact.promoted",
        "fact.revealed",
        "fact.detached",
        "provenance.purged",
        "canon.contradiction_detected",
        "action.resolved",
        "ruling.recorded",
        "scene.opened",
        "scene.closed",
        "session.started",
        "session.ended",
    }
    assert {member.value for member in EventType} >= expected
    domains = {member: event_domain(member) for member in EventType}
    assert domains[EventType.FACT_PROMOTED] == "canon"
    assert domains[EventType.PROVENANCE_PURGED] == "provenance"
    assert domains[EventType.ACTION_RESOLVED] == "play"
