"""Turn receipts are diagnostic rows, not campaign events."""

from __future__ import annotations

from pathlib import Path

from tabletop.api.actions import GameAction
from tabletop.api.entities import EntityRef
from tabletop.campaign.event_store import EventStore
from tabletop.campaign.projections import project_campaign
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.turn import play_turn
from tabletop.plugins.registry import PluginRegistry
from tabletop.storage.sqlite import connect, migrate
from systems.freeform import FreeformPlugin


def test_play_turn_writes_a_deletable_receipt(tmp_path: Path) -> None:
    connection = connect(tmp_path / "receipts.db")
    migrate(connection)
    CampaignStore(connection).create_campaign("campaign-1", "Owned", "freeform")
    action = GameAction(
        action_type="check",
        actor=EntityRef("hero"),
        targets=(),
        parameters={"difficulty": 10, "expression": "1d6"},
    )
    registry = PluginRegistry()
    registry.register(FreeformPlugin())
    play_turn(
        registry,
        connection,
        action,
        campaign_id="campaign-1",
        system_id="freeform",
    )
    play_turn(
        registry,
        connection,
        action,
        campaign_id="campaign-1",
        system_id="freeform",
    )
    rows = connection.execute(
        "SELECT campaign_id, system_id, provider, model, tokens_in, event_sequence "
        "FROM turn_receipts ORDER BY rowid"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["provider"] is None
    assert rows[0]["tokens_in"] is None
    assert rows[0]["event_sequence"] is not None
    connection.execute("DELETE FROM turn_receipts")
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] >= 1
    projection = project_campaign(EventStore(connection).read("campaign-1"))
    assert projection.campaign_id == "campaign-1"
    connection.close()
