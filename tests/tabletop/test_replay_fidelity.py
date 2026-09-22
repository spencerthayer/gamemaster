"""Operator replay-fidelity reports stay off the skill surface."""

from __future__ import annotations

from pathlib import Path

from tabletop.api.events import GameEvent
from tabletop.api.workspace import Workspace
from tabletop.campaign.event_store import EventStore
from tabletop.campaign.replay_fidelity import main, report_database
from tabletop.campaign.setting_events import SettingEventStore, SettingEventType
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate, transaction


def test_mixed_generations_name_the_blocking_type(tmp_path: Path) -> None:
    connection = connect(tmp_path / "fidelity.db")
    migrate(connection)
    connection.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("setting-1", "Owned", "2026-09-22T00:00:00Z"),
    )
    CampaignStore(connection).create_campaign(
        "campaign-1", "Owned", "freeform", setting_id="setting-1"
    )
    connection.execute(
        "INSERT INTO events "
        "(campaign_id, sequence, event_type, payload, occurred_at, event_schema_version) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "campaign-1",
            1,
            "ruling.recorded",
            '{"ruling_id":"old"}',
            "2026-09-22T00:00:00Z",
            0,
        ),
    )
    EventStore(connection).append(
        "campaign-1",
        GameEvent(event_type="quest.mutated", payload={"quest_id": "q", "quest": {}}),
    )
    with transaction(connection):
        SettingEventStore(connection).append_in_transaction(
            connection,
            "setting-1",
            SettingEventType.SETTING_EDITED,
            {"name": "Owned", "created_at": "2026-09-22T00:00:00Z"},
        )
    reports = { (item.scope, item.owner_id): item for item in report_database(connection) }
    campaign = reports[("campaign", "campaign-1")]
    assert campaign.total_events == 2
    assert campaign.by_type["ruling.recorded"] == 1
    assert campaign.by_generation[0] == 1
    assert campaign.by_generation[1] == 1
    assert campaign.generation_zero == 1
    assert campaign.complete_reconstruction is False
    assert campaign.blocking_types == ("ruling.recorded",)
    setting = reports[("setting", "setting-1")]
    assert setting.complete_reconstruction is True
    assert setting.generation_zero == 0
    text = Path(tmp_path / "fidelity.db")
    assert main([str(text)]) == 0
    names = {skill.name for skill in Workspace.CAMPAIGN.skills}
    names.update(skill.name for skill in Workspace.SETTING.skills)
    assert "replay-fidelity" not in names
    connection.close()


def test_generation_one_history_is_complete(tmp_path: Path) -> None:
    connection = connect(tmp_path / "complete.db")
    migrate(connection)
    CampaignStore(connection).create_campaign("campaign-1", "Owned", "freeform")
    EventStore(connection).append(
        "campaign-1",
        GameEvent(
            event_type="session.started",
            payload={"session_id": "s1", "started_at": "2026-09-22T00:00:00Z"},
        ),
    )
    report = report_database(connection)[0]
    assert report.complete_reconstruction is True
    assert report.generation_zero == 0
    assert report.blocking_types == ()
    connection.close()
