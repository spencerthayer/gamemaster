"""Prove Omega recomputes allocated context inside each CHARS_SENT payload."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.docker, pytest.mark.omega]

_REPO = Path(__file__).resolve().parents[2]
_PROJECT = "gamemaster-omega-prompt"
_HEADER = "Allocated tabletop context"
_POLICY = "Tabletop Runtime is authoritative"
_SEPARATOR = ":-:-:-:"

_SEED = r"""
import sys
sys.path.insert(0, "/PeTTa/repos/Omega")
from tabletop.campaign.store import CampaignStore
from tabletop.documents.extraction import ProposedExtraction, ProposedFact
from tabletop.documents.importer import import_extraction
from tabletop.storage.sqlite import connect, migrate

connection = connect("/state/tabletop.sqlite3")
migrate(connection)
connection.execute(
    "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
    ("setting-1", "Coast", "2026-09-22T00:00:00Z"),
)
CampaignStore(connection).create_campaign(
    "campaign-a", "Active", "freeform", setting_id="setting-1"
)
CampaignStore(connection).create_campaign(
    "campaign-b", "Other", "freeform", setting_id="setting-1"
)
connection.execute(
    "INSERT INTO ingest_jobs (job_id, document_hash, parser_version, "
    "slice_strategy_version, status, total_slices, completed_slices, "
    "failed_slices, started_at, updated_at) "
    "VALUES ('job-1', 'hash-1', '1', '1', 'completed', 1, 1, 0, ?, ?)",
    ("2026-09-22T00:00:00Z", "2026-09-22T00:00:00Z"),
)
connection.commit()

def proposed(fact_id, campaign_id, value):
    return ProposedFact(
        fact_id=fact_id,
        fact_scope="campaign",
        setting_id=None,
        campaign_id=campaign_id,
        subject_id=fact_id,
        predicate="note",
        value=value,
        visibility="GM",
        source_document_id="document-1",
        source_chunk_id="chunk-1",
    )

report = import_extraction(
    connection,
    ProposedExtraction(
        job_id="job-1",
        slice_index=0,
        extractor_version="extractor-1",
        facts=(
            proposed("fact-active", "campaign-a", "VISIBLE-ACTIVE-FACT"),
            proposed("fact-other", "campaign-b", "OTHER-CAMPAIGN-SENTINEL"),
        ),
    ),
)
assert "fact-active" in report.accepted_ids
assert "fact-other" in report.accepted_ids
connection.close()
"""

_AFTER = r"""
import sys
sys.path.insert(0, "/PeTTa/repos/Omega")
import os
from tabletop.documents.extraction import ProposedExtraction, ProposedFact
from tabletop.documents.importer import import_extraction
from tabletop.storage.sqlite import connect

