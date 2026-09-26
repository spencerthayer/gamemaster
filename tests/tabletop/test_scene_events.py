"""Scene lifecycle events and their replay into ``CampaignProjection``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.campaign.event_store import (
    EventStore,
    EventType,
    PersistedEvent,
    entity_entered_event,
    open_scene_event,
)
from tabletop.campaign.models import PresenceType
from tabletop.campaign.projections import project_campaign
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate, transaction


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "scene-events.db")
    migrate(connection)
    CampaignStore(connection).create_campaign("campaign-1", "First", "freeform")
    connection.execute(
        "INSERT INTO entities (entity_id, owner_scope, campaign_id, name) "
        "VALUES ('pc-ada', 'campaign', 'campaign-1', 'Ada')",
    )
    yield connection
    connection.close()


def _event(
    event_type: EventType,
    payload: dict[str, object],
    *,
    sequence: int = 1,
    scene_id: str | None = "scene-1",
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
        occurred_at="2026-09-25T00:00:00Z",
        event_schema_version=1,
    )


def _opened(
    *,
    scene_id: str = "scene-1",
    name: str = "Crossroads",
    started_at: str = "2026-09-25T00:00:00Z",
) -> PersistedEvent:
    return _event(
        EventType.SCENE_OPENED,
        {
            "scene_id": scene_id,
            "name": name,
            "session_id": "session-1",
            "location_entity_id": None,
            "in_world_started_at": None,
            "started_at": started_at,
        },
        scene_id=scene_id,
    )


def test_scene_opened_event_names_the_scene_and_its_start() -> None:
    event = open_scene_event(
        scene_id="scene-1",
        name="Crossroads",
        started_at="2026-09-25T00:00:00Z",
        session_id="session-1",
    )
    assert event.event_type == EventType.SCENE_OPENED.value
    assert event.payload["scene_id"] == "scene-1"
    assert event.payload["name"] == "Crossroads"
    assert event.payload["started_at"] == "2026-09-25T00:00:00Z"
    assert event.payload["session_id"] == "session-1"


def test_opening_a_scene_projects_its_identity_and_status() -> None:
    projection = project_campaign((_opened(),))
    scene = projection.scenes["scene-1"]
    assert scene["name"] == "Crossroads"
    assert scene["status"] == "open"
    assert scene["started_at"] == "2026-09-25T00:00:00Z"
    assert scene["ended_at"] is None
    assert scene["members"] == {}
    assert projection.campaign_clock is None


def test_closing_a_scene_marks_it_closed_and_exits_present_entities() -> None:
    entered = _event(
        EventType.SCENE_ENTITY_ENTERED,
        {"scene_id": "scene-1", "entity_id": "pc-ada", "presence_type": "pc",
         "entered_at": "2026-09-25T01:00:00Z"},
        sequence=2,
    )
    closed = _event(
        EventType.SCENE_CLOSED,
        {"scene_id": "scene-1", "ended_at": "2026-09-25T05:00:00Z",
         "exited_entity_ids": ["pc-ada"]},
        sequence=3,
    )
    projection = project_campaign((_opened(), entered, closed))
    scene = projection.scenes["scene-1"]
    assert scene["status"] == "closed"
    assert scene["ended_at"] == "2026-09-25T05:00:00Z"
    assert scene["members"]["pc-ada"]["exited_at"] == "2026-09-25T05:00:00Z"


def test_entering_and_exiting_tracks_presence_through_replay() -> None:
    entered = _event(
        EventType.SCENE_ENTITY_ENTERED,
        {"scene_id": "scene-1", "entity_id": "pc-ada", "presence_type": "pc",
         "entered_at": "2026-09-25T01:00:00Z"},
        sequence=2,
    )
    exited = _event(
        EventType.SCENE_ENTITY_EXITED,
        {"scene_id": "scene-1", "entity_id": "pc-ada",
         "exited_at": "2026-09-25T03:00:00Z"},
        sequence=3,
    )
    projection = project_campaign((_opened(), entered, exited))
    member = projection.scenes["scene-1"]["members"]["pc-ada"]
    assert member["presence_type"] == "pc"
    assert member["entered_at"] == "2026-09-25T01:00:00Z"
    assert member["exited_at"] == "2026-09-25T03:00:00Z"


def test_re_entry_replaces_the_previous_presence_interval() -> None:
    first = _event(
        EventType.SCENE_ENTITY_ENTERED,
        {"scene_id": "scene-1", "entity_id": "pc-ada", "presence_type": "pc",
         "entered_at": "2026-09-25T01:00:00Z"},
        sequence=2,
    )
    left = _event(
        EventType.SCENE_ENTITY_EXITED,
        {"scene_id": "scene-1", "entity_id": "pc-ada", "exited_at": "2026-09-25T03:00:00Z"},
        sequence=3,
    )
    again = _event(
        EventType.SCENE_ENTITY_ENTERED,
        {"scene_id": "scene-1", "entity_id": "pc-ada", "presence_type": "npc",
         "entered_at": "2026-09-25T04:00:00Z"},
        sequence=4,
    )
    projection = project_campaign((_opened(), first, left, again))
    member = projection.scenes["scene-1"]["members"]["pc-ada"]
    assert member["entered_at"] == "2026-09-25T04:00:00Z"
    assert member["exited_at"] is None
    assert member["presence_type"] == "npc"


def test_location_change_is_replayed() -> None:
    changed = _event(
        EventType.SCENE_LOCATION_CHANGED,
        {"scene_id": "scene-1", "location_entity_id": "pc-ada",
         "changed_at": "2026-09-25T02:00:00Z"},
        sequence=2,
    )
    projection = project_campaign((_opened(), changed))
    assert projection.scenes["scene-1"]["location_entity_id"] == "pc-ada"


def test_campaign_clock_is_replayed() -> None:
    changed = _event(
        EventType.SCENE_TIME_CHANGED,
        {"in_world_label": "Day 3, morning", "in_world_minutes": 2880,
         "changed_at": "2026-09-25T02:00:00Z"},
        scene_id=None,
        sequence=2,
    )
    projection = project_campaign((_opened(), changed))
    assert projection.campaign_clock == {
        "in_world_label": "Day 3, morning",
        "in_world_minutes": 2880,
        "updated_at": "2026-09-25T02:00:00Z",
    }


def test_replay_is_stable_across_repeated_folds() -> None:
    events = (
        _opened(),
        _event(
            EventType.SCENE_ENTITY_ENTERED,
            {"scene_id": "scene-1", "entity_id": "pc-ada", "presence_type": "pc",
             "entered_at": "2026-09-25T01:00:00Z"},
            sequence=2,
        ),
    )
    assert project_campaign(events) == project_campaign(events)


def test_closing_a_scene_that_never_opened_is_rejected() -> None:
    orphan = _event(
        EventType.SCENE_CLOSED,
        {"scene_id": "scene-1", "ended_at": "2026-09-25T05:00:00Z"},
    )
    with pytest.raises(ValueError, match="no opened scene"):
        project_campaign((orphan,))


def test_entering_a_scene_that_never_opened_is_rejected() -> None:
    orphan = _event(
        EventType.SCENE_ENTITY_ENTERED,
        {"scene_id": "scene-1", "entity_id": "pc-ada", "presence_type": "pc",
         "entered_at": "2026-09-25T01:00:00Z"},
    )
    with pytest.raises(ValueError, match="no opened scene"):
        project_campaign((orphan,))


def test_presence_events_require_an_entity_id() -> None:
    with pytest.raises(ValueError, match="entity_id"):
        project_campaign(
            (_opened(), _event(EventType.SCENE_ENTITY_ENTERED, {"scene_id": "scene-1"}, sequence=2))
        )


def test_opening_a_scene_requires_a_name() -> None:
    with pytest.raises(ValueError, match="name"):
        project_campaign(
            (_event(EventType.SCENE_OPENED, {"scene_id": "scene-1", "started_at": "t"}),)
        )


def test_scene_opened_event_carries_the_scene_id_column() -> None:
    projection = project_campaign((_opened(scene_id="scene-7"),))
    assert list(projection.scenes) == ["scene-7"]


def test_store_mutations_append_matching_events_atomically(
    conn: sqlite3.Connection,
) -> None:
    """The event log and the scene rows must move together or not at all."""
    events = EventStore(conn)
    store = SceneStore(conn)

    with transaction(conn):
        scene = store.open_scene_in_transaction("campaign-1", "scene-1", "Crossroads")
        events.append_in_transaction(
            conn,
            "campaign-1",
            open_scene_event(
                scene_id=scene.scene_id,
                name=scene.name,
                started_at=scene.started_at,
                session_id=None,
            ),
            scene_id=scene.scene_id,
        )
    stored = events.read("campaign-1")
    assert [e.event_type for e in stored] == [EventType.SCENE_OPENED.value]
    assert stored[0].scene_id == "scene-1"
    assert stored[0].payload["name"] == "Crossroads"
    assert project_campaign(stored).scenes["scene-1"]["status"] == "open"


def test_a_failed_scene_write_rolls_back_its_event(
    conn: sqlite3.Connection,
) -> None:
    events = EventStore(conn)
    store = SceneStore(conn)
    with pytest.raises(Exception):
        with transaction(conn):
            store.open_scene_in_transaction("campaign-1", "scene-1", "Crossroads")
            events.append_in_transaction(
                conn,
                "campaign-1",
                open_scene_event(
                    scene_id="scene-1", name="Crossroads", started_at="2026-09-25T00:00:00Z"
                ),
                scene_id="scene-1",
            )
            raise RuntimeError("crash after append")
    assert events.read("campaign-1") == []
    assert store.list_scenes("campaign-1") == ()


def test_replayed_scene_events_match_the_stored_rows(conn: sqlite3.Connection) -> None:
    events = EventStore(conn)
    store = SceneStore(conn)
    scene = store.open_scene("campaign-1", "scene-1", "Crossroads")
    events.append(
        "campaign-1",
        open_scene_event(scene_id=scene.scene_id, name=scene.name, started_at=scene.started_at),
        scene_id=scene.scene_id,
    )
    store.enter("campaign-1", "scene-1", "pc-ada", PresenceType.PC)
    events.append(
        "campaign-1",
        entity_entered_event(
            scene_id="scene-1", entity_id="pc-ada", presence_type="pc",
            entered_at=store.get_members("campaign-1", "scene-1")[0].entered_at,
        ),
        scene_id="scene-1",
    )

    projection = project_campaign(events.read("campaign-1"))
    stored = store.get_scene("campaign-1", "scene-1")
    assert projection.scenes["scene-1"]["name"] == stored.name
    assert projection.scenes["scene-1"]["status"] == stored.status.value
    assert set(projection.scenes["scene-1"]["members"]) == {
        m.entity_id for m in store.get_members("campaign-1", "scene-1")
    }
