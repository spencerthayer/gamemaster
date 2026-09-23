"""Structured external campaign import staging tests."""

from __future__ import annotations

import json
from pathlib import Path

from tabletop.campaign.store import CampaignStore
from tabletop.importing.json_adapter import JsonCampaignImporter
from tabletop.importing.store import (
    ImportStore,
    authoritative_state_digest,
    derived_batch_status,
)
from tabletop.storage.sqlite import connect, migrate

_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "external-campaign.json"
)


def test_structured_import_stages_without_authoritative_rows(tmp_path: Path) -> None:
    db = connect(tmp_path / "imp.db")
    migrate(db)
    CampaignStore(db).create_campaign("night", "Night", "freeform")
    before = authoritative_state_digest(db, "night")
    batch = JsonCampaignImporter().load(str(_FIXTURE))
    import_id = ImportStore(db).stage_batch("night", batch)
    after = authoritative_state_digest(db, "night")
    assert before == after
    items = ImportStore(db).list_items(import_id)
    assert len(items) == 7
    assert all(item["review_state"] == "pending_review" for item in items)
    assert derived_batch_status(db, import_id) == "pending_review"
    assert db.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
    ada = next(
        item
        for item in items
        if item["kind"] == "entity"
        and json.loads(item["payload_json"])["entity_id"] == "ada"
    )
    assert json.loads(ada["payload_json"])["system_state"]["level"] == 3
    db.close()
