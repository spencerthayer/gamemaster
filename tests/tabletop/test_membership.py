"""Membership store and schema constraints."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from tabletop.campaign.event_store import EventStore, EventType
from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate

_FORBIDDEN = (
    re.compile(r"hit[_\s]?points?", re.IGNORECASE),
    re.compile(r"armor[_\s]?class", re.IGNORECASE),
    re.compile(r"(^|_)class($|_)", re.IGNORECASE),
    re.compile(r"level", re.IGNORECASE),
    re.compile(r"race", re.IGNORECASE),
)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "membership.db")
    migrate(connection)
    store = CampaignStore(connection)
    store.create_campaign("alpha", "Alpha", "freeform")
    store.create_campaign("beta", "Beta", "freeform")
    store.upsert_entity("alpha", "ada", "Ada", entity_type="character")
    store.upsert_entity("alpha", "bo", "Bo", entity_type="character")
    store.upsert_entity("beta", "cy", "Cy", entity_type="character")
    yield connection
    connection.close()


def test_membership_many_to_many_controls(conn: sqlite3.Connection) -> None:
    membership = MembershipStore(conn)
    membership.add_participant("alpha", "gm1", "GM", "gm")
    membership.add_participant("alpha", "p1", "Ada", "player")
    membership.add_participant("alpha", "p2", "Bo", "player")
    membership.grant_control("alpha", "p1", "ada", "owner")
    membership.grant_control("alpha", "p1", "bo", "shared")
    membership.grant_control("alpha", "p2", "ada", "shared")
    temp_id = membership.grant_control("alpha", "p2", "bo", "temporary")
    membership.grant_control("alpha", "gm1", "ada", "gm")
    membership.revoke_control("alpha", temp_id)

    assert len(membership.list_controls("alpha")) == 4
    assert membership.controlled_entity_ids("alpha", "p1") == frozenset({"ada", "bo"})


def test_principal_uniqueness_and_roles(conn: sqlite3.Connection) -> None:
    membership = MembershipStore(conn)
    membership.add_participant("alpha", "p1", "Ada", "player")
    membership.add_participant("alpha", "p2", "Bo", "player")
    membership.bind_principal("alpha", "p1", "telegram", "42")
    with pytest.raises(sqlite3.IntegrityError):
        membership.bind_principal("alpha", "p2", "telegram", "42")
    with pytest.raises(sqlite3.IntegrityError):
        membership.bind_principal("alpha", "p1", "telegram", "43")
    with pytest.raises(ValueError):
        membership.add_participant("alpha", "p3", "X", "npc")


def test_second_gm_raises_after_first(conn: sqlite3.Connection) -> None:
    membership = MembershipStore(conn)
    membership.add_participant("alpha", "gm1", "GM", "gm")
    with pytest.raises(sqlite3.IntegrityError):
        membership.add_participant("alpha", "gm2", "Other", "gm")


def test_zero_gm_is_legal(conn: sqlite3.Connection) -> None:
    membership = MembershipStore(conn)
    membership.add_participant("alpha", "p1", "Ada", "player")
    assert membership.list_participants("alpha")[0]["role"] == "player"


def test_participant_slug_rejected(conn: sqlite3.Connection) -> None:
    membership = MembershipStore(conn)
    with pytest.raises(ValueError):
        membership.add_participant("alpha", "P 1", "Ada", "player")


def test_remove_participant_ends_controls(conn: sqlite3.Connection) -> None:
    membership = MembershipStore(conn)
    membership.add_participant("alpha", "p1", "Ada", "player")
    membership.grant_control("alpha", "p1", "ada", "owner")
    membership.remove_participant("alpha", "p1")
    assert membership.list_participants("alpha") == []
    assert membership.list_controls("alpha", active_only=False) == []
    types = [event.event_type for event in EventStore(conn).read("alpha")]
    assert EventType.CHARACTER_CONTROL_ENDED.value in types
    assert EventType.PARTICIPANT_REMOVED.value in types


def test_cross_campaign_principal_and_control_fail(conn: sqlite3.Connection) -> None:
    membership = MembershipStore(conn)
    membership.add_participant("alpha", "p1", "Ada", "player")
    membership.add_participant("beta", "only-beta", "Beta Player", "player")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO participant_principals "
            "(principal_id, campaign_id, participant_id, channel, external_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("prin-bad", "alpha", "only-beta", "telegram", "99"),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO character_controls "
            "(control_id, campaign_id, participant_id, entity_id, control, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("c-bad", "alpha", "p1", "cy", "owner", "2026-09-22T00:00:00Z"),
        )


def test_membership_tables_have_no_mechanics_columns(conn: sqlite3.Connection) -> None:
    for table in ("participants", "participant_principals", "character_controls"):
        columns = [
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        ]
        for column in columns:
            for pattern in _FORBIDDEN:
                assert not pattern.search(column), f"{table}.{column}"
