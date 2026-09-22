"""Typed temporal relationship edge tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.api.visibility import Viewpoint, parse_scope
from tabletop.campaign.relationships import (
    RelationshipEdge,
    close_edge,
    query_edges,
    store_edge,
    supersede_edge,
)
from tabletop.storage.sqlite import connect, migrate


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    result = connect(tmp_path / "relationships.db")
    migrate(result)
    result.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("setting-1", "Test Setting", "2026-01-01T00:00:00Z"),
    )
    result.execute(
        "INSERT INTO campaigns "
        "(campaign_id, name, system_id, setting_id, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "campaign-1",
            "Test Campaign",
            "test",
            "setting-1",
            "2026-01-01T00:00:00Z",
        ),
    )
    yield result
    result.close()


def _viewpoint(scope: str) -> Viewpoint:
    return Viewpoint(scope=parse_scope(scope))


def _edge(
    relationship_id: str,
    *,
    owner_scope: str = "setting",
    setting_id: str | None = "setting-1",
    campaign_id: str | None = None,
    relationship_type: str = "opposes",
    visibility: str = "PUBLIC",
    valid_from: str | None = "1492-01-01",
    valid_until: str | None = None,
) -> RelationshipEdge:
    return RelationshipEdge(
        relationship_id=relationship_id,
        owner_scope=owner_scope,
        setting_id=setting_id,
        campaign_id=campaign_id,
        source_id="deity-1",
        relationship_type=relationship_type,
        target_id="faction-1",
        metadata={"source": "canon"},
        visibility=visibility,
        valid_from=valid_from,
        valid_until=valid_until,
    )


def test_stores_setting_edge_with_all_fields(conn: sqlite3.Connection) -> None:
    edge = _edge("relationship-1")

    store_edge(conn, edge)

    assert query_edges(
        conn,
        owner_scope="setting",
        setting_id="setting-1",
        entity_id="deity-1",
        as_of="1500-01-01",
        viewpoint=_viewpoint("GM"),
    ) == (edge,)


def test_campaign_edge_does_not_modify_matching_setting_edge(
    conn: sqlite3.Connection,
) -> None:
    setting_edge = _edge("setting-edge")
    campaign_edge = _edge(
        "campaign-edge",
        owner_scope="campaign",
        campaign_id="campaign-1",
    )
    store_edge(conn, setting_edge)
    store_edge(conn, campaign_edge)

    close_edge(
        conn,
        "campaign-edge",
        owner_scope="campaign",
        campaign_id="campaign-1",
        valid_until="1500-01-01",
    )

    assert query_edges(
        conn,
        owner_scope="setting",
        setting_id="setting-1",
        entity_id="deity-1",
        as_of="1501-01-01",
        viewpoint=_viewpoint("GM"),
    ) == (setting_edge,)


def test_query_as_of_excludes_future_and_closed_edges(
    conn: sqlite3.Connection,
) -> None:
    store_edge(conn, _edge("active"))
    store_edge(conn, _edge("future", valid_from="1600-01-01"))
    store_edge(
        conn,
        _edge(
            "closed",
            relationship_type="feared_by",
            valid_until="1499-01-01",
        ),
    )

    edges = query_edges(
        conn,
        owner_scope="setting",
        setting_id="setting-1",
        entity_id="deity-1",
        as_of="1500-01-01",
        viewpoint=_viewpoint("GM"),
    )

    assert tuple(edge.relationship_id for edge in edges) == ("active",)


def test_close_edge_sets_valid_until_without_inserting(
    conn: sqlite3.Connection,
) -> None:
    store_edge(conn, _edge("ended"))

    close_edge(
        conn,
        "ended",
        owner_scope="setting",
        setting_id="setting-1",
        valid_until="1500-01-01",
    )

    rows = conn.execute(
        "SELECT relationship_id, valid_until FROM relationships"
    ).fetchall()
    assert [(row["relationship_id"], row["valid_until"]) for row in rows] == [
        ("ended", "1500-01-01")
    ]


def test_supersede_edge_closes_old_edge_and_inserts_replacement(
    conn: sqlite3.Connection,
) -> None:
    old_edge = _edge("old", relationship_type="allied_with")
    new_edge = _edge(
        "new",
        relationship_type="opposes",
        valid_from="1500-01-01",
    )
    store_edge(conn, old_edge)

    supersede_edge(conn, old_edge, new_edge, valid_until="1500-01-01")

    rows = conn.execute(
        "SELECT relationship_id, relationship_type, valid_until "
        "FROM relationships ORDER BY relationship_id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("new", "opposes", None),
        ("old", "allied_with", "1500-01-01"),
    ]


def test_player_viewpoint_does_not_receive_gm_only_edges(
    conn: sqlite3.Connection,
) -> None:
    store_edge(conn, _edge("public", visibility="PUBLIC"))
    store_edge(
        conn,
        _edge("secret", relationship_type="controls", visibility="GM"),
    )

    player_edges = query_edges(
        conn,
        owner_scope="setting",
        setting_id="setting-1",
        entity_id="deity-1",
        as_of="1500-01-01",
        viewpoint=_viewpoint("CHARACTER:hero"),
    )
    gm_edges = query_edges(
        conn,
        owner_scope="setting",
        setting_id="setting-1",
        entity_id="deity-1",
        as_of="1500-01-01",
        viewpoint=_viewpoint("GM"),
    )

    assert tuple(edge.relationship_id for edge in player_edges) == ("public",)
    assert {edge.relationship_id for edge in gm_edges} == {"public", "secret"}


def test_query_missing_entity_returns_empty_tuple(
    conn: sqlite3.Connection,
) -> None:
    assert query_edges(
        conn,
        owner_scope="campaign",
        campaign_id="campaign-1",
        entity_id="missing",
        as_of="1500-01-01",
        viewpoint=_viewpoint("GM"),
    ) == ()


def test_query_requires_viewpoint(conn: sqlite3.Connection) -> None:
    with pytest.raises(TypeError, match="viewpoint"):
        query_edges(
            conn,
            owner_scope="setting",
            setting_id="setting-1",
            entity_id="deity-1",
            as_of="1500-01-01",
        )
