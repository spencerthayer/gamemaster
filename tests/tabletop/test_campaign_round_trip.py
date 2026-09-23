"""Native package restore round-trip tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tabletop.api.events import GameEvent
from tabletop.campaign.event_store import EventStore, EventType
from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.replay_fidelity import report_database
from tabletop.campaign.setting_events import SettingEventStore, SettingEventType
from tabletop.campaign.store import CampaignStore
from tabletop.export.manifest import PackageError, canonical_json
from tabletop.export.package import (
    _insert_row,
    _read_jsonl,
    export_campaign,
    restore_package,
)
from tabletop.storage.sqlite import connect, migrate, transaction

_MIGRATIONS = Path(__file__).resolve().parents[2] / "tabletop" / "storage" / "migrations"


def _seed_rich_campaign(conn) -> None:
    conn.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("world", "World", "2026-01-01T00:00:00Z"),
    )
    CampaignStore(conn).create_campaign(
        "night",
        "Night",
        "freeform",
        setting_id="world",
        system_version="0.1.0",
    )
    store = CampaignStore(conn)
    store.upsert_entity("night", "ada", "Ada", entity_type="character")
    conn.execute(
        "INSERT INTO entities "
        "(entity_id, owner_scope, setting_id, campaign_id, entity_type, name) "
        "VALUES (?, 'setting', ?, NULL, 'place', ?)",
        ("harbor", "world", "Harbor"),
    )
    conn.execute(
        "INSERT INTO facts "
        "(fact_id, fact_scope, setting_id, campaign_id, subject_id, predicate, "
        "value, created_at) "
        "VALUES (?, 'setting', ?, NULL, ?, ?, ?, ?)",
        (
            "sf1",
            "world",
            "harbor",
            "climate",
            "foggy",
            "2026-01-01T00:00:00Z",
        ),
    )
    conn.execute(
        "INSERT INTO facts "
        "(fact_id, fact_scope, setting_id, campaign_id, subject_id, predicate, "
        "value, created_at) "
        "VALUES (?, 'campaign', ?, ?, ?, ?, ?, ?)",
        (
            "cf1",
            "world",
            "night",
            "ada",
            "status",
            "alive",
            "2026-01-01T00:00:00Z",
        ),
    )
    conn.execute(
        "INSERT INTO relationships "
        "(relationship_id, owner_scope, campaign_id, source_id, "
        "relationship_type, target_id) "
        "VALUES (?, 'campaign', ?, ?, ?, ?)",
        ("r1", "night", "ada", "allied_with", "harbor"),
    )
    conn.execute(
        "INSERT INTO sessions "
        "(session_id, campaign_id, started_at, ended_at, participants, summary) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "s1",
            "night",
            "2026-01-02T00:00:00Z",
            "2026-01-02T02:00:00Z",
            "[]",
            "start",
        ),
    )
    conn.execute(
        "INSERT INTO rulings "
        "(ruling_id, campaign_id, system_id, question, decision, scope, "
        "session_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "ru1",
            "night",
            "freeform",
            "Can Ada climb?",
            "Yes",
            "scene",
            "s1",
            "2026-01-02T01:00:00Z",
        ),
    )
    membership = MembershipStore(conn)
    membership.add_participant("night", "p1", "Pat", "player")
    membership.bind_principal("night", "p1", "telegram", "42", principal_id="prin1")
    membership.grant_control(
        "night", "p1", "ada", "owner", control_id="pc-main"
    )
    EventStore(conn).append(
        "night",
        GameEvent(
            event_type=EventType.SESSION_STARTED.value,
            payload={
                "session_id": "s1",
                "started_at": "2026-01-02T00:00:00Z",
            },
        ),
    )
    EventStore(conn).append(
        "night",
        GameEvent(
            event_type=EventType.SESSION_ENDED.value,
            payload={
                "session_id": "s1",
                "ended_at": "2026-01-02T02:00:00Z",
            },
        ),
    )
    # Generation-0 event for incomplete reconstruction signal.
    conn.execute(
        "INSERT INTO events "
        "(campaign_id, sequence, event_type, payload, occurred_at, event_schema_version) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "night",
            100,
            "ruling.recorded",
            '{"ruling_id":"legacy"}',
            "2026-01-01T00:00:00Z",
            0,
        ),
    )
    with transaction(conn):
        SettingEventStore(conn).append_in_transaction(
            conn,
            "world",
            SettingEventType.SETTING_EDITED,
            {"name": "World", "created_at": "2026-01-01T00:00:00Z"},
        )


def test_round_trip_preserves_ids_and_payloads(tmp_path: Path) -> None:
    source = connect(tmp_path / "source.db")
    migrate(source)
    _seed_rich_campaign(source)
    package = tmp_path / "pkg"
    export_campaign(source, "night", package, migrations_dir=_MIGRATIONS)

    dest = connect(tmp_path / "dest.db")
    migrate(dest)
    restore_package(dest, package)

    assert CampaignStore(dest).get_campaign("night")["setting_id"] == "world"
    assert CampaignStore(dest).get_entity("night", "ada")["name"] == "Ada"
    fact = dest.execute(
        "SELECT * FROM facts WHERE fact_id = ?", ("cf1",)
    ).fetchone()
    assert fact["campaign_id"] == "night"
    assert fact["value"] == "alive"
    setting_fact = dest.execute(
        "SELECT * FROM facts WHERE fact_id = ?", ("sf1",)
    ).fetchone()
    assert setting_fact["setting_id"] == "world"
    assert (
        dest.execute(
            "SELECT COUNT(*) FROM participants WHERE campaign_id = ?", ("night",)
        ).fetchone()[0]
        == 1
    )
    assert (
        dest.execute(
            "SELECT control_id FROM character_controls WHERE campaign_id = ?",
            ("night",),
        ).fetchone()[0]
        == "pc-main"
    )
    events = list(EventStore(dest).read("night"))
    source_events = list(EventStore(source).read("night"))
    assert len(events) == len(source_events)
    for left, right in zip(events, source_events):
        assert left.event_type == right.event_type
        assert left.payload == right.payload
        assert left.campaign_id == "night"
    reports = {
        (item.scope, item.owner_id): item for item in report_database(dest)
    }
    assert reports[("campaign", "night")].complete_reconstruction is False
    source.close()
    dest.close()


def test_restore_fails_when_campaign_has_events(tmp_path: Path) -> None:
    source = connect(tmp_path / "source.db")
    migrate(source)
    CampaignStore(source).create_campaign("night", "Night", "freeform")
    EventStore(source).append(
        "night",
        GameEvent(event_type=EventType.QUEST_MUTATED.value, payload={"quest_id": "q"}),
    )
    package = tmp_path / "pkg"
    export_campaign(source, "night", package, migrations_dir=_MIGRATIONS)

    dest = connect(tmp_path / "dest.db")
    migrate(dest)
    CampaignStore(dest).create_campaign("night", "Other", "freeform")
    EventStore(dest).append(
        "night",
        GameEvent(event_type=EventType.QUEST_MUTATED.value, payload={"quest_id": "keep"}),
    )
    before = list(EventStore(dest).read("night"))
    with pytest.raises(PackageError, match="authoritative state"):
        restore_package(dest, package)
    after = list(EventStore(dest).read("night"))
    assert [e.payload for e in after] == [e.payload for e in before]
    source.close()
    dest.close()


def test_pristine_shell_is_replaced(tmp_path: Path) -> None:
    source = connect(tmp_path / "source.db")
    migrate(source)
    CampaignStore(source).create_campaign("night", "Night", "freeform")
    package = tmp_path / "pkg"
    export_campaign(source, "night", package, migrations_dir=_MIGRATIONS)

    dest = connect(tmp_path / "dest.db")
    migrate(dest)
    CampaignStore(dest).create_campaign("night", "Shell", "freeform")
    restore_package(dest, package)
    assert CampaignStore(dest).get_campaign("night")["name"] == "Night"
    source.close()
    dest.close()


def test_shell_with_entity_fails_atomically(tmp_path: Path) -> None:
    source = connect(tmp_path / "source.db")
    migrate(source)
    CampaignStore(source).create_campaign("night", "Night", "freeform")
    package = tmp_path / "pkg"
    export_campaign(source, "night", package, migrations_dir=_MIGRATIONS)

    dest = connect(tmp_path / "dest.db")
    migrate(dest)
    CampaignStore(dest).create_campaign("night", "Shell", "freeform")
    CampaignStore(dest).upsert_entity("night", "bob", "Bob", entity_type="character")
    with pytest.raises(PackageError):
        restore_package(dest, package)
    assert CampaignStore(dest).get_campaign("night")["name"] == "Shell"
    assert CampaignStore(dest).get_entity("night", "bob")["name"] == "Bob"
    source.close()
    dest.close()


def test_setting_digest_mismatch_rejects(tmp_path: Path) -> None:
    source = connect(tmp_path / "source.db")
    migrate(source)
    _seed_rich_campaign(source)
    package = tmp_path / "pkg"
    export_campaign(source, "night", package, migrations_dir=_MIGRATIONS)

    dest = connect(tmp_path / "dest.db")
    migrate(dest)
    dest.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("world", "Different", "2026-01-01T00:00:00Z"),
    )
    with pytest.raises(PackageError, match="digest"):
        restore_package(dest, package)
    assert CampaignStore(dest).get_campaign("night") is None
    source.close()
    dest.close()


def test_matching_setting_digest_reuses_setting(tmp_path: Path) -> None:
    source = connect(tmp_path / "source.db")
    migrate(source)
    _seed_rich_campaign(source)
    package = tmp_path / "pkg"
    export_campaign(source, "night", package, migrations_dir=_MIGRATIONS)

    dest = connect(tmp_path / "dest.db")
    migrate(dest)
    # Pre-load the exact setting rows from the package so digests match.
    for row in _read_jsonl(package / "tables" / "settings.jsonl"):
        _insert_row(dest, "settings", row)
    for row in _read_jsonl(package / "tables" / "setting_entities.jsonl"):
        _insert_row(dest, "entities", row)
    for row in _read_jsonl(package / "tables" / "setting_facts.jsonl"):
        _insert_row(dest, "facts", row)
    for row in _read_jsonl(package / "setting-events.jsonl"):
        _insert_row(dest, "setting_events", row)
    restore_package(dest, package)
    assert (
        dest.execute(
            "SELECT COUNT(*) FROM settings WHERE setting_id = ?", ("world",)
        ).fetchone()[0]
        == 1
    )
    source.close()
    dest.close()


def test_future_migration_rejected(tmp_path: Path) -> None:
    source = connect(tmp_path / "source.db")
    migrate(source)
    CampaignStore(source).create_campaign("night", "Night", "freeform")
    package = tmp_path / "pkg"
    export_campaign(source, "night", package, migrations_dir=_MIGRATIONS)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_migrations"].append(
        {"filename": "9999_future.sql", "checksum": "abc"}
    )
    del manifest["package_digest"]
    from tabletop.export.manifest import compute_package_digest, sha256_bytes

    file_hashes = {
        path.relative_to(package).as_posix(): sha256_bytes(path.read_bytes())
        for path in package.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    manifest["package_digest"] = compute_package_digest(manifest, file_hashes)
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")

    dest = connect(tmp_path / "dest.db")
    migrate(dest)
    with pytest.raises(PackageError, match="package_schema_too_new"):
        restore_package(dest, package)
    source.close()
    dest.close()
