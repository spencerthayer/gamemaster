"""First-class campaign ruling lifecycle tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.api.actions import GameAction
from tabletop.api.entities import EntityRef
from tabletop.api.errors import FactInvariantError
from tabletop.api.resolution import Resolution, ResolutionContext, ResolutionStatus
from tabletop.api.rules import RuleReference
from tabletop.campaign.event_store import EventStore, EventType
from tabletop.campaign.models import CanonState, KnowledgeState
from tabletop.campaign.rulings import (
    Ruling,
    RulingAwareTierSearch,
    RulingStore,
    ruling_from_adjudication,
)
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.adjudication import (
    AdjudicationResult,
    adjudication_request,
)
from tabletop.retrieval.models import RetrievalNamespace, RetrievedChunk, SourceReference
from tabletop.retrieval.precedence import PrecedenceResolver
from tabletop.api.workspace import Workspace
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "rulings.db")
    migrate(connection)
    CampaignStore(connection).create_campaign("campaign-1", "First", "test-system")
    connection.execute(
        "INSERT INTO sessions (session_id, campaign_id, started_at) VALUES (?, ?, ?)",
        ("session-1", "campaign-1", "2026-09-22T01:00:00+00:00"),
    )
    try:
        yield connection
    finally:
        connection.close()


def _ruling(
    *,
    ruling_id: str = "ruling-1",
    canon_state: CanonState = CanonState.CONFIRMED,
    knowledge_state: KnowledgeState = KnowledgeState.UNREVEALED,
    supersedes: str | None = None,
) -> Ruling:
    return Ruling(
        ruling_id=ruling_id,
        campaign_id="campaign-1",
        system_id="test-system",
        question="Does grappling consume the full action?",
        decision="Grappling consumes the full action.",
        scope="grappling",
        source_references=(
            RuleReference(
                source_id="core-rules",
                title="Combat",
                section="Grappling",
                page=42,
                document_path="/rules/core.pdf",
                chunk_id="chunk-42",
            ),
        ),
        session_id="session-1",
        created_at="2026-09-22T01:02:03+00:00",
        supersedes=supersedes,
        canon_state=canon_state,
        knowledge_state=knowledge_state,
    )


def test_record_stores_complete_ruling_and_appends_event(conn: sqlite3.Connection) -> None:
    stored = RulingStore(conn).record(_ruling())

    assert stored == _ruling()
    assert RulingStore(conn).get("ruling-1") == _ruling()
    events = EventStore(conn).read("campaign-1")
    assert [(event.event_type, event.payload) for event in events] == [
        (
            EventType.RULING_RECORDED.value,
            {"ruling_id": "ruling-1", "canon_state": "confirmed"},
        )
    ]


def test_recording_ruling_never_modifies_documents_or_chunks(
    conn: sqlite3.Connection,
) -> None:
    conn.execute(
        "INSERT INTO documents "
        "(document_id, content_hash, source_path, title, document_shape, visibility, "
        "ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "doc-1",
            "hash-doc",
            "/rules.md",
            "Rules",
            "structured_rules",
            "GM",
            "2026-09-22T00:00:00+00:00",
        ),
    )
    conn.execute(
        "INSERT INTO document_chunks "
        "(chunk_id, document_id, ordinal, heading_path, text, content_hash, visibility) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("chunk-1", "doc-1", 0, '["Rule"]', "Original text", "hash-chunk", "GM"),
    )
    before_document = dict(conn.execute("SELECT * FROM documents").fetchone())
    before_chunk = dict(conn.execute("SELECT * FROM document_chunks").fetchone())

    RulingStore(conn).record(_ruling())

    assert dict(conn.execute("SELECT * FROM documents").fetchone()) == before_document
    assert dict(conn.execute("SELECT * FROM document_chunks").fetchone()) == before_chunk


def test_proposed_ruling_promotes_without_revealing(conn: sqlite3.Connection) -> None:
    store = RulingStore(conn)
    proposed = store.record(_ruling(canon_state=CanonState.PROPOSED))

    promoted = store.promote(proposed.ruling_id)

    assert promoted.canon_state is CanonState.CONFIRMED
    assert promoted.knowledge_state is KnowledgeState.UNREVEALED
    assert store.get(proposed.ruling_id) == promoted
    assert [event.event_type for event in EventStore(conn).read("campaign-1")] == [
        EventType.RULING_RECORDED.value,
        EventType.FACT_PROMOTED.value,
    ]


def test_proposed_ruling_cannot_already_be_known(conn: sqlite3.Connection) -> None:
    with pytest.raises(FactInvariantError):
        RulingStore(conn).record(
            _ruling(
                canon_state=CanonState.PROPOSED,
                knowledge_state=KnowledgeState.KNOWN,
            )
        )


def test_superseding_ruling_links_without_deleting_old_record(
    conn: sqlite3.Connection,
) -> None:
    store = RulingStore(conn)
    old = store.record(_ruling())
    replacement = store.record(
        _ruling(ruling_id="ruling-2", supersedes=old.ruling_id)
    )

    assert replacement.supersedes == old.ruling_id
    assert store.get(old.ruling_id) == old
    assert store.get(replacement.ruling_id) == replacement


def test_search_hides_ruling_with_confirmed_successor(
    conn: sqlite3.Connection,
) -> None:
    store = RulingStore(conn)
    old = store.record(_ruling())
    replacement = store.record(
        _ruling(ruling_id="ruling-2", supersedes=old.ruling_id)
    )

    results = store.search("grappling", "campaign-1")

    assert results == (replacement,)
    assert store.get(old.ruling_id) == old


def test_adjudication_result_preserves_originating_action_and_context(
    conn: sqlite3.Connection,
) -> None:
    action = GameAction(
        actor=EntityRef("hero"),
        action_type="grapple",
        targets=(EntityRef("ogre"),),
        parameters={"approach": "wrestle"},
    )
    context = ResolutionContext(
        campaign_id="campaign-1",
        system_id="test-system",
        scene_id="scene-1",
        state={"weather": "rain"},
    )
    unresolved = Resolution(
        outcome={},
        status=ResolutionStatus.RULING_REQUIRED,
        ruling_question="Does grappling consume the full action?",
        rule_references=(RuleReference(source_id="core-rules", title="Combat"),),
    )
    result = AdjudicationResult(
        request=adjudication_request(unresolved, action, context),
        decision="Grappling consumes the full action.",
    )

    ruling = ruling_from_adjudication(
        result,
        ruling_id="ruling-adjudicated",
        scope="grappling",
        session_id="session-1",
        created_at="2026-09-22T01:02:03+00:00",
    )
    stored = RulingStore(conn).record(ruling)

    assert stored.originating_action == action.to_dict()
    assert stored.originating_context == context.to_dict()
    assert stored.question == result.request.detail
    assert stored.source_references == result.request.rule_references


class _SourcebookSearch:
    def search_tier(
        self, query: str, campaign_id: str, tier: str
    ) -> tuple[RetrievedChunk, ...]:
        if tier != "active system rules":
            return ()
        return (
            RetrievedChunk(
                text="Grappling costs one attack.",
                score=1.0,
                namespace=RetrievalNamespace.SYSTEM,
                source=SourceReference(
                    chunk_id="sourcebook-chunk",
                    document_id="sourcebook",
                    document_title="Core Rules",
                    section="Grappling",
                    page=42,
                    source_path="/rules/core.pdf",
                ),
            ),
        )


def test_rulings_search_before_generic_sourcebooks(conn: sqlite3.Connection) -> None:
    RulingStore(conn).record(_ruling())
    search = RulingAwareTierSearch(RulingStore(conn), _SourcebookSearch())

    result = PrecedenceResolver(search).resolve(
        "grappling", "campaign-1", "mechanics"
    )

    assert result.tier == "campaign rulings"
    assert result.answer is not None
    assert result.answer.text == "Grappling consumes the full action."
    assert result.answer.namespace is RetrievalNamespace.RULINGS
    assert [conflict.conflicting.text for conflict in result.conflicts] == [
        "Grappling costs one attack."
    ]


def test_runtime_record_ruling_persists_instead_of_reporting_unavailable(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    runtime = TabletopRuntime(
        tmp_path,
        campaign_roots=[],
        plugin_roots=[],
        connection=conn,
        workspace=Workspace.CAMPAIGN,
    )

    payload = runtime.record_ruling(_ruling())

    assert payload["ok"] is True
    assert payload["operation"] == "record-ruling"
    assert payload["data"]["ruling_id"] == "ruling-1"
    assert RulingStore(conn).get("ruling-1") == _ruling()
