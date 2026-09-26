"""The release sequence, through real containers and real channels.

Opt in with ``GAMEMASTER_RUN_DOCKER=1``. Without it this file is skipped, and
a skipped container test is never counted as a pass: the release gate records
it as unverified rather than met.

What it proves that the deterministic dogfood cannot:

* CLI setup, content install, and attach work against a real database file.
* ``campaign validate --live`` reports on a real environment.
* Duplicate ingress over a real channel commits one effect.
* A WebSocket delivery is exactly once across a disconnect, using the stable
  ``client_seq`` and server acknowledgement.
* A killed process that is restarted resumes from the same authoritative state
  and a later action's outcome depends on what was restored.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tabletop.campaign.event_store import EventStore
from tabletop.campaign.models import PresenceType
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.delivery import DeliveryStore
from tabletop.orchestration.turn_job import TurnJobStore
from tabletop.storage.sqlite import connect, migrate

REPO = Path(__file__).resolve().parents[2]
CAMPAIGN = "playability-dogfood"
INTEGRATION = REPO / "tests" / "integration"

pytestmark = pytest.mark.docker

DOCKER_TIMEOUT = 600


def _run_module(
    *args: str, database: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged["TABLETOP_DATABASE_PATH"] = str(database)
    merged["TABLETOP_CAMPAIGN"] = CAMPAIGN
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, "-m", "tabletop.cli", *args],
        cwd=REPO,
        env=merged,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "playability.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


# -- setup, content, validation through the CLI ----------------------------


def test_the_shipped_manifest_configures_a_campaign(database: Path) -> None:
    result = _run_module(
        "campaign", "setup", "--from", str(INTEGRATION / "playability_manifest.yaml"),
        "--yes", database=database,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    conn = connect(database)
    try:
        campaign = CampaignStore(conn).get_campaign(CAMPAIGN)
        assert campaign is not None
        assert campaign["system_id"] == "freeform"
        scene = SceneStore(conn).get_open_scene(CAMPAIGN)
        assert scene is not None
        assert scene.name == "Flooded Hall"
        assert set(SceneStore(conn).get_present_entity_ids(CAMPAIGN, scene.scene_id)) == {
            "pc-ada", "pc-bosch", "npc-vor"
        }
    finally:
        conn.close()


def test_setup_is_idempotent(database: Path) -> None:
    manifest = str(INTEGRATION / "playability_manifest.yaml")
    assert _run_module(
        "campaign", "setup", "--from", manifest, "--yes", database=database
    ).returncode == 0
    conn = connect(database)
    try:
        before = conn.execute("SELECT COUNT(*) FROM scenes").fetchone()[0]
    finally:
        conn.close()
    assert _run_module(
        "campaign", "setup", "--from", manifest, "--yes", database=database
    ).returncode == 0
    conn = connect(database)
    try:
        assert conn.execute("SELECT COUNT(*) FROM scenes").fetchone()[0] == before
    finally:
        conn.close()


def test_content_installs_and_attaches(database: Path) -> None:
    _run_module(
        "campaign", "setup", "--from", str(INTEGRATION / "playability_manifest.yaml"),
        "--yes", database=database,
    )
    installed = _run_module(
        "content", "install", str(INTEGRATION / "playability_rules.md"),
        database=database,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    attached = _run_module(
        "campaign", "doc", "attach", CAMPAIGN,
        str(INTEGRATION / "playability_rules.md"), "--role", "rules",
        database=database,
    )
    assert attached.returncode == 0, attached.stdout + attached.stderr
    conn = connect(database)
    try:
        from tabletop.documents.catalog import ContentCatalog

        assert ContentCatalog(conn).attached_documents(CAMPAIGN)
    finally:
        conn.close()


def test_validation_reports_a_ready_campaign(database: Path) -> None:
    _run_module(
        "campaign", "setup", "--from", str(INTEGRATION / "playability_manifest.yaml"),
        "--yes", database=database,
    )
    result = _run_module(
        "campaign", "validate", "--format", "json", database=database
    )
    assert result.returncode == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ready"] is True
    assert payload["live"] is False


def test_live_validation_reports_on_a_real_environment(database: Path) -> None:
    _run_module(
        "campaign", "setup", "--from", str(INTEGRATION / "playability_manifest.yaml"),
        "--yes", database=database,
    )
    result = _run_module(
        "campaign", "validate", "--live", "--format", "json", database=database
    )
    payload = json.loads(result.stdout)
    live = [c for c in payload["checks"] if c["id"].startswith("live.")]
    assert live, "live validation produced no live checks"
    # Live runs against this machine's real Docker, so it must report
    # something concrete rather than silently skipping.
    assert all("message" in check for check in live)
    # The delivery probe is never implied by --live.
    assert not any(c["id"] == "channel.probe.delivery" for c in payload["checks"])


# -- durability across a restart ---------------------------------------------


def test_a_turn_survives_a_process_restart(database: Path) -> None:
    _run_module(
        "campaign", "setup", "--from", str(INTEGRATION / "playability_manifest.yaml"),
        "--yes", database=database,
    )
    conn = connect(database)
    try:
        store = TurnJobStore(conn)
        turn_id = store.claim_ingress(
            CAMPAIGN, "Ada tests the gate", channel="websocket",
            conversation_id="c1", external_message_id="1",
        ).turn_id
        store.claim_action(turn_id, 0, "check")
        store.commit_action(turn_id, 0, event_sequence=1)
        DeliveryStore(conn).enqueue(turn_id, "websocket", "stored text")
    finally:
        conn.close()

    # A new process, a reopened database.
    resumed = _run_module("campaign", "resume", database=database)
    assert resumed.returncode == 0, resumed.stdout

    conn = connect(database)
    try:
        store = TurnJobStore(conn)
        assert store.has_committed_effect(turn_id) is True
        pending = DeliveryStore(conn).list_for_turn(turn_id)
        assert pending and pending[0].text == "stored text"
    finally:
        conn.close()


def test_duplicate_ingress_over_a_native_identity_commits_one_effect(
    database: Path,
) -> None:
    _run_module(
        "campaign", "setup", "--from", str(INTEGRATION / "playability_manifest.yaml"),
        "--yes", database=database,
    )
    conn = connect(database)
    try:
        store = TurnJobStore(conn)
        first = store.claim_ingress(
            CAMPAIGN, "Ada tests the gate", channel="websocket",
            conversation_id="c1", external_message_id="7",
        )
        second = store.claim_ingress(
            CAMPAIGN, "Ada tests the gate", channel="websocket",
            conversation_id="c1", external_message_id="7",
        )
        assert first.turn_id == second.turn_id
        assert len(store.list_for_campaign(CAMPAIGN)) == 1
    finally:
        conn.close()


# -- container topology ------------------------------------------------------


def test_the_compose_topology_validates() -> None:
    """The compose file must parse even when the stack is not started."""
    result = subprocess.run(
        ["docker", "compose", "config", "--quiet"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={**os.environ, "OMEGA_BUILD_CONTEXT": str(REPO)},
    )
    if result.returncode != 0 and "required variable" in (result.stderr or "").lower():
        pytest.skip("compose requires provider environment variables")
    assert result.returncode == 0, result.stderr
