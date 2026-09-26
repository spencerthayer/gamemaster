"""One turn is auditable from input through delivery, and redaction holds."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.api.actions import GameAction
from tabletop.api.entities import EntityRef
from tabletop.api.resolution import (
    Resolution,
    ResolutionStatus,
    StateChange,
    StateOperation,
)
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.delivery import DeliveryStore, GenerationReceiptStore
from tabletop.orchestration.gm_commands import explain_turn_for_gm, GmExplainError
from tabletop.orchestration.turn_job import TurnJobStore
from tabletop.storage.sqlite import connect, migrate
from plugins.tabletop.turn_bridge import TurnBridge, commit_action_effect

_CAMPAIGN = "campaign-1"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "explain.db")
    migrate(connection)
    CampaignStore(connection).create_campaign(_CAMPAIGN, "First", "freeform")
    CampaignStore(connection).upsert_entity(
        _CAMPAIGN, "pc-ada", "Ada", system_state={"resources": {"hp": 10}}
    )
    yield connection
    connection.close()


def _action() -> GameAction:
    return GameAction(actor=EntityRef(id="pc-ada"), action_type="apply_damage")


def _resolution() -> Resolution:
    return Resolution(
        outcome={"decremented": 3},
        status=ResolutionStatus.RESOLVED,
        state_changes=(
            StateChange(
                operation=StateOperation.SET,
                path=("entities", "pc-ada", "system", "resources", "hp"),
                value=7,
            ),
        ),
        explanation="hp reduced by 3",
    )


def _played_turn(conn: sqlite3.Connection) -> str:
    bridge = TurnBridge(conn, campaign_id=_CAMPAIGN)
    turn = bridge.receive(
        "Ada forces the gate",
        channel="telegram", conversation_id="chat-1", external_message_id="99",
        principal="telegram:99",
    )
    bridge.start_generation(turn)
    commit_action_effect(
        conn, campaign_id=_CAMPAIGN, turn_id=turn.turn_id, ordinal=0,
        action=_action(), resolution=_resolution(),
    )
    GenerationReceiptStore(conn).record(
        turn.turn_id, 0, provider="test", model="m", response_text="Ada wounds it."
    )
    delivery_id = bridge.enqueue_output(turn, "telegram", "Ada wounds it.")
    bridge.mark_delivered(turn, delivery_id)
    return turn.turn_id


# -- the whole turn ---------------------------------------------------------


def test_the_explanation_covers_the_whole_turn(conn: sqlite3.Connection) -> None:
    turn_id = _played_turn(conn)
    explained = explain_turn_for_gm(conn, _CAMPAIGN, turn_id)
    assert explained["input"]["text"] == "Ada forces the gate"
    assert explained["ingress"]["external_message_id"] == "99"
    assert explained["ingress"]["principal"] == "telegram:99"
    assert explained["action"]["action_type"] == "apply_damage"
    assert explained["mechanical_parameters"] == []
    assert explained["resolution"]["status"] == "resolved"
    assert explained["rolls"] == []
    assert explained["state_changes"][0]["value"] == 7
    assert explained["action_effect"]["status"] == "committed"
    assert explained["generations"][0]["model"] == "m"
    assert explained["deliveries"][0]["status"] == "delivered"
    assert explained["output"] == "Ada wounds it."


def test_the_explanation_reports_a_mechanical_parameter(
    conn: sqlite3.Connection,
) -> None:
    turn = TurnBridge(conn, campaign_id=_CAMPAIGN).receive("Ada forces the gate")
    TurnBridge(conn, campaign_id=_CAMPAIGN).start_generation(turn)
    from tabletop.orchestration.delivery import GenerationReceiptStore

    GenerationReceiptStore(conn).record(
        turn.turn_id, 0, parameters=[{"name": "dc", "value": 15, "source": "ruling"}]
    )
    explained = explain_turn_for_gm(conn, _CAMPAIGN, turn.turn_id)
    assert explained["mechanical_parameters"] == [
        {"name": "dc", "value": 15, "source": "ruling"}
    ]


def test_the_explanation_marks_absent_evidence(conn: sqlite3.Connection) -> None:
    turn = TurnBridge(conn, campaign_id=_CAMPAIGN).receive("Ada waits")
    explained = explain_turn_for_gm(conn, _CAMPAIGN, turn.turn_id)
    assert explained["action"] is None
    assert explained["resolution"] is None
    assert explained["generations"] == []
    assert explained["deliveries"] == []
    assert explained["output"] is None


def test_the_explanation_is_read_only(conn: sqlite3.Connection) -> None:
    turn_id = _played_turn(conn)
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    explain_turn_for_gm(conn, _CAMPAIGN, turn_id)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before


def test_the_explanation_rejects_a_turn_from_another_campaign(
    conn: sqlite3.Connection,
) -> None:
    CampaignStore(conn).create_campaign("campaign-2", "Second", "freeform")
    turn_id = _played_turn(conn)
    with pytest.raises(GmExplainError, match="not in campaign"):
        explain_turn_for_gm(conn, "campaign-2", turn_id)


# -- redaction --------------------------------------------------------------


def test_gm_only_evidence_is_redacted_for_a_non_gm_viewer(
    conn: sqlite3.Connection,
) -> None:
    turn_id = _played_turn(conn)
    from tabletop.api.visibility import (
        Viewpoint,
        VisibilityKind,
        VisibilityScope,
    )

    player = Viewpoint(
        scope=VisibilityScope(kind=VisibilityKind.CHARACTER, target="pc-ada"),
        party_member=True,
    )
    redacted = explain_turn_for_gm(
        conn, _CAMPAIGN, turn_id, viewpoint_is_gm=False, viewpoint=player
    )
    rendered = str(redacted)
    assert "telegram:99" not in rendered


def test_a_gm_sees_the_full_record(conn: sqlite3.Connection) -> None:
    turn_id = _played_turn(conn)
    explained = explain_turn_for_gm(conn, _CAMPAIGN, turn_id)
    assert explained["ingress"]["principal"] == "telegram:99"
