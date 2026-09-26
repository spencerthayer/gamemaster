"""Attaching activates content for one campaign; installing does not."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.campaign.store import CampaignStore
from tabletop.documents.catalog import CatalogError, ContentCatalog
from tabletop.documents.content_install import install_document, install_pack
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "campaign-1"
_OTHER = "campaign-2"

_PACK_MANIFEST = "id: core-rules\nname: Core Rules\npack_type: rules\nsystem_id: freeform\nversion: 1.0.0\n"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "attach.db")
    migrate(connection)
    store = CampaignStore(connection)
    store.create_campaign(_CAMPAIGN, "First", "freeform")
    store.create_campaign(_OTHER, "Second", "freeform")
    pack = tmp_path / "core-rules"
    pack.mkdir()
    (pack / "content-pack.yaml").write_text(_PACK_MANIFEST, encoding="utf-8")
    (pack / "gates.md").write_text("Gate DC is 15.\n\nRusted shut.", encoding="utf-8")
    install_pack(connection, pack)
    yield connection
    connection.close()


def test_installing_does_not_attach(conn: sqlite3.Connection) -> None:
    assert ContentCatalog(conn).attached_documents(_CAMPAIGN) == []
    assert conn.execute("SELECT COUNT(*) FROM campaign_content_packs").fetchone()[0] == 0


def test_attaching_a_pack_activates_it_for_one_campaign(
    conn: sqlite3.Connection,
) -> None:
    catalog = ContentCatalog(conn)
    catalog.attach_pack(_CAMPAIGN, "core-rules", "rules")
    assert conn.execute(
        "SELECT COUNT(*) FROM campaign_content_packs WHERE campaign_id = ?",
        (_CAMPAIGN,),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM campaign_content_packs WHERE campaign_id = ?",
        (_OTHER,),
    ).fetchone()[0] == 0


def test_attaching_an_uninstalled_pack_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        ContentCatalog(conn).attach_pack(_CAMPAIGN, "ghost-pack", "rules")


def test_attaching_twice_is_refused(conn: sqlite3.Connection) -> None:
    catalog = ContentCatalog(conn)
    catalog.attach_pack(_CAMPAIGN, "core-rules", "rules")
    with pytest.raises(sqlite3.IntegrityError):
        catalog.attach_pack(_CAMPAIGN, "core-rules", "rules")


def test_a_disabled_pack_is_stored_but_inactive(conn: sqlite3.Connection) -> None:
    catalog = ContentCatalog(conn)
    catalog.attach_pack(_CAMPAIGN, "core-rules", "rules", enabled=False)
    row = conn.execute(
        "SELECT enabled FROM campaign_content_packs WHERE campaign_id = ?",
        (_CAMPAIGN,),
    ).fetchone()
    assert row["enabled"] == 0


def test_detaching_a_pack_keeps_it_installed(conn: sqlite3.Connection) -> None:
    catalog = ContentCatalog(conn)
    catalog.attach_pack(_CAMPAIGN, "core-rules", "rules")
    catalog.detach_pack(_CAMPAIGN, "core-rules")
    assert conn.execute("SELECT COUNT(*) FROM installed_packs").fetchone()[0] == 1


def test_attaching_a_document_records_its_role(conn: sqlite3.Connection) -> None:
    document_id = conn.execute(
        "SELECT document_id FROM documents LIMIT 1"
    ).fetchone()["document_id"]
    ContentCatalog(conn).attach_document(_CAMPAIGN, document_id, "rules")
    assert [
        d["role"] for d in ContentCatalog(conn).attached_documents(_CAMPAIGN)
    ] == ["rules"]


def test_one_document_can_hold_different_roles_per_campaign(
    conn: sqlite3.Connection,
) -> None:
    document_id = conn.execute(
        "SELECT document_id FROM documents LIMIT 1"
    ).fetchone()["document_id"]
    catalog = ContentCatalog(conn)
    catalog.attach_document(_CAMPAIGN, document_id, "rules")
    catalog.attach_document(_OTHER, document_id, "notes")
    assert catalog.authoritative_documents(_CAMPAIGN)
    assert catalog.authoritative_documents(_OTHER) == []


def test_an_unknown_role_is_refused(conn: sqlite3.Connection) -> None:
    document_id = conn.execute(
        "SELECT document_id FROM documents LIMIT 1"
    ).fetchone()["document_id"]
    with pytest.raises(CatalogError, match="unknown content role"):
        ContentCatalog(conn).attach_document(_CAMPAIGN, document_id, "lore")


def test_gm_only_is_per_campaign(conn: sqlite3.Connection) -> None:
    document_id = conn.execute(
        "SELECT document_id FROM documents LIMIT 1"
    ).fetchone()["document_id"]
    catalog = ContentCatalog(conn)
    catalog.attach_document(_CAMPAIGN, document_id, "notes", gm_only=True)
    catalog.attach_document(_OTHER, document_id, "notes", gm_only=False)
    assert catalog.player_visible_documents(_CAMPAIGN, viewpoint_is_gm=False) == []
    assert catalog.player_visible_documents(_OTHER, viewpoint_is_gm=False) != []


def test_setup_activation_attaches_content(tmp_path: Path) -> None:
    """A setup manifest's content section activates what it names."""
    from tabletop.campaign.setup import (
        SetupContent,
        apply_setup,
        load_setup_manifest,
    )

    conn = connect(tmp_path / "setup.db")
    try:
        migrate(conn)
        base = tmp_path / "setup"
        base.mkdir()
        (base / "rules.md").write_text("Gate DC is 15.", encoding="utf-8")
        install_document(conn, base / "rules.md")
        (base / "campaign.setup.yaml").write_text(
            "campaign_id: setup-demo\nname: Setup Demo\nsystem_id: freeform\n"
            "content:\n  - path: rules.md\n    role: rules\n",
            encoding="utf-8",
        )
        manifest = load_setup_manifest(base / "campaign.setup.yaml")
        assert manifest.content[0].role == "rules"
        apply_setup(conn, manifest)
        # Setup declares the content, so it installs and activates it. Content
        # setup declared but never installed was a silent configuration loss.
        catalog = ContentCatalog(conn)
        assert [item["role"] for item in catalog.attached_documents("setup-demo")] == [
            "rules"
        ]
        assert conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0] >= 1
    finally:
        conn.close()
