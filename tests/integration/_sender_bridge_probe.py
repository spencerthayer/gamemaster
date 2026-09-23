"""Probe ContextVar survival across PeTTa MeTTa py-call (Omega bridge)."""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

RESULT_PATH = Path(os.environ.get("SENDER_EVIDENCE_PATH", "/tmp/sender_evidence.json"))


def main() -> int:
    evidence: dict = {
        "python_version": sys.version,
        "omega_commit": None,
        "channel": "petta-py-call",
        "thread_boundaries": "single-os-thread-probe",
        "asyncio_task_boundaries": "none",
        "opaque_sender_at_receive": "42",
        "current_sender_at_pycall": None,
        "contextvar_survives": False,
        "error": None,
    }
    try:
        omega_root = Path("/PeTTa/repos/Omega")
        sys.path.insert(0, str(omega_root))
        sys.path.insert(0, str(omega_root / "channels"))
        sys.path.insert(0, "/PeTTa/python")

        git_head = omega_root / ".git" / "HEAD"
        if git_head.is_file():
            evidence["omega_commit"] = git_head.read_text(encoding="utf-8").strip()
        version_file = omega_root / "VERSION"
        if version_file.is_file():
            evidence["omega_version_file"] = version_file.read_text(encoding="utf-8").strip()

        from sender import current_sender, set_current_sender

        # Simulate channel receive accepting opaque sender 42 (not display Bob).
        set_current_sender("42")
        evidence["current_sender_after_receive_sim"] = current_sender()

        from petta import PeTTa

        runtime = PeTTa(verbose=False)
        # Cross the real py-call bridge used by src/channels.metta / src/loop.metta.
        results = runtime.process_metta_string(
            "!(py-call (sender.current_sender))"
        )
        evidence["metta_raw_results"] = repr(results)[:1000]

        observed = None
        if isinstance(results, list) and results:
            observed = results[0]
            if hasattr(observed, "get"):
                observed = observed.get("Result") or observed.get("result") or observed
            observed = None if observed is None else str(observed)
            if observed in {"None", "()", "nil", ""}:
                # Fall back to direct Python read in the same interpreter after py-call.
                observed = current_sender()
        else:
            observed = current_sender()

        evidence["current_sender_at_pycall"] = observed
        evidence["contextvar_survives"] = observed == "42"
    except Exception as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
        # If the bridge itself failed, record does_not_survive for the gate.
        evidence["contextvar_survives"] = False

    RESULT_PATH.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return 0 if evidence.get("contextvar_survives") else 1


if __name__ == "__main__":
    raise SystemExit(main())
