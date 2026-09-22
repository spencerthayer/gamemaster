"""Regression coverage for the platform's named architectural invariants."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Mapping

import pytest

from tabletop.api.actions import GameAction
from tabletop.api.capabilities import Capability
from tabletop.api.entities import EntityRef
from tabletop.api.errors import FactInvariantError, InvalidResolutionError
from tabletop.api.plugin import (
    TABLETOP_PLUGIN_API_VERSION,
    GameSystemInfo,
    GameSystemPlugin,
)
from tabletop.api.resolution import Resolution, ResolutionContext, ResolutionStatus
from tabletop.api.visibility import Viewpoint, parse_scope
from tabletop.api.workspace import Workspace
from tabletop.campaign.event_store import promote_fact
from tabletop.campaign.invariants import promote, reveal
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState
from tabletop.campaign.store import CampaignStore
from tabletop.documents.extraction import KNOWN_EXTRACTOR_VERSIONS
from tabletop.documents.importer import ImportEnvelopeError, import_extraction
from tabletop.documents.jobs import start_or_resume_job
from tabletop.documents.provenance import purge_document, purge_facts_for_document
from tabletop.documents.shape import DocumentShape, detect_shape
from tabletop.orchestration.adjudication import adjudication_request
from tabletop.orchestration.context import ContextEntry, compact
from tabletop.orchestration.turn import resolve_action
from tabletop.plugins.registry import PluginRegistry
from tabletop.retrieval.models import (
    RetrievalNamespace,
    RetrievedChunk,
    SourceReference,
)
from tabletop.retrieval.precedence import PrecedenceResolver
from tabletop.storage.sqlite import connect, migrate

GM_VIEWPOINT = Viewpoint(scope=parse_scope("GM"))


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "architecture-invariants.db")
    migrate(connection)
    connection.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("setting-1", "Test Setting", "2026-09-22T00:00:00Z"),
    )
    CampaignStore(connection).create_campaign(
        "campaign-1",
        "Test Campaign",
        "test",
        setting_id="setting-1",
    )
    try:
        yield connection
    finally:
        connection.close()


def _fact(
    fact_id: str,
    *,
    canon_state: CanonState = CanonState.PROPOSED,
    knowledge_state: KnowledgeState = KnowledgeState.UNREVEALED,
    source_document_id: str | None = None,
    source_chunk_id: str | None = None,
    import_job_id: str | None = None,
    extraction_method: str | None = None,
    source_ownership: str = "attached",
) -> Fact:
    return Fact(
        fact_id=fact_id,
        fact_scope=FactScope.CAMPAIGN,
        setting_id=None,
        campaign_id="campaign-1",
        subject_id="mara",
        predicate="lives_in",
        value="Greyhaven",
        canon_state=canon_state,
        knowledge_state=knowledge_state,
        source_document_id=source_document_id,
        source_chunk_id=source_chunk_id,
        import_job_id=import_job_id,
        extraction_method=extraction_method,
        source_ownership=source_ownership,
        created_at="2026-09-22T00:00:00Z",
    )


def _seed_document(
    conn: sqlite3.Connection,
    document_id: str,
    *,
    content_hash: str,
) -> None:
    conn.execute(
        "INSERT INTO documents "
        "(document_id, content_hash, source_path, title, document_shape, "
        "content_pack_id, system_id, visibility, ingested_at) "
        "VALUES (?, ?, ?, ?, 'prose', NULL, NULL, 'GM', ?)",
        (
            document_id,
            content_hash,
            f"/tmp/{document_id}.md",
            document_id,
            "2026-09-22T00:00:00Z",
        ),
    )
    conn.execute(
        "INSERT INTO document_chunks "
        "(chunk_id, document_id, ordinal, heading_path, page, text, "
        "content_hash, content_pack_id, system_id, visibility) "
        "VALUES (?, ?, 0, '[]', NULL, ?, ?, NULL, NULL, 'GM')",
        (
            f"{document_id}-chunk-0",
            document_id,
            f"Text from {document_id}",
            f"{content_hash}-chunk",
        ),
    )


def _fact_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT fact_id FROM facts ORDER BY fact_id").fetchall()
    return [str(row["fact_id"]) for row in rows]


class _LoreSearch:
    def __init__(self) -> None:
        self.setting_text = "The city is ruled by Queen Mara."
        self.campaign_text = "The city is ruled by Regent Sol."

    def search_tier(
        self,
        query: str,
        campaign_id: str,
        tier: str,
    ) -> tuple[RetrievedChunk, ...]:
        if tier == "campaign-specific canon" and campaign_id == "campaign-1":
            return (self._chunk(self.campaign_text, "campaign"),)
        if tier == "shared setting canon":
            return (self._chunk(self.setting_text, "setting"),)
        return ()

    @staticmethod
    def _chunk(text: str, namespace: str) -> RetrievedChunk:
        retrieval_namespace = (
            RetrievalNamespace.CAMPAIGN
            if namespace == "campaign"
            else RetrievalNamespace.SETTING
        )
        return RetrievedChunk(
            text=text,
            score=1.0,
            namespace=retrieval_namespace,
            source=SourceReference(
                chunk_id=f"{namespace}-chunk",
                document_id=f"{namespace}-document",
                document_title=namespace,
                section="Canon",
                page=None,
                source_path=f"/{namespace}.md",
            ),
        )


class _RefetchRuntime:
    def __init__(self, entities: Mapping[str, object]) -> None:
        self.entities = entities

    def get_entity(self, entity_id: str) -> object:
        return self.entities[entity_id]


class _ResolvablePlugin(GameSystemPlugin):
    def __init__(self) -> None:
        self.calls: list[GameAction] = []

    @property
    def info(self) -> GameSystemInfo:
        return GameSystemInfo(
            id="resolvable",
            name="Resolvable",
            api_version=TABLETOP_PLUGIN_API_VERSION,
        )

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.ACTION_RESOLUTION})

    def resolve(self, action: GameAction, context: ResolutionContext) -> Resolution:
        self.calls.append(action)
        return Resolution(outcome={"total": 17}, explanation="The plugin resolved it.")


def test_01_confirming_a_fact_does_not_reveal_it() -> None:
    proposed = _fact("fact-1")

    confirmed = promote(proposed)

    assert confirmed.canon_state is CanonState.CONFIRMED
    assert confirmed.knowledge_state is KnowledgeState.UNREVEALED


def test_02_revealing_an_unconfirmed_proposed_fact_is_rejected() -> None:
    with pytest.raises(FactInvariantError):
        reveal(_fact("fact-2"))


def test_03_proposed_fact_cannot_be_stored_as_known(
    conn: sqlite3.Connection,
) -> None:
    invalid = _fact(
        "fact-3",
        canon_state=CanonState.PROPOSED,
        knowledge_state=KnowledgeState.KNOWN,
    )

    with pytest.raises(FactInvariantError):
        CampaignStore(conn).add_fact(invalid)

    assert _fact_ids(conn) == []


def test_04_promoting_an_imported_fact_preserves_provenance(
    conn: sqlite3.Connection,
) -> None:
    imported = _fact(
        "fact-4",
        source_document_id="document-1",
        source_chunk_id="chunk-1",
        import_job_id="job-1",
        extraction_method="extractor-1",
    )
    store = CampaignStore(conn)
    store.add_fact(imported)

    promoted = promote_fact(conn, imported)
    stored = store.get_facts("campaign-1", viewpoint=GM_VIEWPOINT)[0]

    assert promoted.canon_state is CanonState.CONFIRMED
    assert stored.source_document_id == "document-1"
    assert stored.source_chunk_id == "chunk-1"
    assert stored.import_job_id == "job-1"
    assert stored.extraction_method == "extractor-1"
    assert stored.source_ownership == "attached"


def test_05_source_purge_removes_attached_imports_but_preserves_owned_facts(
    conn: sqlite3.Connection,
) -> None:
    store = CampaignStore(conn)
    for fact in (
        _fact("attached-proposed", source_document_id="document-1"),
        _fact(
            "attached-promoted",
            canon_state=CanonState.CONFIRMED,
            source_document_id="document-1",
        ),
        _fact(
            "detached",
            canon_state=CanonState.CONFIRMED,
            source_document_id="document-1",
            source_ownership="detached",
        ),
        _fact("hand-authored"),
    ):
        store.add_fact(fact)

    removed = purge_facts_for_document(conn, "document-1")

    assert removed == ["attached-promoted", "attached-proposed"]
    assert _fact_ids(conn) == ["detached", "hand-authored"]


def test_06_deleting_document_removes_only_records_derived_from_it(
    conn: sqlite3.Connection,
) -> None:
    _seed_document(conn, "document-1", content_hash="hash-1")
    _seed_document(conn, "document-2", content_hash="hash-2")
    store = CampaignStore(conn)
    store.add_fact(_fact("from-document-1", source_document_id="document-1"))
    store.add_fact(_fact("from-document-2", source_document_id="document-2"))
    store.add_fact(_fact("hand-authored"))

    removed = purge_document(conn, "document-1")

    assert removed == ["from-document-1"]
    assert _fact_ids(conn) == ["from-document-2", "hand-authored"]
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM documents WHERE document_id = 'document-1'"
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM documents WHERE document_id = 'document-2'"
        ).fetchone()[0]
        == 1
    )


def test_07_campaign_canon_overrides_setting_without_mutating_setting() -> None:
    search = _LoreSearch()
    resolver = PrecedenceResolver(search)

    campaign_result = resolver.resolve("city is ruled", "campaign-1", "lore")
    other_result = resolver.resolve("city is ruled", "campaign-2", "lore")

    assert campaign_result.answer is not None
    assert campaign_result.answer.text == search.campaign_text
    assert other_result.answer is not None
    assert other_result.answer.text == search.setting_text
    assert search.setting_text == "The city is ruled by Queen Mara."


def test_08_deterministic_shape_detection_routes_prose_away_from_structure() -> None:
    prose = (
        "Mara crossed the rain-soaked courtyard and watched the dark windows. "
        "The letter in her pocket named this place but never explained the open gate.\n\n"
        '"Wait here," Iven said. "Someone is inside." Mara replied that they had '
        "come too far to turn back, and together they entered the silent hall."
    )

    first = detect_shape(prose)
    second = detect_shape(prose)

    assert first == second
    assert first.shape is DocumentShape.PROSE
    assert first.parser == DocumentShape.PROSE.value
    assert first.parser != DocumentShape.STRUCTURED_RULES.value


def test_09_malformed_extraction_cannot_reach_authoritative_state(
    conn: sqlite3.Connection,
) -> None:
    conn.execute(
        "INSERT INTO ingest_jobs "
        "(job_id, document_hash, parser_version, slice_strategy_version, "
        "status, total_slices, completed_slices, failed_slices, "
        "started_at, updated_at) "
        "VALUES ('job-9', 'hash-9', '1', '1', 'completed', 1, 1, 0, ?, ?)",
        ("2026-09-22T00:00:00Z", "2026-09-22T00:00:00Z"),
    )
    malformed = {
        "job_id": "job-9",
        "slice_index": 0,
        "extractor_version": next(iter(KNOWN_EXTRACTOR_VERSIONS)),
        "entities": [
            {
                "entity_id": "mara",
                "owner_scope": "campaign",
                "setting_id": None,
                "campaign_id": "campaign-1",
                "overrides_id": None,
                "entity_type": "npc",
                "name": "Mara",
                "system_state": {},
                "metadata": {},
            }
        ],
        "facts": [],
        "unexpected": "must reject the whole envelope",
    }

    with pytest.raises(ImportEnvelopeError):
        import_extraction(conn, malformed)

    assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0


def test_10_interrupted_ingest_resumes_only_for_matching_hash_and_slicing(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_bytes(b"abcdefghijkl")

    def interrupt(index: int, _content: bytes) -> None:
        if index == 1:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        start_or_resume_job(
            conn,
            source,
            interrupt,
            slice_size=4,
            slice_strategy_version="slicing-1",
        )

    resumed_calls: list[int] = []
    resumed = start_or_resume_job(
        conn,
        source,
        lambda index, _content: resumed_calls.append(index),
        slice_size=4,
        slice_strategy_version="slicing-1",
    )
    source.write_bytes(b"mnopqrstuvwx")
    changed_hash_calls: list[int] = []
    changed_hash = start_or_resume_job(
        conn,
        source,
        lambda index, _content: changed_hash_calls.append(index),
        slice_size=4,
        slice_strategy_version="slicing-1",
    )
    changed_slicing_calls: list[int] = []
    changed_slicing = start_or_resume_job(
        conn,
        source,
        lambda index, _content: changed_slicing_calls.append(index),
        slice_size=4,
        slice_strategy_version="slicing-2",
    )

    assert resumed.skipped_slices == (0,)
    assert resumed_calls == [1, 2]
    assert changed_hash.job_id != resumed.job_id
    assert changed_hash.skipped_slices == ()
    assert changed_hash_calls == [0, 1, 2]
    assert changed_slicing.job_id != changed_hash.job_id
    assert changed_slicing.skipped_slices == ()
    assert changed_slicing_calls == [0, 1, 2]


def test_11_compaction_stub_refetches_its_original_data() -> None:
    original = {"entity_id": "ithrazel", "name": "Ithrazel", "kind": "dragon"}
    entry = ContextEntry(
        content="entity record: Ithrazel",
        token_cost=100,
        refetch_tool="get-entity",
        refetch_args={"entity_id": "ithrazel"},
        priority=10,
        compacted=False,
        recency=1,
    )

    (stub,) = compact((entry,), budget=30)
    skill = next(
        skill for skill in Workspace.CAMPAIGN.skills if skill.name == stub.refetch_tool
    )
    runtime = _RefetchRuntime({"ithrazel": original})
    refetched = getattr(runtime, skill.runtime_method)(**stub.refetch_args)

    assert stub.compacted is True
    assert refetched == original


def test_12_setting_workspace_exposes_no_campaign_operations() -> None:
    setting_tools = {skill.name for skill in Workspace.SETTING.skills}
    campaign_only = {
        "read-session",
        "end-session",
        "current-scene",
        "get-party-state",
        "get-open-threads",
        "mutate-quest",
        "read-campaign-secret",
        "current-campaign",
        "query-campaign",
        "resolve-action",
        "record-ruling",
    }

    assert setting_tools.isdisjoint(campaign_only)


def test_13_resolvable_action_cannot_bypass_the_active_plugin() -> None:
    plugin = _ResolvablePlugin()
    registry = PluginRegistry()
    registry.register(plugin)
    action = GameAction(actor=EntityRef(id="mara"), action_type="check")
    context = ResolutionContext(
        campaign_id="campaign-1",
        system_id=plugin.info.id,
    )

    result = resolve_action(registry, action, context)

    assert plugin.calls == [action]
    assert result.outcome == {"total": 17}


def test_14_unsupported_and_unresolved_adjudicate_without_mechanical_numbers() -> None:
    action = GameAction(actor=EntityRef(id="mara"), action_type="check")
    context = ResolutionContext(campaign_id="campaign-1", system_id="test")

    for status in (ResolutionStatus.UNSUPPORTED, ResolutionStatus.UNRESOLVED):
        explanation = f"{status.value} has no mechanical result."
        resolution = Resolution(
            outcome={},
            status=status,
            explanation=explanation,
        )
        request = adjudication_request(resolution, action, context)

        assert request.status is status
        assert request.detail == explanation
        assert resolution.outcome == {}
        assert resolution.rolls == ()
        assert resolution.state_changes == ()
        assert resolution.events == ()
        with pytest.raises(InvalidResolutionError):
            Resolution(
                outcome={"llm_authored_total": 17},
                status=status,
                explanation=explanation,
            )
