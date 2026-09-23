"""Historical notes import staging tests."""

from __future__ import annotations

from pathlib import Path

from tabletop.campaign.store import CampaignStore
from tabletop.importing.interface import ImportBatch, ImportItem
from tabletop.importing.json_adapter import JsonCampaignImporter
from tabletop.importing.notes_adapter import stage_notes
from tabletop.importing.store import (
    ImportStore,
    authoritative_state_digest,
)
from tabletop.storage.sqlite import connect, migrate

_NOTES = Path(__file__).resolve().parent / "fixtures" / "session-notes.md"
_STRUCTURED = (
    Path(__file__).resolve().parent / "fixtures" / "external-campaign.json"
)


def test_historical_notes_stage_without_facts(tmp_path: Path) -> None:
    db = connect(tmp_path / "hist.db")
    migrate(db)
    CampaignStore(db).create_campaign("night", "Night", "freeform")
    before = authoritative_state_digest(db, "night")
    import_id = stage_notes(db, "night", str(_NOTES), import_root=_NOTES.parent)
    after = authoritative_state_digest(db, "night")
    assert before == after
    items = ImportStore(db).list_items(import_id)
    assert items
    assert all(item["review_state"] == "pending_review" for item in items)
    assert db.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
    db.close()


def test_import_staging_has_no_authoritative_side_effects(tmp_path: Path) -> None:
    db = connect(tmp_path / "pure.db")
    migrate(db)
    CampaignStore(db).create_campaign("night", "Night", "freeform")
    before = authoritative_state_digest(db, "night")
    ImportStore(db).stage_batch(
        "night", JsonCampaignImporter().load(str(_STRUCTURED))
    )
    assert authoritative_state_digest(db, "night") == before
    stage_notes(db, "night", str(_NOTES), import_root=_NOTES.parent)
    assert authoritative_state_digest(db, "night") == before
    conflict = ImportBatch(
        format_id="gamemaster-notes/v1",
        source_label="conflict",
        items=(
            ImportItem(
                kind="fact",
                payload={
                    "fact_id": "c1",
                    "subject_id": "ada",
                    "predicate": "status",
                    "value": "dead",
                },
                provenance={},
                proposed_key="c1",
                review={
                    "conflicts": [{"type": "staged_vs_canon", "keys": ["c1"]}]
                },
            ),
        ),
    )
    ImportStore(db).stage_batch("night", conflict)
    assert authoritative_state_digest(db, "night") == before
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    db.close()


def test_path_outside_import_root_rejected(tmp_path: Path) -> None:
    db = connect(tmp_path / "root.db")
    migrate(db)
    CampaignStore(db).create_campaign("night", "Night", "freeform")
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    try:
        stage_notes(db, "night", str(outside), import_root=tmp_path / "allowed")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "outside import root" in str(exc)
    db.close()
