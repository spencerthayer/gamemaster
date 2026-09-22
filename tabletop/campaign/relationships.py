"""Typed temporal relationship edges stored in SQLite."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Mapping

from tabletop.api.visibility import Viewpoint, VisibilityKind
from tabletop.storage.sqlite import transaction


@dataclass(frozen=True, kw_only=True)
class RelationshipEdge:
    """A directed, typed relationship with an optional validity window."""

    relationship_id: str
    owner_scope: str
    setting_id: str | None
    campaign_id: str | None
    source_id: str
    relationship_type: str
    target_id: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    visibility: str = "GM"
    valid_from: str | None = None
    valid_until: str | None = None


def store_edge(conn: sqlite3.Connection, edge: RelationshipEdge) -> None:
    """Insert one relationship edge."""

    _validate_owner(edge.owner_scope, edge.setting_id, edge.campaign_id)
    with transaction(conn):
        _insert_edge(conn, edge)


def close_edge(
    conn: sqlite3.Connection,
    relationship_id: str,
    *,
    owner_scope: str,
    valid_until: str,
    setting_id: str | None = None,
    campaign_id: str | None = None,
) -> None:
    """Close an edge in its owning scope without creating a replacement."""

    _validate_owner(owner_scope, setting_id, campaign_id)
    owner_column, owner_id = _owner_key(owner_scope, setting_id, campaign_id)
    with transaction(conn):
        cursor = conn.execute(
            "UPDATE relationships SET valid_until = ? "
            f"WHERE relationship_id = ? AND owner_scope = ? AND {owner_column} = ?",
            (valid_until, relationship_id, owner_scope, owner_id),
        )
        if cursor.rowcount != 1:
            raise LookupError(
                f"relationship {relationship_id!r} does not exist in that scope"
            )


def supersede_edge(
    conn: sqlite3.Connection,
    old_edge: RelationshipEdge,
    new_edge: RelationshipEdge,
    *,
    valid_until: str,
) -> None:
    """Atomically close an edge and insert its replacement."""

    _validate_owner(old_edge.owner_scope, old_edge.setting_id, old_edge.campaign_id)
    _validate_owner(new_edge.owner_scope, new_edge.setting_id, new_edge.campaign_id)
    if (
        old_edge.owner_scope,
        old_edge.setting_id,
        old_edge.campaign_id,
    ) != (
        new_edge.owner_scope,
        new_edge.setting_id,
        new_edge.campaign_id,
    ):
        raise ValueError("a replacement edge must have the same owner as the old edge")

    owner_column, owner_id = _owner_key(
        old_edge.owner_scope,
        old_edge.setting_id,
        old_edge.campaign_id,
    )
    with transaction(conn):
        cursor = conn.execute(
            "UPDATE relationships SET valid_until = ? "
            f"WHERE relationship_id = ? AND owner_scope = ? AND {owner_column} = ?",
            (
                valid_until,
                old_edge.relationship_id,
                old_edge.owner_scope,
                owner_id,
            ),
        )
        if cursor.rowcount != 1:
            raise LookupError(
                f"relationship {old_edge.relationship_id!r} does not exist in that scope"
            )
        _insert_edge(conn, new_edge)


def query_edges(
    conn: sqlite3.Connection,
    *,
    owner_scope: str,
    entity_id: str,
    as_of: str,
    viewpoint: Viewpoint,
    setting_id: str | None = None,
    campaign_id: str | None = None,
) -> tuple[RelationshipEdge, ...]:
    """Return visible edges touching an entity and active at a world time."""

    _validate_owner(owner_scope, setting_id, campaign_id)
    owner_column, owner_id = _owner_key(owner_scope, setting_id, campaign_id)
    visibility_clause, visibility_params = _visible_edges_clause(viewpoint)
    rows = conn.execute(
        "SELECT relationship_id, owner_scope, setting_id, campaign_id, source_id, "
        "relationship_type, target_id, metadata, visibility, valid_from, valid_until "
        "FROM relationships "
        f"WHERE owner_scope = ? AND {owner_column} = ? "
        "AND (source_id = ? OR target_id = ?) "
        "AND (valid_from IS NULL OR valid_from <= ?) "
        "AND (valid_until IS NULL OR valid_until > ?) "
        f"AND ({visibility_clause}) "
        "ORDER BY relationship_id",
        (
            owner_scope,
            owner_id,
            entity_id,
            entity_id,
            as_of,
            as_of,
            *visibility_params,
        ),
    ).fetchall()
    return tuple(_edge_from_row(row) for row in rows)


def _insert_edge(conn: sqlite3.Connection, edge: RelationshipEdge) -> None:
    conn.execute(
        "INSERT INTO relationships "
        "(relationship_id, owner_scope, setting_id, campaign_id, source_id, "
        "relationship_type, target_id, metadata, visibility, valid_from, valid_until) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            edge.relationship_id,
            edge.owner_scope,
            edge.setting_id,
            edge.campaign_id,
            edge.source_id,
            edge.relationship_type,
            edge.target_id,
            json.dumps(edge.metadata, separators=(",", ":"), ensure_ascii=False),
            edge.visibility,
            edge.valid_from,
            edge.valid_until,
        ),
    )


def _validate_owner(
    owner_scope: str,
    setting_id: str | None,
    campaign_id: str | None,
) -> None:
    if owner_scope == "setting" and setting_id is not None and campaign_id is None:
        return
    if owner_scope == "campaign" and campaign_id is not None:
        return
    raise ValueError("relationship owner ids do not match owner_scope")


def _owner_key(
    owner_scope: str,
    setting_id: str | None,
    campaign_id: str | None,
) -> tuple[str, str]:
    if owner_scope == "setting":
        assert setting_id is not None
        return "setting_id", setting_id
    assert campaign_id is not None
    return "campaign_id", campaign_id


def _visible_edges_clause(viewpoint: Viewpoint) -> tuple[str, tuple[str, ...]]:
    if viewpoint.scope.kind is VisibilityKind.GM:
        return "1 = 1", ()

    visible_scopes = {"PUBLIC"}
    if viewpoint.party_member:
        visible_scopes.add("PARTY")
    if viewpoint.scope.kind in {
        VisibilityKind.CHARACTER,
        VisibilityKind.NPC,
        VisibilityKind.FACTION,
        VisibilityKind.GROUP,
    }:
        visible_scopes.add(viewpoint.scope.to_string())
    visible_scopes.update(
        f"FACTION:{faction_id}" for faction_id in viewpoint.faction_ids
    )
    visible_scopes.update(f"GROUP:{group_id}" for group_id in viewpoint.group_ids)

    ordered_scopes = tuple(sorted(visible_scopes))
    placeholders = ", ".join("?" for _ in ordered_scopes)
    return f"visibility IN ({placeholders})", ordered_scopes


def resolve_relationship_overlay(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    setting_id: str | None,
    entity_id: str,
    as_of: str,
    viewpoint: Viewpoint,
) -> tuple[RelationshipEdge, ...]:
    """Return campaign edges plus setting edges they do not supersede."""

    campaign_edges = query_edges(
        conn,
        owner_scope="campaign",
        entity_id=entity_id,
        as_of=as_of,
        viewpoint=viewpoint,
        campaign_id=campaign_id,
    )
    if setting_id is None:
        return campaign_edges
    setting_edges = query_edges(
        conn,
        owner_scope="setting",
        entity_id=entity_id,
        as_of=as_of,
        viewpoint=viewpoint,
        setting_id=setting_id,
    )
    covered = {
        (edge.source_id, edge.relationship_type, edge.target_id)
        for edge in campaign_edges
    }
    extras = tuple(
        edge
        for edge in setting_edges
        if (edge.source_id, edge.relationship_type, edge.target_id) not in covered
    )
    return campaign_edges + extras


def _edge_from_row(row: sqlite3.Row) -> RelationshipEdge:
    return RelationshipEdge(
        relationship_id=row["relationship_id"],
        owner_scope=row["owner_scope"],
        setting_id=row["setting_id"],
        campaign_id=row["campaign_id"],
        source_id=row["source_id"],
        relationship_type=row["relationship_type"],
        target_id=row["target_id"],
        metadata=json.loads(row["metadata"]),
        visibility=row["visibility"],
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
    )
