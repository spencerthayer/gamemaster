"""Installed content and campaign attachment.

Two operations that must not be confused: installing registers global bytes,
attaching activates them for one campaign. Installing never changes what a
campaign can see, and attaching never copies anything.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from tabletop.storage.sqlite import transaction

#: Semantic roles, highest authority first. Order matters: a query walks it to
#: pick an authority, and a score may only break ties inside one tier.
CONTENT_ROLES = ("rules", "setting", "adventure", "character", "notes", "reference")

#: Roles that may answer a mechanics question. Everything else is context.
MECHANICS_ROLES = frozenset({"rules", "setting", "adventure"})


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class CatalogError(RuntimeError):
    """A catalog operation was refused."""


@dataclass(frozen=True)
class InstalledPack:
    pack_id: str
    name: str
    pack_type: str
    manifest_hash: str
    source_path: str
    system_id: str | None = None
    version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "name": self.name,
            "pack_type": self.pack_type,
            "system_id": self.system_id,
            "version": self.version,
            "manifest_hash": self.manifest_hash,
            "source_path": self.source_path,
        }


class ContentCatalog:
    """Install global content and attach it to campaigns."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def install_pack(
        self,
        pack_id: str,
        name: str,
        pack_type: str,
        manifest_hash: str,
        source_path: str,
        *,
        system_id: str | None = None,
        version: str | None = None,
    ) -> InstalledPack:
        """Register a pack's bytes. This does not attach it to any campaign."""
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO installed_packs "
                "(pack_id, name, pack_type, system_id, version, manifest_hash, "
                "source_path, installed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    pack_id,
                    name,
                    pack_type,
                    system_id,
                    version,
                    manifest_hash,
                    source_path,
                    _now(),
                ),
            )
        return self.require_pack(pack_id)

    def require_pack(self, pack_id: str) -> InstalledPack:
        row = self.conn.execute(
            "SELECT * FROM installed_packs WHERE pack_id = ?", (pack_id,)
        ).fetchone()
        if row is None:
            raise CatalogError(f"pack is not installed: {pack_id}")
        return _pack_from_row(row)

    def list_packs(self) -> tuple[InstalledPack, ...]:
        rows = self.conn.execute(
            "SELECT * FROM installed_packs ORDER BY pack_id"
        ).fetchall()
        return tuple(_pack_from_row(row) for row in rows)

    def attach_document(
        self,
        campaign_id: str,
        document_id: str,
        role: str,
        *,
        gm_only: bool = False,
        enabled: bool = True,
    ) -> None:
        """Activate one document for one campaign. Nothing is copied."""
        _require_role(role)
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO campaign_documents "
                "(campaign_id, document_id, role, enabled, gm_only, attached_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (campaign_id, document_id, role, int(enabled), int(gm_only), _now()),
            )

    def attach_pack(
        self, campaign_id: str, pack_id: str, role: str, *, enabled: bool = True
    ) -> None:
        _require_role(role)
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO campaign_content_packs "
                "(campaign_id, pack_id, role, enabled, attached_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (campaign_id, pack_id, role, int(enabled), _now()),
            )

    def detach_document(self, campaign_id: str, document_id: str) -> None:
        """Stop using a document. The installed bytes stay."""
        with transaction(self.conn):
            self.conn.execute(
                "DELETE FROM campaign_documents WHERE campaign_id = ? AND document_id = ?",
                (campaign_id, document_id),
            )

    def detach_pack(self, campaign_id: str, pack_id: str) -> None:
        with transaction(self.conn):
            self.conn.execute(
                "DELETE FROM campaign_content_packs WHERE campaign_id = ? AND pack_id = ?",
                (campaign_id, pack_id),
            )

    def attached_documents(
        self, campaign_id: str, *, enabled_only: bool = True
    ) -> list[dict[str, Any]]:
        """Every document this campaign may use, with its authority tier."""
        sql = (
            "SELECT d.document_id, d.title, d.source_path, d.content_hash, "
            "c.role, c.gm_only FROM campaign_documents c "
            "JOIN documents d ON d.document_id = c.document_id "
            "WHERE c.campaign_id = ?"
        )
        if enabled_only:
            sql += " AND c.enabled = 1"
        sql += " ORDER BY c.role, d.document_id"
        return [dict(row) for row in self.conn.execute(sql, (campaign_id,)).fetchall()]

    def authoritative_documents(self, campaign_id: str) -> list[dict[str, Any]]:
        """Documents that may answer a rules question, highest tier first."""
        mechanics = [
            item
            for item in self.attached_documents(campaign_id)
            if item["role"] in MECHANICS_ROLES
        ]
        order = {role: index for index, role in enumerate(CONTENT_ROLES)}
        return sorted(mechanics, key=lambda item: (order[item["role"]], item["document_id"]))

    def player_visible_documents(
        self, campaign_id: str, *, viewpoint_is_gm: bool
    ) -> list[dict[str, Any]]:
        """Documents a viewer may see. A GM-only attachment is not one."""
        return [
            item
            for item in self.attached_documents(campaign_id)
            if viewpoint_is_gm or not item["gm_only"]
        ]


def _require_role(role: str) -> None:
    if role not in CONTENT_ROLES:
        raise CatalogError(
            f"unknown content role {role!r}; choose one of {', '.join(CONTENT_ROLES)}"
        )


def _pack_from_row(row: sqlite3.Row) -> InstalledPack:
    return InstalledPack(
        pack_id=str(row["pack_id"]),
        name=str(row["name"]),
        pack_type=str(row["pack_type"]),
        manifest_hash=str(row["manifest_hash"]),
        source_path=str(row["source_path"]),
        system_id=row["system_id"],
        version=row["version"],
    )


def authority_aware_search(
    conn,
    campaign_id: str,
    query: str,
    *,
    viewpoint_is_gm: bool = True,
    search: Callable[[str], list[Any]] | None = None,
) -> dict[str, Any] | None:
    """Answer a query using the campaign's attached content, by authority.

    ``search`` returns scored chunks for a query, normally an FTS lookup. The
    winner is chosen by semantic role first and score second, so a matching
    campaign note cannot outrank an attached rules document. Candidates the
    viewpoint may not see are dropped before ranking, not after.
    """
    from tabletop.retrieval.precedence import select_by_authority

    catalog = ContentCatalog(conn)
    attached = catalog.player_visible_documents(
        campaign_id, viewpoint_is_gm=viewpoint_is_gm
    )
    roles = {item["document_id"]: item["role"] for item in attached}
    if search is None:
        return None
    candidates = [
        chunk
        for chunk in search(query)
        if getattr(getattr(chunk, "source", None), "document_id", None) in roles
    ]
    result = select_by_authority(candidates, roles=roles)
    if result is None:
        return None
    return {
        "document_id": result.answer.source.document_id,
        "role": roles[result.answer.source.document_id],
        "tier": result.tier,
        "text": result.answer.text,
        "score": result.answer.score,
        "conflicts": [
            {
                "document_id": conflict.conflicting.source.document_id,
                "tier": conflict.conflicting_tier,
            }
            for conflict in result.conflicts
        ],
    }
