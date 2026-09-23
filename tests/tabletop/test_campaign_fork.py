"""Native package fork tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tabletop.campaign.event_store import EventStore, EventType
from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.replay_fidelity import report_database
from tabletop.campaign.store import CampaignStore
from tabletop.export.manifest import PackageError
from tabletop.export.package import (
    CAMPAIGN_EXPORT_TABLES,
    FORK_IDENTITY_POLICY,
    export_campaign,
    fork_package,
)
from tabletop.storage.sqlite import connect, migrate

_MIGRATIONS = Path(__file__).resolve().parents[2] / "tabletop" / "storage" / "migrations"


def _seed(conn) -> None:
    conn.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("world", "World", "2026-01-01T00:00:00Z"),
    )
    CampaignStore(conn).create_campaign(
        "night", "Night", "freeform", setting_id="world"
    )
    CampaignStore(conn).upsert_entity(
        "night", "ada", "Ada", entity_type="character"
    )
    conn.execute(
        "INSERT INTO facts "
        "(fact_id, fact_scope, setting_id, campaign_id, subject_id, predicate, "
        "value, created_at) "
        "VALUES (?, 'campaign', ?, ?, ?, ?, ?, ?)",
        (
            "f123",
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
        ("r123", "night", "ada", "knows", "ada"),
    )
    conn.execute(
        "INSERT INTO sessions "
        "(session_id, campaign_id, started_at, participants) "
        "VALUES (?, ?, ?, ?)",
        ("s123", "night", "2026-01-02T00:00:00Z", "[]"),
    )
    conn.execute(
        "INSERT INTO rulings "
        "(ruling_id, campaign_id, system_id, question, decision, scope, "
        "session_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "ru123",
            "night",
            "freeform",
            "Q?",
            "A",
            "scene",
            "s123",
            "2026-01-02T01:00:00Z",
        ),
    )
    membership = MembershipStore(conn)
    membership.add_participant("night", "p1", "Pat", "player")
    membership.bind_principal("night", "p1", "telegram", "42")
    membership.grant_control(
        "night", "p1", "ada", "owner", control_id="pc-main"
    )


def test_fork_identity_policy_exhaustive() -> None:
    assert set(FORK_IDENTITY_POLICY) == set(CAMPAIGN_EXPORT_TABLES)


def test_fork_copies_current_state_and_starts_new_log(tmp_path: Path) -> None:
    source = connect(tmp_path / "source.db")
    migrate(source)
    _seed(source)
    package = tmp_path / "pkg"
    export_campaign(source, "night", package, migrations_dir=_MIGRATIONS)

    # Fork into the same database so id collisions would surface.
    result = fork_package(source, package, "dawn")
    assert result["campaign_id"] == "dawn"
    assert CampaignStore(source).get_campaign("dawn")["setting_id"] == "world"
    assert CampaignStore(source).get_entity("dawn", "ada")["name"] == "Ada"
    assert (
        source.execute(
            "SELECT participant_id FROM participants WHERE campaign_id = ?",
            ("dawn",),
        ).fetchone()[0]
        == "p1"
    )
    assert (
        source.execute(
            "SELECT control_id FROM character_controls WHERE campaign_id = ?",
            ("dawn",),
        ).fetchone()[0]
        == "pc-main"
    )
    dawn_fact = source.execute(
        "SELECT fact_id FROM facts WHERE campaign_id = ?", ("dawn",)
    ).fetchone()[0]
    assert dawn_fact != "f123"
    dawn_rel = source.execute(
        "SELECT relationship_id FROM relationships WHERE campaign_id = ?",
        ("dawn",),
    ).fetchone()[0]
    assert dawn_rel != "r123"
    dawn_session = source.execute(
        "SELECT session_id FROM sessions WHERE campaign_id = ?", ("dawn",)
    ).fetchone()[0]
    assert dawn_session != "s123"
    dawn_ruling = source.execute(
        "SELECT ruling_id, session_id FROM rulings WHERE campaign_id = ?",
        ("dawn",),
    ).fetchone()
    assert dawn_ruling["ruling_id"] != "ru123"
    assert dawn_ruling["session_id"] == dawn_session

    principals = source.execute(
        "SELECT COUNT(*) FROM participant_principals WHERE campaign_id = ?",
        ("dawn",),
    ).fetchone()[0]
    assert principals == 0
    MembershipStore(source).bind_principal("dawn", "p1", "telegram", "99")

    events = list(EventStore(source).read("dawn"))
    assert len(events) == 1
    assert events[0].event_type == EventType.CAMPAIGN_FORKED.value
    assert events[0].payload["forked_from_campaign_id"] == "night"
    assert events[0].payload["setting_id"] == "world"
    assert "package_digest" in events[0].payload
    night_types = {e.event_type for e in EventStore(source).read("night")}
    assert EventType.CAMPAIGN_FORKED.value not in night_types

    reports = {
        (item.scope, item.owner_id): item for item in report_database(source)
    }
    assert reports[("campaign", "dawn")].complete_reconstruction is False
    source.close()


def test_fork_rejects_existing_id(tmp_path: Path) -> None:
    source = connect(tmp_path / "source.db")
    migrate(source)
    CampaignStore(source).create_campaign("night", "Night", "freeform")
    package = tmp_path / "pkg"
    export_campaign(source, "night", package, migrations_dir=_MIGRATIONS)
    with pytest.raises(PackageError, match="already exists"):
        fork_package(source, package, "night")
    source.close()


def test_fork_rejects_setting_digest_mismatch(tmp_path: Path) -> None:
    source = connect(tmp_path / "source.db")
    migrate(source)
    _seed(source)
    package = tmp_path / "pkg"
    export_campaign(source, "night", package, migrations_dir=_MIGRATIONS)

    dest = connect(tmp_path / "dest.db")
    migrate(dest)
    dest.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("world", "Other", "2026-01-01T00:00:00Z"),
    )
    with pytest.raises(PackageError, match="digest"):
        fork_package(dest, package, "dawn")
    assert CampaignStore(dest).get_campaign("dawn") is None
    source.close()
    dest.close()
