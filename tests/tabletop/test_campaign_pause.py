"""Pausing is auditable and never silently discards work."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.campaign.scenes import (
    pause_campaign,
    resume_campaign,
)
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.turn_job import TurnJobStore
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "campaign-1"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "pause-schema.db")
    migrate(connection)
    CampaignStore(connection).create_campaign(_CAMPAIGN, "First", "freeform")
    yield connection
    connection.close()


def test_the_pause_table_exists_with_an_audit_trail(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(campaign_pause)").fetchall()
    }
    assert columns == {
        "campaign_id", "paused", "reason", "paused_at", "resumed_at", "updated_at"
    }


def test_pausing_records_a_reason_and_time(conn: sqlite3.Connection) -> None:
    pause_campaign(conn, _CAMPAIGN, reason="session break")
    row = conn.execute(
        "SELECT reason, paused_at, resumed_at FROM campaign_pause WHERE campaign_id = ?",
        (_CAMPAIGN,),
    ).fetchone()
    assert row["reason"] == "session break"
    assert row["paused_at"] is not None
    assert row["resumed_at"] is None


def test_resuming_records_when(conn: sqlite3.Connection) -> None:
    pause_campaign(conn, _CAMPAIGN)
    resume_campaign(conn, _CAMPAIGN)
    row = conn.execute(
        "SELECT paused, resumed_at, reason FROM campaign_pause WHERE campaign_id = ?",
        (_CAMPAIGN,),
    ).fetchone()
    assert row["paused"] == 0
    assert row["resumed_at"] is not None
    assert row["reason"] is None


def test_pausing_twice_updates_rather_than_duplicating(
    conn: sqlite3.Connection,
) -> None:
    pause_campaign(conn, _CAMPAIGN, reason="first")
    pause_campaign(conn, _CAMPAIGN, reason="second")
    rows = conn.execute(
        "SELECT reason FROM campaign_pause WHERE campaign_id = ?", (_CAMPAIGN,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["reason"] == "second"


def test_pausing_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "restart.db"
    conn = connect(path)
    migrate(conn)
    CampaignStore(conn).create_campaign(_CAMPAIGN, "First", "freeform")
    pause_campaign(conn, _CAMPAIGN, reason="overnight")
    conn.close()

    reopened = connect(path)
    try:
        row = reopened.execute(
            "SELECT paused FROM campaign_pause WHERE campaign_id = ?", (_CAMPAIGN,)
        ).fetchone()
        assert row["paused"] == 1
    finally:
        reopened.close()


def test_pausing_keeps_every_queued_turn(conn: sqlite3.Connection) -> None:
    store = TurnJobStore(conn)
    turns = [
        store.claim_ingress(_CAMPAIGN, f"message {i}").turn_id for i in range(3)
    ]
    pause_campaign(conn, _CAMPAIGN)
    assert [job.turn_id for job in store.list_for_campaign(_CAMPAIGN)] == turns


def test_deleting_a_campaign_removes_its_pause(conn: sqlite3.Connection) -> None:
    pause_campaign(conn, _CAMPAIGN)
    conn.execute("DELETE FROM campaigns WHERE campaign_id = ?", (_CAMPAIGN,))
    assert conn.execute("SELECT COUNT(*) FROM campaign_pause").fetchone()[0] == 0
