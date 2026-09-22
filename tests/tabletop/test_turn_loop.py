"""Turn loop: intake through resolve_action, events, and response assembly."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from tabletop.api.actions import GameAction
from tabletop.api.entities import EntityRef
from tabletop.api.errors import InvalidResolutionError
from tabletop.api.resolution import ResolutionStatus, RollResult
from tabletop.api.workspace import Workspace
from tabletop.campaign.event_store import EventStore, EventType
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.turn import play_turn
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "turn.db")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def registry(tmp_path: Path):
    runtime = TabletopRuntime(
        _REPO_ROOT,
        campaign_roots=[],
        plugin_roots=[],
        workspace=Workspace.CAMPAIGN,
    )
    return runtime._registry


def _seed_freeform(conn, *, resources: dict | None = None) -> None:
    store = CampaignStore(conn)
    store.create_campaign(
        "campaign-1",
        "Freeform Run",
        "freeform",
        system_state=resources or {},
    )


def _seed_dnd5e(conn, *, with_hero: bool = True, with_goblin: bool = True) -> None:
    store = CampaignStore(conn)
    store.create_campaign("campaign-1", "D&D Run", "dnd5e")
    if with_hero:
        store.upsert_entity(
            campaign_id="campaign-1",
            entity_id="hero",
            name="Mara",
            system_state={
                "armor_class": 16,
                "hit_points": 20,
                "max_hit_points": 20,
                "speed": 30,
                "position": {"x": 0, "y": 0},
                "abilities": {
                    "strength": 16,
                    "dexterity": 14,
                    "constitution": 12,
                    "intelligence": 10,
                    "wisdom": 10,
                    "charisma": 8,
                },
                "resources": {"hit_dice": 1},
            },
        )
    if with_goblin:
        store.upsert_entity(
            campaign_id="campaign-1",
            entity_id="goblin",
            name="Goblin",
            system_state={
                "armor_class": 13,
                "hit_points": 7,
                "max_hit_points": 7,
                "speed": 30,
                "position": {"x": 5, "y": 0},
                "abilities": {
                    "strength": 8,
                    "dexterity": 14,
                    "constitution": 10,
                    "intelligence": 10,
                    "wisdom": 8,
                    "charisma": 8,
                },
                "resources": {"hit_dice": 1},
            },
        )


def _patch_rolls(monkeypatch: pytest.MonkeyPatch, totals: list[int]) -> None:
    remaining = iter(totals)

    def fake_roll(expression: str, rng=None) -> RollResult:
        return RollResult(
            expression=expression,
            total=next(remaining),
            details={"rolls": [], "kept": [], "dropped": [], "modifier": 0},
        )

    monkeypatch.setattr("systems.dnd5e.roller.roll", fake_roll)
    monkeypatch.setattr("systems.freeform.roller.roll", fake_roll)


def test_resolved_turn_builds_context_resolves_persists_and_assembles(
    conn, registry
) -> None:
    _seed_dnd5e(conn)
    action = GameAction(
        actor=EntityRef(id="hero"),
        action_type="move",
        parameters={"destination": {"x": 10, "y": 0}},
    )

    result = play_turn(
        registry,
        conn,
        action,
        campaign_id="campaign-1",
        system_id="dnd5e",
    )

    assert result.resolution.status is ResolutionStatus.RESOLVED
    assert result.context.campaign_id == "campaign-1"
    assert result.context.system_id == "dnd5e"
    assert result.context.state["entities"]["hero"]["system"]["speed"] == 30
    assert result.event is not None
    assert result.event.event_type == EventType.ACTION_RESOLVED.value
    assert result.adjudication is None
    assert result.narration == result.resolution.explanation
    assert result.resolution.explanation
    assert result.narration == result.resolution.explanation
    entity = CampaignStore(conn).get_entity("campaign-1", "hero")
    assert entity is not None
    assert entity["system_state"]["position"] == {"x": 10, "y": 0}
    events = EventStore(conn).read("campaign-1")
    assert len(events) == 1
    assert events[0].event_type == EventType.ACTION_RESOLVED.value


def test_resolved_turn_failure_leaves_neither_state_nor_events(
    conn, registry, monkeypatch
) -> None:
    _seed_dnd5e(conn)
    _patch_rolls(monkeypatch, [18, 12])
    action = GameAction(
        actor=EntityRef(id="hero"),
        action_type="roll_initiative",
        targets=(EntityRef(id="hero"), EntityRef(id="goblin")),
        parameters={},
    )

    with pytest.raises(InvalidResolutionError):
        play_turn(
            registry,
            conn,
            action,
            campaign_id="campaign-1",
            system_id="dnd5e",
            scene_id="missing-scene",
        )

    assert CampaignStore(conn).get_campaign("campaign-1")["system_state"] == {}
    assert EventStore(conn).read("campaign-1") == []


def test_ruling_required_routes_to_adjudication_without_persist(
    conn, registry, monkeypatch
) -> None:
    _seed_dnd5e(conn)
    _patch_rolls(monkeypatch, [15, 15])
    action = GameAction(
        actor=EntityRef(id="hero"),
        action_type="roll_initiative",
        targets=(EntityRef(id="hero"), EntityRef(id="goblin")),
        parameters={},
    )

    result = play_turn(
        registry,
        conn,
        action,
        campaign_id="campaign-1",
        system_id="dnd5e",
    )

    assert result.resolution.status is ResolutionStatus.RULING_REQUIRED
    assert result.adjudication is not None
    assert result.adjudication.headline == "The rules leave this to GM judgment."
    assert result.adjudication.detail == result.resolution.ruling_question
    assert result.event is None
    assert EventStore(conn).read("campaign-1") == []
    assert CampaignStore(conn).get_campaign("campaign-1")["system_state"] == {}


def test_unresolved_routes_to_adjudication_without_persist(conn, registry) -> None:
    _seed_freeform(conn)
    action = GameAction(
        actor=EntityRef(id="hero"),
        action_type="check",
        parameters={"expression": "1d20"},
    )

    result = play_turn(
        registry,
        conn,
        action,
        campaign_id="campaign-1",
        system_id="freeform",
    )

    assert result.resolution.status is ResolutionStatus.UNRESOLVED
    assert result.adjudication is not None
    assert result.adjudication.headline == "Required information is missing."
    assert result.adjudication.detail == result.resolution.explanation
    assert result.event is None
    assert EventStore(conn).read("campaign-1") == []


def test_unsupported_routes_to_adjudication_without_persist(conn, registry) -> None:
    _seed_freeform(conn)
    action = GameAction(
        actor=EntityRef(id="hero"),
        action_type="cast_spell",
    )

    result = play_turn(
        registry,
        conn,
        action,
        campaign_id="campaign-1",
        system_id="freeform",
    )

    assert result.resolution.status is ResolutionStatus.UNSUPPORTED
    assert result.adjudication is not None
    assert (
        result.adjudication.headline
        == "This system plugin does not implement this."
    )
    assert result.adjudication.detail == result.resolution.explanation
    assert result.event is None
    assert EventStore(conn).read("campaign-1") == []


def test_non_resolved_statuses_have_distinct_framing(conn, registry, monkeypatch) -> None:
    _seed_dnd5e(conn)
    _seed_freeform_campaign = CampaignStore(conn)
    _seed_freeform_campaign.create_campaign("campaign-2", "Freeform B", "freeform")

    _patch_rolls(monkeypatch, [15, 15])
    ruling = play_turn(
        registry,
        conn,
        GameAction(
            actor=EntityRef(id="hero"),
            action_type="roll_initiative",
            targets=(EntityRef(id="hero"), EntityRef(id="goblin")),
        ),
        campaign_id="campaign-1",
        system_id="dnd5e",
    )
    unresolved = play_turn(
        registry,
        conn,
        GameAction(
            actor=EntityRef(id="hero"),
            action_type="check",
            parameters={"expression": "1d20"},
        ),
        campaign_id="campaign-2",
        system_id="freeform",
    )
    unsupported = play_turn(
        registry,
        conn,
        GameAction(actor=EntityRef(id="hero"), action_type="cast_spell"),
        campaign_id="campaign-2",
        system_id="freeform",
    )

    headlines = {
        ruling.adjudication.headline,
        unresolved.adjudication.headline,
        unsupported.adjudication.headline,
    }
    assert len(headlines) == 3


def test_narration_comes_from_resolution_not_replacement(
    conn, registry, monkeypatch
) -> None:
    _seed_freeform(conn)
    _patch_rolls(monkeypatch, [17])
    action = GameAction(
        actor=EntityRef(id="hero"),
        action_type="check",
        parameters={"expression": "1d20", "difficulty": 10},
    )

    result = play_turn(
        registry,
        conn,
        action,
        campaign_id="campaign-1",
        system_id="freeform",
    )

    assert result.resolution.status is ResolutionStatus.RESOLVED
    assert result.narration == result.resolution.explanation
    assert "17" in result.narration
    assert "10" in result.narration
    assert "sword" not in result.narration.lower()


def test_action_resolution_system_always_calls_plugin(conn, registry, monkeypatch) -> None:
    _seed_freeform(conn)
    plugin = registry.get("freeform")
    calls: list[GameAction] = []
    original = plugin.resolve

    def tracking_resolve(action, context):
        calls.append(action)
        return original(action, context)

    monkeypatch.setattr(plugin, "resolve", tracking_resolve)
    action = GameAction(
        actor=EntityRef(id="hero"),
        action_type="check",
        parameters={"expression": "1d2+4", "difficulty": 5},
    )
    _patch_rolls(monkeypatch, [6])

    result = play_turn(
        registry,
        conn,
        action,
        campaign_id="campaign-1",
        system_id="freeform",
    )

    assert len(calls) == 1
    assert calls[0] is action
    assert result.resolution.status is ResolutionStatus.RESOLVED
    assert result.resolution.outcome["roll_total"] == 6
    assert result.resolution.explanation == result.narration
    # play_turn must go through the guard; it must not call plugin.resolve itself.
    play_body = inspect.getsource(play_turn)
    assert "resolve_action(registry, action, context)" in play_body
    assert "plugin.resolve" not in play_body


def test_runtime_resolve_action_returns_real_result(conn, tmp_path, monkeypatch) -> None:
    _seed_freeform(conn)
    _patch_rolls(monkeypatch, [6])
    runtime = TabletopRuntime(
        tmp_path,
        campaign_roots=[],
        plugin_roots=[_REPO_ROOT / "systems"],
        connection=conn,
        active_campaign="campaign-1",
        workspace=Workspace.CAMPAIGN,
    )
    payload = {
        "actor": {"id": "hero"},
        "action_type": "check",
        "parameters": {"expression": "1d2+4", "difficulty": 5},
    }

    response = runtime.resolve_action(json.dumps(payload))

    assert response["ok"] is True
    assert response["operation"] == "resolve-action"
    assert "error" not in response
    assert response["data"]["resolution"]["status"] == "resolved"
    assert response["data"]["narration"]
    assert response["data"]["resolution"]["explanation"] == response["data"]["narration"]
    events = EventStore(conn).read("campaign-1")
    assert len(events) == 1