connection = connect(os.environ["TABLETOP_DATABASE_PATH"])
report = import_extraction(
    connection,
    ProposedExtraction(
        job_id="job-1",
        slice_index=1,
        extractor_version="extractor-1",
        facts=(
            ProposedFact(
                fact_id="fact-after",
                fact_scope="campaign",
                setting_id=None,
                campaign_id="campaign-a",
                subject_id="fact-after",
                predicate="note",
                value="VISIBLE-AFTER-START",
                visibility="GM",
                source_document_id="document-1",
                source_chunk_id="chunk-1",
            ),
        ),
    ),
)
assert "fact-after" in report.accepted_ids
connection.close()
"""


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["TABLETOP_STATE_VOLUME"] = "gamemaster-omega-prompt-state"
    env["OMEGA_MEMORY_VOLUME"] = "gamemaster-omega-prompt-memory"
    env["TEST_SERVER_IP"] = "127.0.0.1"
    env["TABLETOP_WORKSPACE"] = "campaign"
    env["TABLETOP_CAMPAIGN"] = "campaign-a"
    return env


def _compose(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(_REPO / ".env.example"),
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.integration.yml",
            "-p",
            _PROJECT,
            *args,
        ],
        cwd=_REPO,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _payloads(logs: str) -> list[str]:
    """Return each CHARS_SENT event body, including embedded newlines."""

    bodies: list[str] = []
    for part in logs.split("CHARS_SENT:")[1:]:
        end = len(part)
        for marker in ("---------iteration", "\nRESPONSE:", "\nloop"):
            index = part.find(marker)
            if index != -1:
                end = min(end, index)
        bodies.append(part[:end])
    return bodies


def _assert_order(payload: str, fact: str) -> None:
    policy = payload.find(_POLICY)
    header = payload.find(_HEADER)
    visible = payload.find(fact)
    separator = payload.find(_SEPARATOR)
    assert min(policy, header, visible, separator) >= 0, payload[:500]
    assert policy < header < visible < separator


def test_allocated_context_refreshes_inside_chars_sent() -> None:
    env = _env()
    image = env.get("OMEGA_IMAGE", "gamemaster:latest")
    volume = env["TABLETOP_STATE_VOLUME"]
    try:
        _compose(env, "down", "--volumes")
        build = _compose(env, "build")
        assert build.returncode == 0, build.stderr[-2000:]
        subprocess.run(["docker", "volume", "create", volume], check=False)
        prepare = subprocess.run(
            [
                "docker", "run", "--rm", "--entrypoint", "sh",
                "-v", f"{volume}:/state", image,
                "-c", "mkdir -p /state && chown 65534:65534 /state",
            ],
            check=False, capture_output=True, text=True,
        )
        assert prepare.returncode == 0, prepare.stderr
        seed = subprocess.run(
            [
                "docker", "run", "--rm", "--user", "65534:65534",
                "--entrypoint", "python3", "-v", f"{volume}:/state", image,
                "-c", _SEED,
            ],
            check=False, capture_output=True, text=True,
        )
        assert seed.returncode == 0, seed.stdout[-1000:] + seed.stderr[-1000:]
        up = _compose(env, "up", "-d")
        assert up.returncode == 0, up.stderr[-2000:]
        deadline = time.monotonic() + 90
        logs = ""
        payloads: list[str] = []
        while time.monotonic() < deadline:
            logs = _compose(env, "logs", "--no-color").stdout
            payloads = _payloads(logs)
            if any("VISIBLE-ACTIVE-FACT" in payload for payload in payloads):
                break
            time.sleep(2)
        else:
            window = logs[-1500:]
            pytest.fail(f"CHARS_SENT payload did not include the active fact:\n{window}")
        before = logs.split("tabletop-prompt-extension", 1)[0]
        assert "Traceback" not in before
        first = next(payload for payload in payloads if "VISIBLE-ACTIVE-FACT" in payload)
        _assert_order(first, "VISIBLE-ACTIVE-FACT")
        assert all("OTHER-CAMPAIGN-SENTINEL" not in payload for payload in payloads)
        container = subprocess.run(
            ["docker", "compose", "-p", _PROJECT, "ps", "-q"],
            cwd=_REPO, env=env, check=False, capture_output=True, text=True,
        )
        container_id = container.stdout.strip().splitlines()[0]
        sealed = subprocess.run(
            [
                "docker", "exec", "--user", "65534:65534", container_id, "python3",
                "-c",
                "import os; path='/PeTTa/repos/Omega/plugins/tabletop/tabletop.metta';"
                "raise SystemExit(0 if not os.access(path, os.W_OK) else 1)",
            ],
            check=False, capture_output=True, text=True,
        )
        assert sealed.returncode == 0, sealed.stderr
        written = subprocess.run(
            [
                "docker", "exec", "--user", "65534:65534", container_id,
                "python3", "-c", _AFTER,
            ],
            check=False, capture_output=True, text=True,
        )
        assert written.returncode == 0, written.stdout[-1000:] + written.stderr[-1000:]
        deadline = time.monotonic() + 90
        refreshed = False
        for _attempt in (1, 2):
            while time.monotonic() < deadline:
                logs = _compose(env, "logs", "--no-color").stdout
                payloads = _payloads(logs)
                match = next(
                    (payload for payload in payloads if "VISIBLE-AFTER-START" in payload),
                    None,
                )
                if match is not None:
                    _assert_order(match, "VISIBLE-AFTER-START")
                    refreshed = True
                    break
                time.sleep(2)
            if refreshed:
                break
            deadline = time.monotonic() + 90
        if not refreshed:
            window = logs[-1500:]
            pytest.fail(
                "extension stayed static after an authoritative fact write:\n" + window
            )
        assert all("OTHER-CAMPAIGN-SENTINEL" not in payload for payload in payloads)
    finally:
        stop = _compose(env, "stop")
        assert stop.returncode == 0, stop.stderr
        _compose(env, "down", "--volumes")
