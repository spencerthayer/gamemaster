"""Omega PeTTa bridge: ContextVar survives receive-equivalent set into later py-call."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.docker, pytest.mark.omega]

_REPO = Path(__file__).resolve().parents[2]
_PROBE_METTA = _REPO / "tests" / "integration" / "_sender_bridge_probe.metta"
_PROBE_HELPERS = _REPO / "tests" / "integration" / "_sender_bridge_helpers.py"
_EVIDENCE_DIR = _REPO / "docs" / "evidence"
_EVIDENCE_FILE = _EVIDENCE_DIR / "d2-contextvar-survives.json"


def test_contextvar_survives_petta_pycall_bridge() -> None:
    image = os.environ.get("OMEGA_IMAGE", "gamemaster:latest")
    _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            "-v",
            f"{_REPO / 'channels'}:/PeTTa/repos/Omega/channels:ro",
            "-v",
            f"{_PROBE_HELPERS}:/PeTTa/repos/Omega/sender_bridge_helpers.py:ro",
            "-v",
            f"{_PROBE_METTA}:/probe/sender_bridge_probe.metta:ro",
            "-v",
            f"{_EVIDENCE_DIR}:/evidence",
            "-e",
            "PYTHONPATH=/PeTTa/repos/Omega",
            image,
            "-c",
            "cd /PeTTa && sh run.sh /probe/sender_bridge_probe.metta "
            "&& cp /tmp/sender_evidence.json /evidence/d2-contextvar-survives.json",
        ],
        cwd=_REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    evidence = json.loads(_EVIDENCE_FILE.read_text(encoding="utf-8"))
    git = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    evidence["omega_commit"] = git.stdout.strip()
    evidence["display_identity"] = "Bob"
    evidence["opaque_sender_applied_at_receive"] = "42"
    _EVIDENCE_FILE.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    assert evidence["contextvar_survives"] is True
    assert evidence["current_sender_at_pycall"] == "42"
