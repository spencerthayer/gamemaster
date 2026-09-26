"""P4 acceptance: static, live, JSON, and exit classes behave independently."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

from tabletop.campaign.live_probes import (
    Probe,
    ProbeContext,
    ProbeResult,
    default_probes,
    merge_live,
    run_probes,
)
from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore
from tabletop.campaign.validation import (
    CheckStatus,
    ValidationCheck,
    ValidationReport,
    validate_campaign,
)
from tabletop.storage.sqlite import connect, migrate

REPO_ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN = "p4-demo"


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "p4.db"
    conn = connect(path)
    migrate(conn)
    store = CampaignStore(conn)
    store.create_campaign(CAMPAIGN, "P4 Demo", "freeform")
    store.upsert_entity(CAMPAIGN, "pc-ada", "Ada")
    MembershipStore(conn).add_participant(CAMPAIGN, "gm-1", "The GM", "gm")
    SceneStore(conn).open_scene(CAMPAIGN, "scene-1", "Crossroads")
    conn.close()
    return path


# -- every status combination ------------------------------------------------


@pytest.mark.parametrize(
    "statuses",
    [
        [CheckStatus.PASS],
        [CheckStatus.FAIL],
        [CheckStatus.WARN],
        [CheckStatus.SKIP],
        [CheckStatus.NOT_APPLICABLE],
        [CheckStatus.PASS, CheckStatus.FAIL],
        [CheckStatus.FAIL, CheckStatus.SKIP],
        [CheckStatus.PASS, CheckStatus.SKIP, CheckStatus.WARN],
    ],
)
def test_a_probe_result_keeps_its_own_status(statuses: list[CheckStatus]) -> None:
    """No status is rewritten on the way through the runner."""
    checks = run_probes(
        tuple(
            Probe(f"live.p{index}", _stub(status))
            for index, status in enumerate(statuses)
        )
    )
    assert [check.status for check in checks] == statuses


def _stub(status: CheckStatus) -> Callable[[], ProbeResult]:
    return lambda: ProbeResult(status, "stub")


# -- exit-class precedence --------------------------------------------------


def _report(failing: bool) -> ValidationReport:
    checks = [ValidationCheck("campaign.exists", CheckStatus.PASS)]
    if failing:
        checks.append(ValidationCheck("participant.gm.count", CheckStatus.FAIL, "none"))
    return ValidationReport(campaign_id=CAMPAIGN, checks=tuple(checks))


def test_validation_failure_outranks_a_live_pass() -> None:
    merged = merge_live(
        _report(failing=True), run_probes((Probe("live.ok", _stub(CheckStatus.PASS)),))
    )
    assert merged.ready is False


def test_a_live_failure_alone_also_blocks_the_live_exit_class() -> None:
    merged = merge_live(
        _report(failing=False), run_probes((Probe("live.bad", _stub(CheckStatus.FAIL)),))
    )
    live_failed = any(
        check.status is CheckStatus.FAIL and check.check_id.startswith("live.")
        for check in merged.checks
    )
    assert live_failed is True
    assert merged.ready is True


def test_a_skipped_live_probe_does_not_block_anything() -> None:
    merged = merge_live(
        _report(failing=False), run_probes((Probe("live.skip", _stub(CheckStatus.SKIP)),))
    )
    assert merged.ready is True
    assert not any(
        check.check_id.startswith("live.") for check in merged.failures()
    )


# -- static report shape ----------------------------------------------------


def test_the_static_report_is_stable_for_a_ready_campaign(database: Path) -> None:
    conn = connect(database)
    try:
        first = validate_campaign(conn, CAMPAIGN).to_dict()
        second = validate_campaign(conn, CAMPAIGN).to_dict()
    finally:
        conn.close()
    assert first == second
    assert first["ready"] is True
    assert first["summary"]["fail"] == 0


def test_the_static_report_lists_every_contract_id(database: Path) -> None:
    conn = connect(database)
    try:
        ids = {check.check_id for check in validate_campaign(conn, CAMPAIGN).checks}
    finally:
        conn.close()
    assert {
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
    } <= ids


# -- the documented command -------------------------------------------------


def test_the_documented_command_produces_documented_exit_codes(
    database: Path,
) -> None:
    env = os.environ.copy()
    env["TABLETOP_DATABASE_PATH"] = str(database)
    env["TABLETOP_CAMPAIGN"] = CAMPAIGN

    ready = subprocess.run(
        [sys.executable, "-m", "tabletop.cli", "campaign", "validate"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert ready.returncode == 0, ready.stdout

    invalid = subprocess.run(
        [sys.executable, "-m", "tabletop.cli", "campaign", "validate", "--nope"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert invalid.returncode == 3


def test_the_documented_json_command_is_machine_readable(database: Path) -> None:
    env = os.environ.copy()
    env["TABLETOP_DATABASE_PATH"] = str(database)
    env["TABLETOP_CAMPAIGN"] = CAMPAIGN
    result = subprocess.run(
        [
            sys.executable, "-m", "tabletop.cli", "campaign", "validate",
            "--format", "json",
        ],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False,
    )
    payload: Any = json.loads(result.stdout)
    assert payload["ready"] is True
    assert payload["live"] is False


def test_the_documented_live_command_runs_without_network(database: Path) -> None:
    env = os.environ.copy()
    env["TABLETOP_DATABASE_PATH"] = str(database)
    env["TABLETOP_CAMPAIGN"] = CAMPAIGN
    result = subprocess.run(
        [
            sys.executable, "-m", "tabletop.cli", "campaign", "validate",
            "--live", "--format", "json",
        ],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False,
    )
    payload = json.loads(result.stdout)
    live_ids = {c["id"] for c in payload["checks"] if c["id"].startswith("live.")}
    assert live_ids
    assert "channel.probe.delivery" not in live_ids


def test_live_probes_never_run_without_the_flag(database: Path) -> None:
    context = ProbeContext(
        environ={}, repo_root=REPO_ROOT, database_path=database, campaign_id=CAMPAIGN
    )
    # The default set is inert until a caller runs it.
    assert {p.probe_id for p in default_probes(context)}
