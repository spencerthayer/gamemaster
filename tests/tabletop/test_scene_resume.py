"""Resume and prompt context consume the structured authoritative snapshot."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.api.visibility import (
    Viewpoint,
    VisibilityKind,
    VisibilityScope,
    gm_viewpoint,
)
from tabletop.campaign.event_store import (
    EventStore,
    close_scene_event,
    entity_entered_event,
    open_scene_event,
)
from tabletop.campaign.models import (
    CanonState,
    Fact,
    FactScope,
    KnowledgeState,
    PresenceType,
)
from tabletop.campaign.resume import resume_snapshot
from tabletop.campaign.scene_snapshot import build_scene_snapshot
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "campaign-1"


def _player_viewpoint(entity_id: str = "pc-ada") -> Viewpoint:
    return Viewpoint(
        scope=VisibilityScope(kind=VisibilityKind.CHARACTER, target=entity_id),
        party_member=True,
    )


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "resume.db")
    migrate(connection)
    store = CampaignStore(connection)
    store.create_campaign(_CAMPAIGN, "First", "freeform")
    for entity_id in ("pc-ada", "npc-vor"):
        store.upsert_entity(_CAMPAIGN, entity_id, entity_id)
    yield connection
    connection.close()


# -- helpers that play the way the runtime does: rows and events together ----


def _record_open(conn: sqlite3.Connection, scene_id: str, name: str) -> None:
    scene = SceneStore(conn).open_scene(_CAMPAIGN, scene_id, name)
    EventStore(conn).append(
        _CAMPAIGN,
        open_scene_event(
            scene_id=scene.scene_id, name=scene.name, started_at=scene.started_at
        ),
        scene_id=scene.scene_id,
    )


def _record_enter(
    conn: sqlite3.Connection,
    scene_id: str,
    entity_id: str,
    presence_type: PresenceType,
) -> None:
    member = SceneStore(conn).enter(_CAMPAIGN, scene_id, entity_id, presence_type)
    EventStore(conn).append(
        _CAMPAIGN,
        entity_entered_event(
            scene_id=scene_id,
            entity_id=entity_id,
            presence_type=presence_type.value,
            entered_at=member.entered_at,
        ),
        scene_id=scene_id,
    )


def _record_close(conn: sqlite3.Connection, scene_id: str) -> None:
    store = SceneStore(conn)
    present = store.get_present_entity_ids(_CAMPAIGN, scene_id)
    scene = store.close_scene(_CAMPAIGN, scene_id)
    EventStore(conn).append(
        _CAMPAIGN,
        close_scene_event(
            scene_id=scene_id, ended_at=scene.ended_at or "", exited_entity_ids=present
        ),
        scene_id=scene_id,
    )


def _play_through_a_scene(conn: sqlite3.Connection) -> None:
    """Play scene A with two present, close it, then open scene B."""

    _record_open(conn, "scene-a", "Crossroads")
    _record_enter(conn, "scene-a", "pc-ada", PresenceType.PC)
    _record_enter(conn, "scene-a", "npc-vor", PresenceType.NPC)
    SceneStore(conn).set_game_time(_CAMPAIGN, in_world_label="Day 1, morning")
    _record_close(conn, "scene-a")
    _record_open(conn, "scene-b", "Archive")


def _add_gm_secret(conn: sqlite3.Connection) -> None:
    CampaignStore(conn).add_fact(
        Fact(
            fact_id="fact-secret",
            fact_scope=FactScope.CAMPAIGN,
            setting_id=None,
            campaign_id=_CAMPAIGN,
            subject_id="npc-vor",
            predicate="intends-to-blame",
            value="Ada",
            canon_state=CanonState.CONFIRMED,
            knowledge_state=KnowledgeState.UNREVEALED,
            visibility="GM",
            created_at="2026-09-25T00:00:00+00:00",
        )
    )


# -- tests ------------------------------------------------------------------


def test_snapshot_exposes_every_required_key(conn: sqlite3.Connection) -> None:
    _play_through_a_scene(conn)
    snapshot = build_scene_snapshot(conn, _CAMPAIGN, viewpoint=gm_viewpoint())
    for key in (
        "campaign",
        "session",
        "scene",
        "present_entities",
        "recent_events",
        "active_rulings",
        "relevant_facts",
        "pending_contradictions",
        "game_time",
    ):
        assert key in snapshot, f"snapshot is missing {key!r}"


def test_snapshot_names_the_current_scene_and_clock(conn: sqlite3.Connection) -> None:
    _play_through_a_scene(conn)
    snapshot = build_scene_snapshot(conn, _CAMPAIGN, viewpoint=gm_viewpoint())
    assert snapshot["scene"]["scene_id"] == "scene-b"
    assert snapshot["scene"]["status"] == "open"
    assert snapshot["open_scene_id"] == "scene-b"
    assert snapshot["game_time"]["in_world_label"] == "Day 1, morning"


def test_snapshot_lists_present_entities_by_name(conn: sqlite3.Connection) -> None:
    _record_open(conn, "scene-1", "Crossroads")
    _record_enter(conn, "scene-1", "pc-ada", PresenceType.PC)
    _record_enter(conn, "scene-1", "npc-vor", PresenceType.NPC)
    SceneStore(conn).exit(_CAMPAIGN, "scene-1", "npc-vor")

    snapshot = build_scene_snapshot(conn, _CAMPAIGN, viewpoint=gm_viewpoint())
    present = snapshot["present_entities"]
    assert [item["entity_id"] for item in present] == ["pc-ada"]
    assert present[0]["name"] == "pc-ada"
    assert present[0]["presence_type"] == "pc"


def test_snapshot_reads_authoritative_events(conn: sqlite3.Connection) -> None:
    _play_through_a_scene(conn)
    snapshot = build_scene_snapshot(conn, _CAMPAIGN, viewpoint=gm_viewpoint())
    types = [item["event_type"] for item in snapshot["recent_events"]]
    assert "scene.opened" in types
    assert "scene.closed" in types
    assert "scene.entity_entered" in types


def test_resume_returns_the_real_scene_instead_of_unknown(
    conn: sqlite3.Connection,
) -> None:
    _play_through_a_scene(conn)
    resumed = resume_snapshot(conn, _CAMPAIGN)
    assert resumed["scene"]["scene_id"] == "scene-b"
    assert resumed["in_world_date"] == "Day 1, morning"
    assert resumed["scene"] != "unknown"
    assert resumed["in_world_date"] != "unknown"


def test_resume_keeps_a_closed_scene_inspectable(conn: sqlite3.Connection) -> None:
    _record_open(conn, "scene-a", "Crossroads")
    _record_close(conn, "scene-a")

    snapshot = build_scene_snapshot(conn, _CAMPAIGN, viewpoint=gm_viewpoint())
    assert snapshot["scene"]["scene_id"] == "scene-a"
    assert snapshot["scene"]["status"] == "closed"
    assert snapshot["open_scene_id"] is None


def test_snapshot_for_a_campaign_with_no_scene_is_honest(
    conn: sqlite3.Connection,
) -> None:
    snapshot = build_scene_snapshot(conn, _CAMPAIGN, viewpoint=gm_viewpoint())
    assert snapshot["scene"] is None
    assert snapshot["open_scene_id"] is None
    assert snapshot["game_time"] is None


def test_player_snapshot_never_carries_gm_only_facts(
    conn: sqlite3.Connection,
) -> None:
    _add_gm_secret(conn)
    _play_through_a_scene(conn)

    player = build_scene_snapshot(conn, _CAMPAIGN, viewpoint=_player_viewpoint())
    rendered = repr(player)
    assert "intends-to-blame" not in rendered
    assert "fact-secret" not in rendered
    assert all(fact["fact_id"] != "fact-secret" for fact in player["relevant_facts"])


def test_gm_snapshot_does_see_gm_only_facts(conn: sqlite3.Connection) -> None:
    _add_gm_secret(conn)
    gm = build_scene_snapshot(conn, _CAMPAIGN, viewpoint=gm_viewpoint())
    assert any(fact["fact_id"] == "fact-secret" for fact in gm["relevant_facts"])


def test_building_a_snapshot_writes_no_events(conn: sqlite3.Connection) -> None:
    _play_through_a_scene(conn)
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    build_scene_snapshot(conn, _CAMPAIGN, viewpoint=gm_viewpoint())
    build_scene_snapshot(conn, _CAMPAIGN, viewpoint=_player_viewpoint())
    after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert before == after


def test_snapshot_of_an_unknown_campaign_raises(conn: sqlite3.Connection) -> None:
    with pytest.raises(LookupError):
        build_scene_snapshot(conn, "missing", viewpoint=gm_viewpoint())


# -- prompt context consumes the same snapshot --------------------------------


def test_prompt_context_includes_the_current_scene(
    conn: sqlite3.Connection,
) -> None:
    from tabletop.api.workspace import Workspace
    from tabletop.orchestration.prompt_context import build_prompt_context_snapshot

    _play_through_a_scene(conn)
    snapshot = build_prompt_context_snapshot(
        conn, workspace=Workspace.CAMPAIGN, campaign_id=_CAMPAIGN
    )
    assert "Archive" in snapshot.text
    assert "Day 1, morning" in snapshot.text
    assert "current_scene" in snapshot.source_kinds


def test_player_prompt_context_omits_gm_only_state(
    conn: sqlite3.Connection,
) -> None:
    from tabletop.api.workspace import Workspace
    from tabletop.orchestration.prompt_context import (
        build_player_prompt_context_snapshot,
    )

    _add_gm_secret(conn)
    _play_through_a_scene(conn)
    snapshot = build_player_prompt_context_snapshot(
        conn,
        workspace=Workspace.PLAYER,
        campaign_id=_CAMPAIGN,
        viewpoint=_player_viewpoint(),
    )
    assert "intends-to-blame" not in snapshot.text
    assert "fact-secret" not in snapshot.text
    assert "Archive" in snapshot.text


def test_prompt_context_renders_no_python_repr(conn: sqlite3.Connection) -> None:
    from tabletop.api.workspace import Workspace
    from tabletop.orchestration.prompt_context import build_prompt_context_snapshot

    _play_through_a_scene(conn)
    snapshot = build_prompt_context_snapshot(
        conn, workspace=Workspace.CAMPAIGN, campaign_id=_CAMPAIGN
    )
    assert "{'campaign_id'" not in snapshot.text
    assert "scene_id" not in snapshot.text


def test_the_snapshot_reports_the_newest_scene_not_an_alphabetical_one(
    conn: sqlite3.Connection,
) -> None:
    """Scene ids that sort the wrong way must not decide what the model sees.

    Stored timestamps are truncated to whole seconds, so two scenes routinely
    share a `started_at` and an alphabetical tiebreak would report the closed
    one forever after.
    """
    scenes = SceneStore(conn)
    scenes.open_scene(_CAMPAIGN, "zzz", "First", started_at="2026-01-01T00:00:00Z")
    scenes.close_scene(_CAMPAIGN, "zzz", ended_at="2026-01-01T01:00:00Z")
    scenes.open_scene(_CAMPAIGN, "aaa", "Second", started_at="2026-01-01T00:00:00Z")

    snapshot = build_scene_snapshot(conn, _CAMPAIGN, viewpoint=gm_viewpoint())
    assert snapshot["scene"]["scene_id"] == "aaa"
    assert snapshot["scene"]["status"] == "open"
