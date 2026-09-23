"""Native package safety and digest tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tabletop.campaign.store import CampaignStore
from tabletop.export.manifest import (
    SETTING_AUTHORITATIVE_TABLES,
    PackageError,
    canonical_json,
    validate_package_directory,
)
from tabletop.export.package import (
    CAMPAIGN_EXPORT_TABLES,
    FORK_IDENTITY_POLICY,
    export_campaign,
    restore_package,
)
from tabletop.storage.sqlite import connect, migrate

_MIGRATIONS = Path(__file__).resolve().parents[2] / "tabletop" / "storage" / "migrations"


def test_setting_authoritative_table_set() -> None:
    assert SETTING_AUTHORITATIVE_TABLES == {
        "settings",
        "entities",
        "facts",
        "setting_events",
    }


def test_fork_identity_policy_covers_export_tables() -> None:
    assert set(FORK_IDENTITY_POLICY) == set(CAMPAIGN_EXPORT_TABLES)
    assert set(FORK_IDENTITY_POLICY.values()) <= {"preserve", "regenerate", "omit"}


def test_rejects_symlink(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "documents").mkdir()
    (package / "documents" / "notes.md").write_text("x", encoding="utf-8")
    target = tmp_path / "outside"
    target.write_text("secret", encoding="utf-8")
    (package / "documents" / "leak").symlink_to(target)
    (package / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PackageError, match="symlink"):
        validate_package_directory(package)


def test_rejects_plugin_yaml(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "plugin.yaml").write_text("id: evil\n", encoding="utf-8")
    (package / "manifest.json").write_text(
        json.dumps({"format": "gamemaster-campaign/v1", "package_digest": "x"}),
        encoding="utf-8",
    )
    with pytest.raises(PackageError, match="plugin.yaml"):
        validate_package_directory(package)


def test_rejects_credential_keys(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "manifest.json").write_text(
        canonical_json(
            {
                "format": "gamemaster-campaign/v1",
                "api_key": "secret",
                "package_digest": "x",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PackageError, match="credential"):
        validate_package_directory(package)


def test_export_restore_round_trip(tmp_path: Path) -> None:
    source = connect(tmp_path / "source.db")
    migrate(source)
    CampaignStore(source).create_campaign(
        "night", "Night", "freeform", system_version="0.1.0"
    )
    CampaignStore(source).upsert_entity(
        "night", "ada", "Ada", entity_type="character"
    )
    out = tmp_path / "export"
    export_campaign(source, "night", out, migrations_dir=_MIGRATIONS)
    validate_package_directory(out)

    dest = connect(tmp_path / "dest.db")
    migrate(dest)
    restore_package(dest, out)
    campaign = CampaignStore(dest).get_campaign("night")
    assert campaign is not None
    assert campaign["name"] == "Night"
    assert CampaignStore(dest).get_entity("night", "ada")["name"] == "Ada"
    source.close()
    dest.close()


def test_identical_exports_match_digest(tmp_path: Path) -> None:
    db = connect(tmp_path / "camp.db")
    migrate(db)
    CampaignStore(db).create_campaign("night", "Night", "freeform")
    a = tmp_path / "a"
    b = tmp_path / "b"
    ma = export_campaign(db, "night", a, migrations_dir=_MIGRATIONS)
    mb = export_campaign(db, "night", b, migrations_dir=_MIGRATIONS)
    assert ma["package_digest"] == mb["package_digest"]
    db.close()


def test_whitespace_change_changes_digest(tmp_path: Path) -> None:
    db = connect(tmp_path / "camp.db")
    migrate(db)
    CampaignStore(db).create_campaign("night", "Night", "freeform")
    out = tmp_path / "export"
    manifest = export_campaign(db, "night", out, migrations_dir=_MIGRATIONS)
    events = out / "events.jsonl"
    events.write_text(events.read_text(encoding="utf-8") + " \n", encoding="utf-8")
    with pytest.raises(PackageError, match="package_digest"):
        validate_package_directory(out)
    assert manifest["package_digest"]
    db.close()
