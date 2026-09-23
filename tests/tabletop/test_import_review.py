"""Import review, apply idempotency, and resume tests."""

from __future__ import annotations

import json
from pathlib import Path

from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.resume import resume_snapshot
from tabletop.campaign.store import CampaignStore
from tabletop.importing.apply import ApplyBlockedError, apply_item, review_item
from tabletop.importing.interface import ImportBatch, ImportItem
from tabletop.importing.json_adapter import JsonCampaignImporter
from tabletop.importing.store import (
    ImportStore,
    authoritative_state_digest,
    derived_batch_status,
    import_status_report,
)
from tabletop.storage.sqlite import connect, migrate, transaction

_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "external-campaign.json"
)


def _stage_structured(db):
    CampaignStore(db).create_campaign("night", "Night", "freeform")
    MembershipStore(db).add_participant("night", "gm1", "GM", "gm")
    # Entities must exist before fact subjects are valid for conflict checks.
    import_id = ImportStore(db).stage_batch(
        "night", JsonCampaignImporter().load(str(_FIXTURE))
    )
    return import_id, ImportStore(db).list_items(import_id)


def test_import_status_splits_staging_and_campaign(tmp_path: Path) -> None:
    db = connect(tmp_path / "status.db")
    migrate(db)
    import_id, _ = _stage_structured(db)
    report = import_status_report(db, import_id)
    assert "staging" in report and "campaign" in report
    assert "conflicts" in report["staging"]
    assert "unresolved_contradictions" in report["campaign"]
    db.close()


def test_apply_double_import_is_single_fact(tmp_path: Path) -> None:
    db = connect(tmp_path / "idemp.db")
    migrate(db)
    _, items = _stage_structured(db)
    for item in items:
        if item["kind"] == "entity":
            apply_item(db, item["item_id"])
    fact = next(item for item in items if item["kind"] == "fact")
    first = apply_item(db, fact["item_id"])
    second = apply_item(db, fact["item_id"])
    assert first["status"] == "applied"
    assert second["status"] == "already_applied"
    count = db.execute(
        "SELECT COUNT(*) FROM facts WHERE fact_id = ?",
        (first["applied_target_id"],),
    ).fetchone()[0]
    assert count == 1
    promoted = db.execute(
        "SELECT COUNT(*) FROM events WHERE event_type = 'fact.promoted' "
        "AND payload LIKE ?",
        (f'%{first["applied_target_id"]}%',),
    ).fetchone()[0]
    assert promoted == 1
    db.close()


def test_apply_rejected_item_refused(tmp_path: Path) -> None:
    db = connect(tmp_path / "rej.db")
    migrate(db)
    _, items = _stage_structured(db)
    fact = next(item for item in items if item["kind"] == "fact")
    review_item(db, fact["item_id"], reject=True)
    before = authoritative_state_digest(db, "night")
    try:
        apply_item(db, fact["item_id"])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert authoritative_state_digest(db, "night") == before
    db.close()


def test_review_reject_is_single_judgment(tmp_path: Path) -> None:
    db = connect(tmp_path / "rej2.db")
    migrate(db)
    _, items = _stage_structured(db)
    fact = next(item for item in items if item["kind"] == "fact")
    review_item(db, fact["item_id"], reject=True)
    review_item(db, fact["item_id"], reject=True)
    row = db.execute(
        "SELECT review_state FROM import_items WHERE item_id = ?",
        (fact["item_id"],),
    ).fetchone()
    assert row["review_state"] == "rejected"
    db.close()


def test_failed_apply_metadata_rerun_converges(tmp_path: Path) -> None:
    db = connect(tmp_path / "fail.db")
    migrate(db)
    _, items = _stage_structured(db)
    for item in items:
        if item["kind"] == "entity":
            apply_item(db, item["item_id"])
    fact = next(item for item in items if item["kind"] == "fact")
    # Force failure by using an unsupported kind via direct corruption.
    with transaction(db):
        db.execute(
            "UPDATE import_items SET kind = 'nope' WHERE item_id = ?",
            (fact["item_id"],),
        )
    try:
        apply_item(db, fact["item_id"])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    row = db.execute(
        "SELECT review_state, error_json FROM import_items WHERE item_id = ?",
        (fact["item_id"],),
    ).fetchone()
    assert row["review_state"] == "pending_review"
    assert json.loads(row["error_json"]).get("code") == "apply_failed"
    with transaction(db):
        db.execute(
            "UPDATE import_items SET kind = 'fact' WHERE item_id = ?",
            (fact["item_id"],),
        )
    result = apply_item(db, fact["item_id"])
    assert result["status"] == "applied"
    db.close()


def test_resume_report_is_read_only(tmp_path: Path) -> None:
    db = connect(tmp_path / "resume.db")
    migrate(db)
    _stage_structured(db)
    before = authoritative_state_digest(db, "night")
    snap = resume_snapshot(db, "night")
    assert snap["in_world_date"] == "unknown"
    assert snap["scene"] == "unknown"
    assert "authoritative_contradictions" in snap
    assert "pending_imports" in snap
    assert "pending_import_conflicts" in snap
    resume_snapshot(db, "night")
    assert authoritative_state_digest(db, "night") == before
    db.close()


def test_batch_complete_when_only_rejected(tmp_path: Path) -> None:
    db = connect(tmp_path / "complete.db")
    migrate(db)
    import_id, items = _stage_structured(db)
    for item in items:
        review_item(db, item["item_id"], reject=True)
    assert derived_batch_status(db, import_id) == "complete"
    db.close()


def test_apply_blocked_on_canon_conflict(tmp_path: Path) -> None:
    db = connect(tmp_path / "block.db")
    migrate(db)
    CampaignStore(db).create_campaign("night", "Night", "freeform")
    MembershipStore(db).add_participant("night", "gm1", "GM", "gm")
    CampaignStore(db).upsert_entity("night", "ada", "Ada", entity_type="character")
    # Seed confirmed canon via apply path first.
    seed = ImportBatch(
        format_id="gamemaster-external/v1",
        source_label="seed",
        items=(
            ImportItem(
                kind="fact",
                payload={
                    "fact_id": "alive",
                    "subject_id": "ada",
                    "predicate": "status",
                    "value": "alive",
                },
                provenance={"source_path": "seed"},
                proposed_key="alive",
            ),
        ),
    )
    seed_id = ImportStore(db).stage_batch("night", seed)
    seed_item = ImportStore(db).list_items(seed_id)[0]
    apply_item(db, seed_item["item_id"])
    conflict = ImportBatch(
        format_id="gamemaster-external/v1",
        source_label="conflict",
        items=(
            ImportItem(
                kind="fact",
                payload={
                    "fact_id": "dead",
                    "subject_id": "ada",
                    "predicate": "status",
                    "value": "dead",
                },
                provenance={"source_path": "conflict"},
                proposed_key="dead",
            ),
        ),
    )
    cid = ImportStore(db).stage_batch("night", conflict)
    item = ImportStore(db).list_items(cid)[0]
    events_before = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    try:
        apply_item(db, item["item_id"])
        raise AssertionError("expected ApplyBlockedError")
    except ApplyBlockedError:
        pass
    row = db.execute(
        "SELECT review_state FROM import_items WHERE item_id = ?",
        (item["item_id"],),
    ).fetchone()
    assert row["review_state"] == "pending_review"
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events_before
    assert db.execute(
        "SELECT COUNT(*) FROM facts WHERE fact_id = 'dead'"
    ).fetchone()[0] == 0
    db.close()
