"""Migration 0021: authoritative scene lifecycle, presence, and campaign clock."""

from __future__ import annotations

import re
import shutil
import sqlite3
from pathlib import Path

import pytest

from tabletop.storage.sqlite import connect, migrate

_SOURCE_MIGRATIONS = (
    Path(__file__).resolve().parents[2] / "tabletop" / "storage" / "migrations"
)

_FORBIDDEN_COLUMN_PATTERNS = (
    re.compile(r"hit[_\s]?points?", re.IGNORECASE),
    re.compile(r"armor[_\s]?class", re.IGNORECASE),
    re.compile(r"^level$", re.IGNORECASE),
)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "scenes.db")
    migrate(connection)
    yield connection
    connection.close()


def _seed_campaign(conn: sqlite3.Connection, campaign_id: str = "campaign-1") -> None:
    conn.execute(
        "INSERT INTO campaigns (campaign_id, name, system_id, created_at) "
        "VALUES (?, ?, 'freeform', '2026-09-25T00:00:00+00:00')",
        (campaign_id, f"Campaign {campaign_id}"),
    )


def _seed_entity(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    campaign_id: str = "campaign-1",
) -> None:
    conn.execute(
        "INSERT INTO entities (entity_id, owner_scope, campaign_id, name) "
        "VALUES (?, 'campaign', ?, ?)",
        (entity_id, campaign_id, f"Entity {entity_id}"),
    )


def _open_scene(
    conn: sqlite3.Connection,
    scene_id: str,
    *,
    campaign_id: str = "campaign-1",
    name: str = "Crossroads",
    started_at: str = "2026-09-25T00:00:00Z",
    location_entity_id: str | None = None,
    session_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO scenes "
        "(scene_id, campaign_id, session_id, name, status, started_at, location_entity_id) "
        "VALUES (?, ?, ?, ?, 'open', ?, ?)",
        (scene_id, campaign_id, session_id, name, started_at, location_entity_id),
    )


def test_scene_carries_lifecycle_columns(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"]: row for row in conn.execute("PRAGMA table_info(scenes)").fetchall()
    }
    for name in (
        "status",
        "location_entity_id",
        "in_world_started_at",
        "in_world_ended_at",
        "started_at",
        "ended_at",
        "system_state",
    ):
        assert name in columns, f"scenes is missing {name!r}"
    assert columns["status"]["notnull"] == 1


def test_scene_status_rejects_unknown_value(conn: sqlite3.Connection) -> None:
    _seed_campaign(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO scenes (scene_id, campaign_id, name, status, started_at) "
            "VALUES ('scene-1', 'campaign-1', 'Bad', 'paused', '2026-09-25T00:00:00Z')",
        )


def test_only_one_open_scene_per_campaign(conn: sqlite3.Connection) -> None:
    _seed_campaign(conn)
    _open_scene(conn, "scene-1")
    with pytest.raises(sqlite3.IntegrityError):
        _open_scene(conn, "scene-2", name="Second")


def test_a_second_closed_scene_is_allowed(conn: sqlite3.Connection) -> None:
    _seed_campaign(conn)
    _open_scene(conn, "scene-1")
    conn.execute(
        "INSERT INTO scenes "
        "(scene_id, campaign_id, name, status, started_at, ended_at) "
        "VALUES ('scene-2', 'campaign-1', 'Earlier', 'closed', "
        "'2026-09-24T00:00:00Z', '2026-09-24T01:00:00Z')",
    )
    rows = conn.execute(
        "SELECT scene_id FROM scenes WHERE campaign_id = 'campaign-1' ORDER BY scene_id"
    ).fetchall()
    assert [row["scene_id"] for row in rows] == ["scene-1", "scene-2"]


def test_closed_scene_cannot_end_before_it_starts(conn: sqlite3.Connection) -> None:
    _seed_campaign(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO scenes "
            "(scene_id, campaign_id, name, status, started_at, ended_at) "
            "VALUES ('scene-1', 'campaign-1', 'Backwards', 'closed', "
            "'2026-09-25T02:00:00Z', '2026-09-25T01:00:00Z')",
        )


