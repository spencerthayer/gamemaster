"""Campaign readiness report tests."""

from __future__ import annotations

from pathlib import Path

from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.readiness import readiness_report
from tabletop.campaign.store import CampaignStore
from tabletop.importing.interface import ImportBatch, ImportItem
from tabletop.importing.store import ImportStore
from tabletop.storage.sqlite import connect, migrate


def _clean_campaign(db) -> None:
    CampaignStore(db).create_campaign("night", "Night", "freeform")
    MembershipStore(db).add_participant("night", "gm1", "GM", "gm")


def test_readiness_ok_without_channel(tmp_path: Path) -> None:
    db = connect(tmp_path / "ready.db")
    migrate(db)
    _clean_campaign(db)
    report = readiness_report(db, "night", environ={})
    assert report["ok"] is True
    db.close()


def test_readiness_errors_when_channel_lacks_expected_sender(tmp_path: Path) -> None:
    db = connect(tmp_path / "ready.db")
    migrate(db)
    _clean_campaign(db)
    report = readiness_report(
        db,
        "night",
        environ={"OMEGA_COMMCHANNEL": "telegram"},
    )
    assert report["ok"] is False
    assert any("OMEGA_EXPECTED_SENDER" in item for item in report["errors"])
    db.close()


def test_readiness_requires_gm(tmp_path: Path) -> None:
    db = connect(tmp_path / "nogm.db")
    migrate(db)
    CampaignStore(db).create_campaign("night", "Night", "freeform")
    report = readiness_report(db, "night", environ={})
    assert report["ok"] is False
    assert any("exactly one GM" in item for item in report["errors"])
    db.close()


def test_require_reviewed_promotes_pending_imports(tmp_path: Path) -> None:
    db = connect(tmp_path / "rev.db")
    migrate(db)
    _clean_campaign(db)
    ImportStore(db).stage_batch(
        "night",
        ImportBatch(
            format_id="gamemaster-external/v1",
            source_label="x",
            items=(
                ImportItem(
                    kind="entity",
                    payload={"entity_id": "ada", "name": "Ada"},
                    provenance={},
                    proposed_key="ada",
                ),
            ),
        ),
    )
    warn = readiness_report(db, "night", environ={}, require_reviewed=False)
    assert warn["ok"] is True
    assert any("pending import" in item for item in warn["warnings"])
    err = readiness_report(db, "night", environ={}, require_reviewed=True)
    assert err["ok"] is False
    assert any("pending import" in item for item in err["errors"])
    db.close()
