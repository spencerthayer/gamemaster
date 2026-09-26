"""Installation is not activation, and one document can serve many campaigns."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate

ROLES = ("rules", "setting", "adventure", "character", "notes", "reference")

_CAMPAIGN = "campaign-1"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "catalog.db")
    migrate(connection)
    store = CampaignStore(connection)
    store.create_campaign(_CAMPAIGN, "First", "freeform")
    store.create_campaign("campaign-2", "Second", "freeform")
    connection.execute(
        "INSERT INTO documents (document_id, content_hash, source_path, title, "
        "document_shape, system_id, visibility, ingested_at) "
        "VALUES ('doc-1', 'hash-1', 'rules.md', 'Rules', 'prose', 'freeform', "
        "'GM', '2026-09-25T00:00:00Z')",
    )
    yield connection
    connection.close()


def _pack(conn: sqlite3.Connection, pack_id: str = "pack-1") -> None:
    conn.execute(
        "INSERT INTO installed_packs "
        "(pack_id, name, pack_type, system_id, version, manifest_hash, source_path, "
        "installed_at) VALUES (?, ?, 'rules', 'freeform', '1.0.0', ?, ?, "
        "'2026-09-25T00:00:00Z')",
        (pack_id, f"Pack {pack_id}", f"manifest-{pack_id}", f"packs/{pack_id}"),
    )


def _attach_doc(
    conn: sqlite3.Connection, campaign_id: str, role: str = "rules", **overrides: object
) -> None:
    conn.execute(
        "INSERT INTO campaign_documents "
        "(campaign_id, document_id, role, enabled, gm_only, attached_at) "
        "VALUES (?, ?, ?, ?, ?, '2026-09-25T00:00:00Z')",
        (
            campaign_id,
            str(overrides.get("document_id", "doc-1")),
            role,
            int(bool(overrides.get("enabled", True))),
            int(bool(overrides.get("gm_only", False))),
        ),
    )


# -- packs ------------------------------------------------------------------


def test_a_pack_is_installed_with_a_manifest_hash(conn: sqlite3.Connection) -> None:
    _pack(conn)
    row = conn.execute(
        "SELECT pack_type, manifest_hash, system_id FROM installed_packs "
        "WHERE pack_id = 'pack-1'"
    ).fetchone()
    assert row["pack_type"] == "rules"
    assert row["manifest_hash"] == "manifest-pack-1"


def test_the_same_manifest_cannot_be_installed_twice(conn: sqlite3.Connection) -> None:
    """A re-ingest of unchanged material is a duplicate, not a second install."""
    _pack(conn, "pack-1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO installed_packs "
            "(pack_id, name, pack_type, system_id, version, manifest_hash, "
            "source_path, installed_at) "
            "VALUES ('pack-1-copy', 'Copy', 'rules', 'freeform', '1.0.0', "
            "'manifest-pack-1', 'packs/copy', '2026-09-25T00:00:00Z')",
        )


def test_a_changed_manifest_installs_as_a_new_pack(conn: sqlite3.Connection) -> None:
    _pack(conn, "pack-1")
    conn.execute(
        "INSERT INTO installed_packs "
        "(pack_id, name, pack_type, system_id, version, manifest_hash, source_path, "
        "installed_at) VALUES ('pack-2', 'Pack 2', 'rules', 'freeform', '1.0.1', "
        "'manifest-pack-2', 'packs/pack-2', '2026-09-25T00:00:00Z')",
    )
    assert conn.execute("SELECT COUNT(*) FROM installed_packs").fetchone()[0] == 2


def test_an_unknown_pack_type_is_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO installed_packs "
            "(pack_id, name, pack_type, manifest_hash, source_path, installed_at) "
            "VALUES ('pack-1', 'P', 'lore', 'h', 'p', '2026-09-25T00:00:00Z')",
        )


def test_a_pack_document_carries_a_semantic_role(conn: sqlite3.Connection) -> None:
    _pack(conn)
    conn.execute(
        "INSERT INTO installed_pack_documents (pack_id, document_id, ordinal, role) "
        "VALUES ('pack-1', 'doc-1', 0, 'rules')",
    )
    row = conn.execute(
        "SELECT role FROM installed_pack_documents WHERE pack_id = 'pack-1'"
    ).fetchone()
    assert row["role"] == "rules"


# -- attachment -------------------------------------------------------------


def test_attaching_a_document_needs_an_installed_document(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO campaign_documents "
            "(campaign_id, document_id, role, attached_at) "
            "VALUES (?, 'ghost', 'rules', '2026-09-25T00:00:00Z')",
            (_CAMPAIGN,),
        )


def test_attaching_a_pack_needs_an_installed_pack(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO campaign_content_packs (campaign_id, pack_id, role, attached_at) "
            "VALUES (?, 'ghost-pack', 'rules', '2026-09-25T00:00:00Z')",
            (_CAMPAIGN,),
        )


def test_attaching_rejects_an_unknown_campaign(conn: sqlite3.Connection) -> None:
    _pack(conn)
    with pytest.raises(sqlite3.IntegrityError, match="missing campaign"):
        conn.execute(
            "INSERT INTO campaign_content_packs (campaign_id, pack_id, role, attached_at) "
            "VALUES ('ghost-campaign', 'pack-1', 'rules', '2026-09-25T00:00:00Z')",
        )


@pytest.mark.parametrize("role", ROLES)
def test_every_semantic_role_is_accepted(conn: sqlite3.Connection, role: str) -> None:
    _attach_doc(conn, _CAMPAIGN, role=role)
    row = conn.execute(
        "SELECT role FROM campaign_documents WHERE campaign_id = ?", (_CAMPAIGN,)
    ).fetchone()
    assert row["role"] == role


def test_an_unknown_role_is_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _attach_doc(conn, _CAMPAIGN, role="lore")


def test_a_document_cannot_be_attached_twice_to_one_campaign(
    conn: sqlite3.Connection,
) -> None:
    _attach_doc(conn, _CAMPAIGN)
    with pytest.raises(sqlite3.IntegrityError):
        _attach_doc(conn, _CAMPAIGN, role="notes")


# -- sharing ----------------------------------------------------------------


def test_one_document_can_serve_several_campaigns(conn: sqlite3.Connection) -> None:
    """The bytes are shared; only the attachment is per-campaign."""
    _attach_doc(conn, _CAMPAIGN, role="rules")
    _attach_doc(conn, "campaign-2", role="notes")
    rows = conn.execute(
        "SELECT campaign_id, role FROM campaign_documents WHERE document_id = 'doc-1' "
        "ORDER BY campaign_id"
    ).fetchall()
    assert [(r["campaign_id"], r["role"]) for r in rows] == [
        (_CAMPAIGN, "rules"),
        ("campaign-2", "notes"),
    ]
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


def test_a_disabled_attachment_is_stored_but_inactive(
    conn: sqlite3.Connection,
) -> None:
    _attach_doc(conn, _CAMPAIGN, enabled=False)
    row = conn.execute(
        "SELECT enabled FROM campaign_documents WHERE campaign_id = ?", (_CAMPAIGN,)
    ).fetchone()
    assert row["enabled"] == 0


def test_gm_only_is_per_attachment_not_per_document(
    conn: sqlite3.Connection,
) -> None:
    _attach_doc(conn, _CAMPAIGN, gm_only=True)
    _attach_doc(conn, "campaign-2", gm_only=False)
    flags = dict(
        conn.execute(
            "SELECT campaign_id, gm_only FROM campaign_documents WHERE document_id = 'doc-1'"
        ).fetchall()
    )
    assert flags[_CAMPAIGN] == 1
    assert flags["campaign-2"] == 0


def test_detaching_keeps_the_installed_document(conn: sqlite3.Connection) -> None:
    _attach_doc(conn, _CAMPAIGN)
    conn.execute("DELETE FROM campaign_documents WHERE campaign_id = ?", (_CAMPAIGN,))
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


def test_installing_a_pack_does_not_attach_it_to_a_campaign(
    conn: sqlite3.Connection,
) -> None:
    """Installation is global. Activation is a separate, explicit step."""
    _pack(conn)
    rows = conn.execute("SELECT COUNT(*) FROM campaign_content_packs").fetchall()
    assert rows[0][0] == 0


def test_deleting_a_campaign_removes_its_attachments(conn: sqlite3.Connection) -> None:
    _pack(conn)
    _attach_doc(conn, _CAMPAIGN)
    conn.execute(
        "INSERT INTO campaign_content_packs (campaign_id, pack_id, role, attached_at) "
        "VALUES (?, 'pack-1', 'rules', '2026-09-25T00:00:00Z')",
        (_CAMPAIGN,),
    )
    conn.execute("DELETE FROM campaigns WHERE campaign_id = ?", (_CAMPAIGN,))
    assert conn.execute("SELECT COUNT(*) FROM campaign_documents").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM campaign_content_packs").fetchone()[0] == 0
    # The installed pack and document survive.
    assert conn.execute("SELECT COUNT(*) FROM installed_packs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


# -- the store --------------------------------------------------------------


def test_installing_a_pack_does_not_attach_it(conn: sqlite3.Connection) -> None:
    from tabletop.documents.catalog import ContentCatalog

    catalog = ContentCatalog(conn)
    catalog.install_pack("pack-1", "Core", "rules", "hash-1", "packs/core")
    assert catalog.require_pack("pack-1").manifest_hash == "hash-1"
    assert catalog.attached_documents(_CAMPAIGN) == []


def test_attaching_makes_a_document_visible(conn: sqlite3.Connection) -> None:
    from tabletop.documents.catalog import ContentCatalog

    catalog = ContentCatalog(conn)
    catalog.attach_document(_CAMPAIGN, "doc-1", "rules")
    assert [d["document_id"] for d in catalog.attached_documents(_CAMPAIGN)] == ["doc-1"]


def test_a_disabled_attachment_is_not_returned(conn: sqlite3.Connection) -> None:
    from tabletop.documents.catalog import ContentCatalog

    catalog = ContentCatalog(conn)
    catalog.attach_document(_CAMPAIGN, "doc-1", "rules", enabled=False)
    assert catalog.attached_documents(_CAMPAIGN) == []
    assert len(catalog.attached_documents(_CAMPAIGN, enabled_only=False)) == 1


def test_gm_only_content_is_hidden_from_a_player(conn: sqlite3.Connection) -> None:
    from tabletop.documents.catalog import ContentCatalog

    catalog = ContentCatalog(conn)
    catalog.attach_document(_CAMPAIGN, "doc-1", "notes", gm_only=True)
    assert catalog.player_visible_documents(_CAMPAIGN, viewpoint_is_gm=True) != []
    assert catalog.player_visible_documents(_CAMPAIGN, viewpoint_is_gm=False) == []


def test_authoritative_documents_exclude_notes(conn: sqlite3.Connection) -> None:
    from tabletop.documents.catalog import ContentCatalog

    catalog = ContentCatalog(conn)
    catalog.attach_document(_CAMPAIGN, "doc-1", "notes")
    assert catalog.authoritative_documents(_CAMPAIGN) == []


def test_an_unknown_role_is_refused(conn: sqlite3.Connection) -> None:
    from tabletop.documents.catalog import CatalogError, ContentCatalog

    with pytest.raises(CatalogError, match="unknown content role"):
        ContentCatalog(conn).attach_document(_CAMPAIGN, "doc-1", "lore")


def test_requiring_an_uninstalled_pack_raises(conn: sqlite3.Connection) -> None:
    from tabletop.documents.catalog import CatalogError, ContentCatalog

    with pytest.raises(CatalogError, match="not installed"):
        ContentCatalog(conn).require_pack("ghost")


def test_detaching_keeps_the_bytes(conn: sqlite3.Connection) -> None:
    from tabletop.documents.catalog import ContentCatalog

    catalog = ContentCatalog(conn)
    catalog.attach_document(_CAMPAIGN, "doc-1", "rules")
    catalog.detach_document(_CAMPAIGN, "doc-1")
    assert catalog.attached_documents(_CAMPAIGN) == []
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