def test_scene_location_must_be_an_entity_in_the_same_campaign(
    conn: sqlite3.Connection,
) -> None:
    _seed_campaign(conn, "campaign-1")
    _seed_campaign(conn, "campaign-2")
    _seed_entity(conn, "loc-other", campaign_id="campaign-2")
    with pytest.raises(sqlite3.IntegrityError, match="scenes location mismatch"):
        _open_scene(conn, "scene-1", location_entity_id="loc-other")

    _seed_entity(conn, "loc-hall", campaign_id="campaign-1")
    _open_scene(conn, "scene-2", location_entity_id="loc-hall")
    row = conn.execute(
        "SELECT location_entity_id FROM scenes WHERE scene_id = 'scene-2'"
    ).fetchone()
    assert row["location_entity_id"] == "loc-hall"


def test_deleting_a_location_entity_clears_the_scene_reference(
    conn: sqlite3.Connection,
) -> None:
    _seed_campaign(conn)
    _seed_entity(conn, "loc-hall")
    _open_scene(conn, "scene-1", location_entity_id="loc-hall")
    conn.execute(
        "DELETE FROM entities WHERE entity_id = 'loc-hall' AND campaign_id = 'campaign-1'"
    )
    row = conn.execute(
        "SELECT location_entity_id FROM scenes WHERE scene_id = 'scene-1'"
    ).fetchone()
    assert row["location_entity_id"] is None


def test_scene_session_must_belong_to_the_same_campaign(
    conn: sqlite3.Connection,
) -> None:
    _seed_campaign(conn, "campaign-1")
    _seed_campaign(conn, "campaign-2")
    conn.execute(
        "INSERT INTO sessions (session_id, campaign_id, started_at) "
        "VALUES ('session-2', 'campaign-2', '2026-09-25T00:00:00Z')",
    )
    with pytest.raises(sqlite3.IntegrityError, match="scenes session mismatch"):
        _open_scene(conn, "scene-1", session_id="session-2")

    conn.execute(
        "INSERT INTO sessions (session_id, campaign_id, started_at) "
        "VALUES ('session-1', 'campaign-1', '2026-09-25T00:00:00Z')",
    )
    _open_scene(conn, "scene-2", session_id="session-1")
    row = conn.execute("SELECT session_id FROM scenes WHERE scene_id = 'scene-2'").fetchone()
    assert row["session_id"] == "session-1"


def test_scene_members_allow_only_declared_presence_types(
    conn: sqlite3.Connection,
) -> None:
    _seed_campaign(conn)
    _seed_entity(conn, "pc-ada")
    _open_scene(conn, "scene-1")
    conn.execute(
        "INSERT INTO scene_members (scene_id, entity_id, presence_type, entered_at) "
        "VALUES ('scene-1', 'pc-ada', 'pc', '2026-09-25T00:00:00Z')",
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO scene_members (scene_id, entity_id, presence_type, entered_at) "
            "VALUES ('scene-1', 'pc-ada', 'ally', '2026-09-25T00:00:00Z')",
        )


def test_scene_members_have_no_visibility_column(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(scene_members)").fetchall()
    }
    assert columns == {
        "scene_id",
        "entity_id",
        "presence_type",
        "entered_at",
        "exited_at",
    }


def test_entity_presence_is_unique_per_scene(conn: sqlite3.Connection) -> None:
    _seed_campaign(conn)
    _seed_entity(conn, "pc-ada")
    _open_scene(conn, "scene-1")
    conn.execute(
        "INSERT INTO scene_members (scene_id, entity_id, presence_type, entered_at) "
        "VALUES ('scene-1', 'pc-ada', 'pc', '2026-09-25T00:00:00Z')",
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO scene_members (scene_id, entity_id, presence_type, entered_at) "
            "VALUES ('scene-1', 'pc-ada', 'npc', '2026-09-25T01:00:00Z')",
        )


