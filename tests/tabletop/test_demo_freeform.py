"""End-to-end demonstration of a model-free freeform campaign."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from tabletop.api.events import GameEvent
from tabletop.api.resolution import ResolutionContext, ResolutionStatus
from tabletop.api.visibility import Viewpoint, parse_scope
from tabletop.api.workspace import Workspace
from tabletop.campaign.event_store import (
    EventStore,
    EventType,
    promote_fact,
    reveal_fact,
)
from tabletop.campaign.models import Fact, FactScope
from tabletop.campaign.relationships import (
    RelationshipEdge,
    query_edges,
    store_edge,
)
from tabletop.campaign.rulings import (
    RulingStore,
    ruling_from_adjudication,
)
from tabletop.campaign.store import CampaignStore
from tabletop.documents.content_pack import load_content_pack
from tabletop.documents.ingest import IngestContext
from tabletop.documents.markdown import MarkdownIngestor
from tabletop.orchestration.adjudication import (
    AdjudicationRequest,
    AdjudicationResult,
)
from tabletop.orchestration.turn import parse_game_action
from tabletop.retrieval.lexical import LexicalRetriever
from tabletop.retrieval.models import RetrievalFilters, RetrievalNamespace
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate


_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE = _REPO_ROOT / "examples" / "campaigns" / "freeform-demo"
_CAMPAIGN_ID = "freeform-demo"
_SESSION_ID = "session-1"
_SCENE_ID = "flooded-archive"
_STARTED_AT = "2026-09-22T12:00:00+00:00"
_PLAYER = Viewpoint(scope=parse_scope("CHARACTER:mara"), party_member=True)


def _action(
    action_type: str,
    *,
    targets: list[dict[str, str]] | None = None,
    parameters: dict[str, object] | None = None,
    scene_id: str = _SCENE_ID,
) -> str:
    return json.dumps(
        {
            "actor": {"id": "mara"},
            "action_type": action_type,
            "targets": targets or [],
            "parameters": parameters or {},
            "scene_id": scene_id,
        }
    )


def _index_document(
    connection,
    document_id: str,
    namespace: RetrievalNamespace,
) -> None:
    document = connection.execute(
        "SELECT title, source_path, content_pack_id, system_id, visibility "
        "FROM documents WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    chunks = connection.execute(
        "SELECT chunk_id, heading_path, text FROM document_chunks "
        "WHERE document_id = ? ORDER BY ordinal",
        (document_id,),
    ).fetchall()
    retriever = LexicalRetriever(connection)
    for chunk in chunks:
        retriever.index(
            namespace,
            chunk_id=chunk["chunk_id"],
            document_id=document_id,
            document_title=document["title"],
            section=tuple(json.loads(chunk["heading_path"])),
            text=chunk["text"],
            source_path=document["source_path"],
            content_pack_id=document["content_pack_id"],
            system_id=document["system_id"],
            visibility=document["visibility"],
        )
    connection.commit()


def _record_fact(connection, fact: Fact) -> None:
    CampaignStore(connection).add_fact(fact)
    EventStore(connection).append(
        _CAMPAIGN_ID,
        GameEvent(
            event_type=EventType.FACT_PROPOSED.value,
            payload={
                "fact_id": fact.fact_id,
                "subject_id": fact.subject_id,
                "predicate": fact.predicate,
                "value": fact.value,
                "visibility": fact.visibility,
            },
        ),
        session_id=_SESSION_ID,
    )


def test_freeform_example_campaign_runs_end_to_end(tmp_path: Path) -> None:
    definition = yaml.safe_load((_EXAMPLE / "campaign.yaml").read_text())
    pack_directory = _EXAMPLE / definition["content_pack"]
    pack = load_content_pack(pack_directory)
    assert pack.system_id == "freeform"
    assert pack.gm_only == ("gm-notes.md",)

    database_path = tmp_path / "freeform-demo.sqlite3"
    campaign_root = tmp_path / "campaigns"
    projection_directory = campaign_root / _CAMPAIGN_ID
    projection_directory.mkdir(parents=True)
    connection = connect(database_path)
    migrate(connection)
    store = CampaignStore(connection)
    store.create_campaign(
        _CAMPAIGN_ID,
        definition["name"],
        definition["system_id"],
        created_at=_STARTED_AT,
        system_state=definition["initial_state"],
    )
    connection.execute(
        "INSERT INTO sessions "
        "(session_id, campaign_id, started_at, participants, important_facts, "
        "open_threads) VALUES (?, ?, ?, ?, ?, ?)",
        (
            _SESSION_ID,
            _CAMPAIGN_ID,
            _STARTED_AT,
            '["mara"]',
            '["The brass lantern is in the archive"]',
            '["Who opened the floodgate?"]',
        ),
    )
    connection.execute(
        "INSERT INTO scenes "
        "(scene_id, campaign_id, session_id, name, opened_at, system_state) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            _SCENE_ID,
            _CAMPAIGN_ID,
            _SESSION_ID,
            "Flooded Archive",
            _STARTED_AT,
            "{}",
        ),
    )
    connection.commit()
    events = EventStore(connection)
    events.append(
        _CAMPAIGN_ID,
        GameEvent(
            event_type=EventType.SESSION_STARTED.value,
            payload={"session_id": _SESSION_ID, "started_at": _STARTED_AT},
        ),
        session_id=_SESSION_ID,
        occurred_at=_STARTED_AT,
    )
    events.append(
        _CAMPAIGN_ID,
        GameEvent(event_type=EventType.SCENE_OPENED.value),
        session_id=_SESSION_ID,
        scene_id=_SCENE_ID,
        occurred_at=_STARTED_AT,
    )

    store.upsert_entity(
        _CAMPAIGN_ID,
        "mara",
        "Mara Venn",
        entity_type="character",
        system_state={"resources": {"resolve": 2}},
    )
    store.upsert_entity(
        _CAMPAIGN_ID,
        "archivist",
        "The Archivist",
        entity_type="npc",
        system_state={"resources": {"composure": 1}},
        metadata={"agenda": "Keep the floodgate secret"},
    )

    ingestor = MarkdownIngestor()
    ingest_context = IngestContext(
        connection=connection,
        content_pack_id=pack.id,
        system_id=pack.system_id,
        visibility="PUBLIC",
    )
    setting_document_id = ingestor.ingest(
        pack_directory / "setting.md",
        ingest_context,
    )
    rules_document_id = ingestor.ingest(
        pack_directory / "rules.md",
        ingest_context,
    )
    gm_document_id = ingestor.ingest(
        pack_directory / "gm-notes.md",
        IngestContext(
            connection=connection,
            content_pack_id=pack.id,
            system_id=pack.system_id,
            visibility="GM",
        ),
    )
    _index_document(connection, setting_document_id, RetrievalNamespace.SETTING)
    _index_document(connection, rules_document_id, RetrievalNamespace.SYSTEM)
    _index_document(connection, gm_document_id, RetrievalNamespace.SETTING)

    runtime = TabletopRuntime(
        _REPO_ROOT,
        workspace=Workspace.CAMPAIGN,
        campaign_roots=[campaign_root],
        active_campaign=_CAMPAIGN_ID,
        connection=connection,
    )
    simple = runtime.resolve_action(
        _action(
            "check",
            parameters={"expression": "1d2", "difficulty": 1},
        )
    )
    assert simple["data"]["resolution"]["outcome"]["check_result"] == "success"

    opposed = runtime.resolve_action(
        _action(
            "opposed_check",
            targets=[{"id": "archivist"}],
            parameters={
                "actor_expression": "1d2+5",
                "target_expression": "1d2",
            },
        )
    )
    assert opposed["data"]["resolution"]["outcome"]["opposed_result"] == "actor"

    unresolved = runtime.resolve_action(
        _action("check", parameters={"expression": "1d20"})
    )
    assert unresolved["data"]["resolution"]["status"] == "unresolved"
    assert unresolved["data"]["event"] is None
    adjudication = unresolved["data"]["adjudication"]
    context = unresolved["data"]["context"]
    ruling = ruling_from_adjudication(
        AdjudicationResult(
            request=AdjudicationRequest(
                status=ResolutionStatus(adjudication["status"]),
                action=parse_game_action(unresolved["data"]["action"]),
                context=ResolutionContext(
                    campaign_id=context["campaign_id"],
                    system_id=context["system_id"],
                    scene_id=context["scene_id"],
                    state=context["state"],
                ),
                detail=adjudication["detail"],
            ),
            decision="The rusted gate opens after Mara sacrifices one point of resolve.",
        ),
        ruling_id="ruling-rusted-gate",
        scope="rusted-gate",
        session_id=_SESSION_ID,
        created_at="2026-09-22T12:10:00+00:00",
    )
    assert runtime.record_ruling(ruling)["ok"] is True

    public_fact = Fact(
        fact_id="fact-lantern-maker",
        fact_scope=FactScope.CAMPAIGN,
        setting_id=None,
        campaign_id=_CAMPAIGN_ID,
        subject_id="brass-lantern",
        predicate="was-made-by",
        value="The Archivist",
        visibility="PUBLIC",
        created_at="2026-09-22T12:11:00+00:00",
    )
    _record_fact(connection, public_fact)
    promoted_public_fact = promote_fact(connection, public_fact)
    reveal_fact(connection, promoted_public_fact)

    gm_fact = Fact(
        fact_id="fact-floodgate-saboteur",
        fact_scope=FactScope.CAMPAIGN,
        setting_id=None,
        campaign_id=_CAMPAIGN_ID,
        subject_id="archivist",
        predicate="opened",
        value="the floodgate",
        visibility="GM",
        created_at="2026-09-22T12:12:00+00:00",
    )
    _record_fact(connection, gm_fact)
    promote_fact(connection, gm_fact)

    store_edge(
        connection,
        RelationshipEdge(
            relationship_id="relationship-mara-archivist",
            owner_scope="campaign",
            setting_id=None,
            campaign_id=_CAMPAIGN_ID,
            source_id="mara",
            relationship_type="distrusts",
            target_id="archivist",
            metadata={"formed_in": _SCENE_ID},
            visibility="PARTY",
            valid_from="2026-09-22",
        ),
    )

    clock = runtime.resolve_action(
        _action(
            "decrement_resource",
            parameters={
                "path": ["campaign", "system", "clocks", "flood", "remaining"],
                "amount": 1,
            },
        )
    )
    assert clock["data"]["resolution"]["outcome"]["new_value"] == 3

    rule_results = LexicalRetriever(connection).search(
        "opposed check",
        RetrievalFilters(
            namespace=RetrievalNamespace.SYSTEM,
            content_pack_id=pack.id,
            system_id="freeform",
            visibility="PUBLIC",
        ),
        limit=5,
    )
    assert len(rule_results) == 1
    assert "compares the totals" in " ".join(rule_results[0].text.split())

    runtime.shutdown()
    connection.close()

    restarted_connection = connect(database_path)
    restarted = TabletopRuntime(
        _REPO_ROOT,
        workspace=Workspace.CAMPAIGN,
        campaign_roots=[campaign_root],
        active_campaign=_CAMPAIGN_ID,
        connection=restarted_connection,
    )
    restarted_store = CampaignStore(restarted_connection)
    assert restarted_store.get_campaign(_CAMPAIGN_ID)["system_state"]["clocks"][
        "flood"
    ]["remaining"] == 3
    assert restarted_store.get_entity(_CAMPAIGN_ID, "mara")["name"] == "Mara Venn"
    assert RulingStore(restarted_connection).get("ruling-rusted-gate") is not None
    assert restarted_connection.execute(
        "SELECT COUNT(*) FROM documents"
    ).fetchone()[0] == 3

    player_facts = restarted_store.get_facts(_CAMPAIGN_ID, viewpoint=_PLAYER)
    assert {fact.fact_id for fact in player_facts} == {"fact-lantern-maker"}
    assert "floodgate" not in json.dumps(
        [fact.value for fact in player_facts]
    ).lower()
    player_setting_results = LexicalRetriever(restarted_connection).search(
        "archivist opened floodgate",
        RetrievalFilters(
            namespace=RetrievalNamespace.SETTING,
            content_pack_id=pack.id,
            system_id="freeform",
            visibility="PUBLIC",
        ),
        limit=5,
    )
    assert "intends to blame Mara" not in " ".join(
        result.text for result in player_setting_results
    )
    gm_setting_results = LexicalRetriever(restarted_connection).search(
        "archivist opened floodgate",
        RetrievalFilters(
            namespace=RetrievalNamespace.SETTING,
            content_pack_id=pack.id,
            system_id="freeform",
            visibility="GM",
        ),
        limit=5,
    )
    assert "intends to blame Mara" in " ".join(
        result.text for result in gm_setting_results
    )
    player_relationships = query_edges(
        restarted_connection,
        owner_scope="campaign",
        campaign_id=_CAMPAIGN_ID,
        entity_id="mara",
        as_of="2026-09-23",
        viewpoint=_PLAYER,
    )
    assert [edge.relationship_id for edge in player_relationships] == [
        "relationship-mara-archivist"
    ]

    ended = restarted.end_session()
    assert ended["ok"] is True
    assert ended["data"]["session"]["ended_at"] is not None
    assert ended["data"]["session"]["event_range"]["start"] == 1

    projected_campaign = yaml.safe_load(
        (projection_directory / "campaign.yaml").read_text()
    )
    projected_state = yaml.safe_load(
        (projection_directory / "state" / "campaign.yaml").read_text()
    )
    projected_world = yaml.safe_load(
        (projection_directory / "world" / "facts.yaml").read_text()
    )
    projected_gm = yaml.safe_load(
        (projection_directory / "gm" / "facts.yaml").read_text()
    )
    assert projected_campaign["campaign_id"] == _CAMPAIGN_ID
    assert projected_state["system"]["clocks"]["flood"]["remaining"] == 3
    assert [fact["fact_id"] for fact in projected_world["facts"]] == [
        "fact-lantern-maker"
    ]
    assert [fact["fact_id"] for fact in projected_gm["facts"]] == [
        "fact-floodgate-saboteur"
    ]
    assert restarted.shutdown()["ok"] is True
    restarted_connection.close()
