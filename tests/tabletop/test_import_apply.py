"""Import apply and review tests."""

from __future__ import annotations

from pathlib import Path

from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.store import CampaignStore
from tabletop.importing.apply import ApplyBlockedError, apply_item, review_item
from tabletop.importing.json_adapter import JsonCampaignImporter
from tabletop.importing.store import ImportStore, derived_batch_status
from tabletop.storage.sqlite import connect, migrate

_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "external-campaign.json"
)


def _stage(db) -> tuple[str, list[dict]]:
    CampaignStore(db).create_campaign("night", "Night", "freeform")
    MembershipStore(db).add_participant("night", "gm1", "GM", "gm")
    import_id = ImportStore(db).stage_batch(
        "night", JsonCampaignImporter().load(str(_FIXTURE))
    )
    return import_id, ImportStore(db).list_items(import_id)


def test_apply_entity_and_reject_fact(tmp_path: Path) -> None:
    db = connect(tmp_path / "apply.db")
    migrate(db)
    import_id, items = _stage(db)
    entity = next(item for item in items if item["kind"] == "entity")
    fact = next(item for item in items if item["kind"] == "fact")
    result = apply_item(db, entity["item_id"])
    assert result["status"] == "applied"
    assert CampaignStore(db).get_entity("night", result["applied_target_id"])
    review_item(db, fact["item_id"], reject=True)
    assert derived_batch_status(db, import_id) == "partially_applied"
    again = apply_item(db, entity["item_id"])
    assert again["status"] == "already_applied"
    db.close()


def test_apply_participant_and_control(tmp_path: Path) -> None:
    db = connect(tmp_path / "mem.db")
    migrate(db)
    _, items = _stage(db)
    for kind in ("entity", "participant", "control"):
        item = next(row for row in items if row["kind"] == kind)
        if kind == "control":
            # Control requires the entity and participant first.
            continue
        apply_item(db, item["item_id"])
    # Apply remaining entities then control.
    for item in items:
        if item["kind"] == "entity" and item["review_state"] == "pending_review":
            apply_item(db, item["item_id"])
    control = next(item for item in items if item["kind"] == "control")
    result = apply_item(db, control["item_id"])
    assert result["status"] == "applied"
    controls = MembershipStore(db).list_controls("night")
    assert any(row["entity_id"] == "ada" for row in controls)
    db.close()