def test_presence_exit_cannot_precede_entry(conn: sqlite3.Connection) -> None:
    _seed_campaign(conn)
    _seed_entity(conn, "pc-ada")
    _open_scene(conn, "scene-1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO scene_members "
            "(scene_id, entity_id, presence_type, entered_at, exited_at) "
            "VALUES ('scene-1', 'pc-ada', 'pc', "
            "'2026-09-25T02:00:00Z', '2026-09-25T01:00:00Z')",
        )


def test_scene_member_must_be_an_entity_in_the_scene_campaign(
    conn: sqlite3.Connection,
) -> None:
    _seed_campaign(conn, "campaign-1")
    _seed_campaign(conn, "campaign-2")
    _seed_entity(conn, "npc-other", campaign_id="campaign-2")
    _open_scene(conn, "scene-1", campaign_id="campaign-1")
    with pytest.raises(sqlite3.IntegrityError, match="scene_members entity mismatch"):
        conn.execute(
            "INSERT INTO scene_members (scene_id, entity_id, presence_type, entered_at) "
            "VALUES ('scene-1', 'npc-other', 'npc', '2026-09-25T00:00:00Z')",
        )


def test_campaign_clock_allows_exactly_one_row(conn: sqlite3.Connection) -> None:
    _seed_campaign(conn)
    conn.execute(
        "INSERT INTO campaign_clock (campaign_id, in_world_label, updated_at) "
        "VALUES ('campaign-1', 'Day 3, morning', '2026-09-25T00:00:00Z')",
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO campaign_clock (campaign_id, in_world_label, updated_at) "
            "VALUES ('campaign-1', 'Day 4', '2026-09-25T01:00:00Z')",
        )


def test_campaign_clock_rejects_negative_minutes(conn: sqlite3.Connection) -> None:
    _seed_campaign(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO campaign_clock (campaign_id, in_world_minutes, updated_at) "
            "VALUES ('campaign-1', -1, '2026-09-25T00:00:00Z')",
        )


def test_legacy_scene_rows_backfill_status_and_preserve_system_state(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "migrations-before-0021"
    staging.mkdir()
    for path in sorted(_SOURCE_MIGRATIONS.glob("*.sql")):
        if not path.name.startswith("0021_"):
            shutil.copyfile(path, staging / path.name)

    database = tmp_path / "legacy.db"
    conn = connect(database)
    migrate(conn, directory=staging)
    _seed_campaign(conn)
    conn.execute(
        "INSERT INTO scenes "
        "(scene_id, campaign_id, name, opened_at, closed_at, system_state) "
        "VALUES ('scene-closed', 'campaign-1', 'Old Harbour', "
        "'2026-09-01T00:00:00Z', '2026-09-01T02:00:00Z', '{\"round\":3}')",
    )
    conn.execute(
        "INSERT INTO scenes (scene_id, campaign_id, name, opened_at, system_state) "
        "VALUES ('scene-open', 'campaign-1', 'Crossroads', "
        "'2026-09-02T00:00:00Z', '{\"round\":7}')",
    )
    conn.close()

    conn = connect(database)
    applied = migrate(conn)
    assert "0021_scene_lifecycle.sql" in applied

    rows = {
        row["scene_id"]: row
        for row in conn.execute(
            "SELECT scene_id, status, started_at, ended_at, system_state FROM scenes"
        ).fetchall()
    }
    assert rows["scene-closed"]["status"] == "closed"
    assert rows["scene-closed"]["started_at"] == "2026-09-01T00:00:00Z"
    assert rows["scene-closed"]["ended_at"] == "2026-09-01T02:00:00Z"
    assert rows["scene-closed"]["system_state"] == '{"round":3}'
    assert rows["scene-open"]["status"] == "open"
    assert rows["scene-open"]["ended_at"] is None
    assert rows["scene-open"]["system_state"] == '{"round":7}'
    conn.close()


def test_scene_columns_stay_system_agnostic(conn: sqlite3.Connection) -> None:
    for table in ("scenes", "scene_members", "campaign_clock"):
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall():
            for pattern in _FORBIDDEN_COLUMN_PATTERNS:
                assert not pattern.search(row["name"]), (
                    f"{table}.{row['name']} matches {pattern.pattern!r}"
                )
