"""Helpers for the Omega sender ContextVar bridge probe."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

EVIDENCE_PATH = Path(
    os.environ.get("SENDER_EVIDENCE_PATH", "/tmp/sender_evidence.json")
)


def bootstrap() -> str:
    channels = "/PeTTa/repos/Omega/channels"
    if channels not in sys.path:
        sys.path.insert(0, channels)
    return "ok"


def accept_sender() -> str:
    from sender import set_current_sender

    set_current_sender("42")
    return "42"


def read_sender() -> str:
    from sender import current_sender

    observed = current_sender()
    value = None if observed is None else str(observed)
    evidence = {
        "python_version": sys.version,
        "channel": "metta-two-pycall-probe",
        "thread_boundaries": "petta-swipl-py-call",
        "asyncio_task_boundaries": "none",
        "opaque_sender_at_receive": "42",
        "current_sender_at_pycall": value,
        "contextvar_survives": value == "42",
    }
    EVIDENCE_PATH.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return value or ""
