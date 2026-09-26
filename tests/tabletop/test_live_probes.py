"""Live probes are explicit, bounded, injectable, and never leak secrets."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable

import pytest

from tabletop.campaign.live_probes import (
    DELIVERY_PROBE,
    Probe,
    ProbeContext,
    ProbeResult,
    default_probes,
    merge_live,
    run_probes,
)
from tabletop.campaign.store import CampaignStore
from tabletop.campaign.validation import CheckStatus, ValidationReport, validate_campaign
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "campaign-1"
SECRET = "super-secret-value"


def _result(status: CheckStatus, message: str = "ok") -> Callable[[], ProbeResult]:
    return lambda: ProbeResult(status, message)


@pytest.fixture
def context(tmp_path: Path) -> ProbeContext:
    database = tmp_path / "live.db"
    conn = connect(database)
    migrate(conn)
    CampaignStore(conn).create_campaign(_CAMPAIGN, "First", "freeform")
    conn.close()
    return ProbeContext(
        environ={}, repo_root=Path(__file__).resolve().parents[2],
        database_path=database, campaign_id=_CAMPAIGN,
    )


# -- probing is explicit ----------------------------------------------------


def test_the_default_set_never_includes_the_delivery_probe() -> None:
    probe_ids = {
        probe.probe_id
        for probe in default_probes(
            ProbeContext(environ={}, repo_root=Path("."), database_path=Path("x"), campaign_id="c")
        )
    }
    # Delivery sends a real message, so it must be opt-in even here.
    assert DELIVERY_PROBE not in probe_ids


def test_probes_run_without_network_or_docker(context: ProbeContext) -> None:
    checks = run_probes(default_probes(context), timeout_s=60.0)
    assert checks
    assert all(isinstance(check.status, CheckStatus) for check in checks)


def test_a_probe_that_raises_becomes_a_failed_check() -> None:
    def boom() -> ProbeResult:
        raise RuntimeError("probe exploded")

    checks = run_probes((Probe("live.boom", boom),))
    assert checks[0].status is CheckStatus.FAIL
    assert "probe exploded" in checks[0].message


def test_a_probe_that_hangs_becomes_a_failed_check() -> None:
    import time

    def slow() -> ProbeResult:
        time.sleep(0.2)
        return ProbeResult(CheckStatus.PASS)

    checks = run_probes((Probe("live.slow", slow),), timeout_s=0.01)
    assert checks[0].status is CheckStatus.FAIL
    assert "exceeded" in checks[0].message


def test_a_skipped_probe_is_reported_as_skipped_not_passed() -> None:
    checks = run_probes((Probe("live.skip", _result(CheckStatus.SKIP, "not configured")),))
    assert checks[0].status is CheckStatus.SKIP
    assert checks[0].status is not CheckStatus.PASS


def test_a_not_applicable_probe_keeps_its_own_status() -> None:
    checks = run_probes(
        (Probe("live.na", _result(CheckStatus.NOT_APPLICABLE, "no documents table")),)
    )
    assert checks[0].status is CheckStatus.NOT_APPLICABLE


# -- secrets ----------------------------------------------------------------


def test_a_secret_value_never_appears_in_probe_output() -> None:
    context = ProbeContext(
        environ={"OMEGA_MODEL_API_KEY": SECRET},
        repo_root=Path(__file__).resolve().parents[2],
        database_path=Path("x"),
        campaign_id=_CAMPAIGN,
    )
    checks = run_probes(default_probes(context), timeout_s=60.0)
    rendered = json.dumps([check.to_dict() for check in checks])
    assert SECRET not in rendered


def test_credentials_report_slot_names_not_values() -> None:
    from tabletop.campaign.live_probes import _credentials_present

    result = _credentials_present(
        ProbeContext(
            environ={"WS_TOKEN": SECRET}, repo_root=Path("."), database_path=Path("x"),
            campaign_id="c",
        ),
        ("WS_TOKEN",),
    )
    assert SECRET not in json.dumps(result.to_check("live.channel.credentials").to_dict())
    assert result.detail is not None
    assert result.detail["slots"] == ["WS_TOKEN"]


def test_missing_credentials_skip_rather_than_fail() -> None:
    from tabletop.campaign.live_probes import _credentials_present

    result = _credentials_present(
        ProbeContext(environ={}, repo_root=Path("."), database_path=Path("x"), campaign_id="c"),
        ("WS_TOKEN",),
    )
    # A missing credential is an environment fact, not a broken system.
    assert result.status is CheckStatus.SKIP


# -- reporting --------------------------------------------------------------


def test_live_checks_merge_into_a_static_report(context: ProbeContext) -> None:
    conn = connect(context.database_path)
    try:
        static = validate_campaign(conn, _CAMPAIGN)
    finally:
        conn.close()
    merged = merge_live(static, run_probes((Probe("live.x", _result(CheckStatus.PASS)),)))
    assert merged.live is True
    assert len(merged.checks) == len(static.checks) + 1
    assert merged.to_dict()["live"] is True


def test_a_live_failure_does_not_by_itself_unready_a_campaign(
    context: ProbeContext,
) -> None:
    conn = connect(context.database_path)
    try:
        static = validate_campaign(conn, _CAMPAIGN)
    finally:
        conn.close()
    merged = merge_live(static, run_probes((Probe("live.x", _result(CheckStatus.FAIL)),)))
    # The environment is broken, not the campaign.
    assert merged.to_dict()["ready"] is False
    assert any(check.check_id.startswith("live.") for check in merged.failures())
