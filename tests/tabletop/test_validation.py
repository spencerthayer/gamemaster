"""Validation returns stable check ids a program can branch on."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tabletop.api.capabilities import Capability
from tabletop.api.plugin import (
    TABLETOP_PLUGIN_API_VERSION,
    GameSystemInfo,
    GameSystemPlugin,
)
from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.models import PresenceType
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore
from tabletop.campaign.validation import (
    CheckStatus,
    ValidationReport,
    validate_campaign,
)
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "campaign-1"

#: Every id the contract promises. Adding one is fine; renaming one is not.
REQUIRED_CHECK_IDS = {
    "campaign.exists",
    "campaign.archived",
    "plugin.compatible",
    "plugin.state",
    "participant.gm.count",
    "participant.identity.unique",
    "character.ownership",
    "import.reviewed",
    "database.writable",
    "campaign.paths",
    "session.current",
    "scene.structure",
}


class _Plugin(GameSystemPlugin):
    @property
    def info(self) -> GameSystemInfo:
        return GameSystemInfo(
            id="freeform", name="Freeform", api_version=TABLETOP_PLUGIN_API_VERSION
        )

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.DICE})

    def resolve(self, action, context):  # pragma: no cover
        raise NotImplementedError


def _resolver(system_id: str) -> _Plugin:
    if system_id != "freeform":
        raise LookupError(f"no plugin for {system_id!r}")
    return _Plugin()


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "validation.db")
    migrate(connection)
    CampaignStore(connection).create_campaign(_CAMPAIGN, "First", "freeform")
    CampaignStore(connection).upsert_entity(_CAMPAIGN, "pc-ada", "Ada")
    yield connection
    connection.close()


def _validate(conn: sqlite3.Connection) -> ValidationReport:
    return validate_campaign(conn, _CAMPAIGN, plugin_resolver=_resolver)


def _by_id(report: ValidationReport) -> dict[str, CheckStatus]:
    return {check.check_id: check.status for check in report.checks}


# -- ids and shape ----------------------------------------------------------


def test_a_ready_campaign_reports_every_required_check(conn: sqlite3.Connection) -> None:
    report = _validate(conn)
    assert REQUIRED_CHECK_IDS <= set(_by_id(report))


def test_check_ids_are_unique(conn: sqlite3.Connection) -> None:
    ids = [check.check_id for check in _validate(conn).checks]
    assert len(ids) == len(set(ids))


def test_the_report_is_json_safe(conn: sqlite3.Connection) -> None:
    payload = _validate(conn).to_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["campaign_id"] == _CAMPAIGN
    assert "ready" in payload
    assert isinstance(payload["checks"], list)


def test_the_summary_counts_every_status(conn: sqlite3.Connection) -> None:
    summary = _validate(conn).to_dict()["summary"]
    assert set(summary) == {"pass", "fail", "warn", "skip", "not_applicable"}
    assert sum(summary.values()) == len(_validate(conn).checks)


# -- individual checks ------------------------------------------------------


def test_a_missing_campaign_fails_on_campaign_exists(tmp_path: Path) -> None:
    conn = connect(tmp_path / "empty.db")
    try:
        migrate(conn)
        report = validate_campaign(conn, "absent", plugin_resolver=_resolver)
        assert report.ready is False
        assert _by_id(report) == {"campaign.exists": CheckStatus.FAIL}
    finally:
        conn.close()


def test_an_archived_campaign_fails(conn: sqlite3.Connection) -> None:
    CampaignStore(conn).archive_campaign(_CAMPAIGN)
    assert _by_id(_validate(conn))["campaign.archived"] is CheckStatus.FAIL


def test_an_unavailable_plugin_fails_compatibility(conn: sqlite3.Connection) -> None:
    CampaignStore(conn).archive_campaign(_CAMPAIGN)  # force a second failure too
    report = validate_campaign(
        conn, _CAMPAIGN, plugin_resolver=lambda _: (_ for _ in ()).throw(LookupError("gone"))
    )
    assert _by_id(report)["plugin.compatible"] is CheckStatus.FAIL


def test_no_gm_fails_the_gm_count(conn: sqlite3.Connection) -> None:
    assert _by_id(_validate(conn))["participant.gm.count"] is CheckStatus.FAIL


def test_the_schema_refuses_a_second_gm(conn: sqlite3.Connection) -> None:
    """A unique index already enforces one GM, so the check cannot see two."""

    membership = MembershipStore(conn)
    membership.add_participant(_CAMPAIGN, "gm-1", "One", "gm")
    with pytest.raises(sqlite3.IntegrityError):
        membership.add_participant(_CAMPAIGN, "gm-2", "Two", "gm")
    assert _by_id(_validate(conn))["participant.gm.count"] is CheckStatus.PASS


def test_exactly_one_gm_passes(conn: sqlite3.Connection) -> None:
    MembershipStore(conn).add_participant(_CAMPAIGN, "gm-1", "The GM", "gm")
    assert _by_id(_validate(conn))["participant.gm.count"] is CheckStatus.PASS


def test_an_entity_owned_by_two_participants_fails_identity(
    conn: sqlite3.Connection,
) -> None:
    membership = MembershipStore(conn)
    membership.add_participant(_CAMPAIGN, "gm-1", "One", "gm")
    membership.add_participant(_CAMPAIGN, "player-1", "Two", "player")
    membership.grant_control(_CAMPAIGN, "gm-1", "pc-ada", "gm")
    membership.grant_control(_CAMPAIGN, "player-1", "pc-ada", "owner")
    assert _by_id(_validate(conn))["participant.identity.unique"] is CheckStatus.FAIL


def test_a_campaign_with_no_setting_is_not_applicable(conn: sqlite3.Connection) -> None:
    assert _by_id(_validate(conn))["campaign.paths"] is CheckStatus.NOT_APPLICABLE


def test_no_open_session_is_a_warning_not_a_failure(conn: sqlite3.Connection) -> None:
    MembershipStore(conn).add_participant(_CAMPAIGN, "gm-1", "The GM", "gm")
    report = _validate(conn)
    assert _by_id(report)["session.current"] is CheckStatus.WARN
    assert report.ready is True


def test_an_open_session_passes(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO sessions (session_id, campaign_id, started_at) "
        "VALUES ('session-1', ?, '2026-09-25T00:00:00Z')",
        (_CAMPAIGN,),
    )
    assert _by_id(_validate(conn))["session.current"] is CheckStatus.PASS


def test_no_open_scene_is_a_warning(conn: sqlite3.Connection) -> None:
    assert _by_id(_validate(conn))["scene.structure"] is CheckStatus.WARN


def test_an_open_scene_passes(conn: sqlite3.Connection) -> None:
    scenes = SceneStore(conn)
    scenes.open_scene(_CAMPAIGN, "scene-1", "Crossroads")
    scenes.enter(_CAMPAIGN, "scene-1", "pc-ada", PresenceType.PC)
    assert _by_id(_validate(conn))["scene.structure"] is CheckStatus.PASS


# -- no mutation ------------------------------------------------------------


def test_validation_writes_no_campaign_row(conn: sqlite3.Connection) -> None:
    _validate(conn)
    for table in ("campaigns", "entities", "facts", "events", "scenes", "sessions"):
        before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        _validate(conn)
        after = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert before == after, table


def test_the_writability_probe_leaves_no_table(conn: sqlite3.Connection) -> None:
    _validate(conn)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'validation_probe'"
    ).fetchone()
    assert row is None


def test_a_read_only_database_fails_writability(tmp_path: Path) -> None:
    path = tmp_path / "ro.db"
    conn = connect(path)
    migrate(conn)
    CampaignStore(conn).create_campaign(_CAMPAIGN, "First", "freeform")
    conn.close()
    path.chmod(0o444)
    try:
        readonly = connect(path)
        try:
            assert _by_id(_validate(readonly))["database.writable"] is CheckStatus.FAIL
        finally:
            readonly.close()
    finally:
        path.chmod(0o644)


# -- verdict ----------------------------------------------------------------


def test_a_warning_does_not_make_a_campaign_unready(conn: sqlite3.Connection) -> None:
    MembershipStore(conn).add_participant(_CAMPAIGN, "gm-1", "The GM", "gm")
    report = _validate(conn)
    assert report.to_dict()["summary"]["warn"] >= 1
    assert report.ready is True


def test_any_failure_makes_a_campaign_unready(conn: sqlite3.Connection) -> None:
    assert _validate(conn).ready is False
    assert len(_validate(conn).failures()) >= 1
