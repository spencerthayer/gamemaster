"""Setting event log: atomic writes, replay, and separation from campaigns."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tabletop.api.workspace import Workspace
from tabletop.campaign.event_store import EventStore
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState
from tabletop.campaign.projections import project_campaign
from tabletop.campaign.setting_events import SettingEventStore, project_setting
from tabletop.campaign.store import CampaignStore
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate


@pytest.fixture
def runtime(tmp_path: Path):
    connection = connect(tmp_path / "settings.db")
    migrate(connection)
    connection.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("setting-1", "Owned", "2026-09-22T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("setting-2", "Other", "2026-09-22T00:00:01Z"),
    )
    CampaignStore(connection).create_campaign(
        "campaign-1",
        "Owned",
        "freeform",
        setting_id="setting-1",
    )
    tabletop = TabletopRuntime(
        tmp_path,
        campaign_roots=[],
        plugin_roots=[],
        connection=connection,
        workspace=Workspace.CAMPAIGN,
        active_campaign="campaign-1",
    )
    try:
        yield tabletop, connection
    finally:
        connection.close()


def test_setting_events_are_immutable(runtime) -> None:
    _runtime, connection = runtime
    assert _runtime.edit_setting(
        json.dumps(
            {
                "setting_id": "setting-1",
                "name": "Renamed",
                "created_at": "2026-09-22T00:00:00Z",
            }
        )
    )["ok"]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(
            "UPDATE setting_events SET event_type = ? WHERE setting_id = ?",
            ("changed", "setting-1"),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("DELETE FROM setting_events")


def test_setting_writes_replay_to_the_stored_rows(runtime) -> None:
    tabletop, connection = runtime
    assert tabletop.edit_setting(
        json.dumps(
            {
                "setting_id": "setting-1",
                "name": "Renamed",
                "created_at": "2026-09-22T00:00:00Z",
            }
        )
    )["ok"]
    assert tabletop.upsert_world_entity(
        json.dumps(
            {
                "entity_id": "harbor",
                "setting_id": "setting-1",
                "name": "Harbor",
                "entity_type": "place",
            }
        )
    )["ok"]
    assert tabletop.record_world_history(
        json.dumps(
            {
                "fact_id": "fact-1",
                "setting_id": "setting-1",
                "subject_id": "harbor",
                "predicate": "founded",
                "value": "by lantern keepers",
                "created_at": "2026-09-22T01:00:00Z",
            }
        )
    )["ok"]
    events = SettingEventStore(connection).read("setting-1")
    assert [event.event_schema_version for event in events] == [1, 1, 1]
    projection = project_setting(events)
    stored_setting = connection.execute(
        "SELECT name FROM settings WHERE setting_id = ?",
        ("setting-1",),
    ).fetchone()
    assert projection.settings["setting-1"]["name"] == stored_setting["name"]
    stored_entity = connection.execute(
        "SELECT name FROM entities WHERE entity_id = ?",
        ("harbor",),
    ).fetchone()
    assert projection.entities["harbor"]["name"] == stored_entity["name"]
    fact = projection.facts["fact-1"]
    assert fact["canon_state"] == "proposed"
    assert fact["knowledge_state"] == "unrevealed"
    assert fact["value"] == "by lantern keepers"


def test_foreign_setting_write_appends_nothing(runtime) -> None:
    tabletop, connection = runtime
    rejected = tabletop.record_world_history(
        json.dumps(
            {
                "fact_id": "fact-x",
                "setting_id": "setting-2",
                "subject_id": "harbor",
                "predicate": "founded",
                "value": "elsewhere",
            }
        )
    )
    assert rejected["ok"] is False
    assert connection.execute("SELECT COUNT(*) FROM setting_events").fetchone()[0] == 0


def test_failed_setting_write_rolls_back_the_event(runtime, monkeypatch) -> None:
    tabletop, connection = runtime

    def explode(*_args, **_kwargs):
        raise sqlite3.IntegrityError("forced")

    monkeypatch.setattr(
        "tabletop.runtime.SettingEventStore.append_in_transaction",
        explode,
    )
    result = tabletop.edit_setting(
        json.dumps({"setting_id": "setting-3", "name": "New"})
    )
    assert result["ok"] is False
    assert (
        connection.execute(
            "SELECT setting_id FROM settings WHERE setting_id = ?",
            ("setting-3",),
        ).fetchone()
        is None
    )
    assert connection.execute("SELECT COUNT(*) FROM setting_events").fetchone()[0] == 0


def test_setting_and_campaign_replays_do_not_share_facts(runtime) -> None:
    tabletop, connection = runtime
    assert tabletop.record_world_history(
        json.dumps(
            {
                "fact_id": "world-fact",
                "setting_id": "setting-1",
                "subject_id": "harbor",
                "predicate": "founded",
                "value": "by lantern keepers",
            }
        )
    )["ok"]
    CampaignStore(connection).add_fact(
        Fact(
            fact_id="campaign-fact",
            fact_scope=FactScope.CAMPAIGN,
            setting_id=None,
            campaign_id="campaign-1",
            subject_id="hero",
            predicate="has-title",
            value="Warden",
            canon_state=CanonState.PROPOSED,
            knowledge_state=KnowledgeState.UNREVEALED,
            created_at="2026-09-22T02:00:00Z",
        )
    )
    setting_projection = project_setting(SettingEventStore(connection).read("setting-1"))
    campaign_projection = project_campaign(EventStore(connection).read("campaign-1"))
    assert "campaign-fact" not in setting_projection.facts
    assert "world-fact" not in campaign_projection.facts
    assert "world-fact" in setting_projection.facts
