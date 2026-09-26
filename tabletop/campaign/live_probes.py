"""Bounded, injectable probes for the things static validation cannot see.

``--live`` must not be a hidden side effect, so every probe here is an
explicit callable the caller supplies. A probe returns a status and a message;
it never raises past the runner, and it never prints a secret.

Live evidence is opt-in and separable: a skipped probe is reported as a skip,
never as a pass, and the actual delivery probe runs only when explicitly
requested because it is the only one that sends a real message.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from tabletop.campaign.validation import CheckStatus, ValidationCheck, ValidationReport

#: Probes that only read local state. Cheap, safe, and always runnable.
STATIC_LIVE_PROBIDS = (
    "live.docker.available",
    "live.compose.file",
    "live.provider.credentials",
    "live.channel.credentials",
    "live.plugin.load",
    "live.retrieval.indexed",
    "live.documents.readable",
)

#: The only probe that sends anything. Off unless explicitly requested.
DELIVERY_PROBE = "channel.probe.delivery"


@dataclass(frozen=True)
class ProbeResult:
    """One probe's outcome."""

    status: CheckStatus
    message: str = ""
    detail: Mapping[str, Any] | None = None

    def to_check(self, probe_id: str) -> ValidationCheck:
        return ValidationCheck(
            check_id=probe_id, status=self.status, message=self.message, detail=self.detail
        )


@dataclass(frozen=True)
class Probe:
    """One named, injectable probe."""

    probe_id: str
    run: Callable[[], ProbeResult]


@dataclass(frozen=True)
class ProbeContext:
    """Everything a probe may read. Secrets are passed in, never discovered."""

    environ: Mapping[str, str]
    repo_root: Any
    database_path: Any
    campaign_id: str


def default_probes(context: ProbeContext) -> tuple[Probe, ...]:
    """Build the standard probe set against one context."""

    return (
        Probe("live.docker.available", lambda: _docker_available()),
        Probe("live.compose.file", lambda: _compose_file(context)),
        Probe(
            "live.provider.credentials",
            lambda: _credentials_present(context, _PROVIDER_SLOTS),
        ),
        Probe(
            "live.channel.credentials",
            lambda: _credentials_present(context, _CHANNEL_SLOTS),
        ),
        Probe("live.plugin.load", lambda: _plugin_load(context)),
        Probe("live.retrieval.indexed", lambda: _retrieval_indexed(context)),
        Probe("live.documents.readable", lambda: _documents_readable(context)),
    )


#: Provider credential slots. Presence is reported; values never are.
_PROVIDER_SLOTS = ("OMEGA_MODEL_API_KEY", "ASI_API_KEY", "ANTHROPIC_API_KEY")
_CHANNEL_SLOTS = ("WS_TOKEN", "TELEGRAM_TOKEN", "SLACK_TOKEN", "MATTERMOST_TOKEN")


def _docker_available() -> ProbeResult:
    if shutil.which("docker") is None:
        return ProbeResult(CheckStatus.FAIL, "docker is not on PATH")
    try:
        completed = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ProbeResult(CheckStatus.FAIL, f"docker is not usable: {exc}")
    if completed.returncode != 0:
        # The daemon being down is a distinct, actionable fact from the CLI
        # being absent, and an operator needs to tell them apart.
        return ProbeResult(
            CheckStatus.FAIL, "docker is installed but the daemon is not responding"
        )
    return ProbeResult(CheckStatus.PASS, "docker daemon is responding")


def _compose_file(context: ProbeContext) -> ProbeResult:
    candidate = context.repo_root / "docker-compose.yml"
    if not candidate.is_file():
        return ProbeResult(CheckStatus.FAIL, "docker-compose.yml is missing")
    return ProbeResult(CheckStatus.PASS, "compose file is present")


def _credentials_present(
    context: ProbeContext, slots: Sequence[str]
) -> ProbeResult:
    """Report which credential slots are set, never their values."""

    present = sorted(
        name for name in slots if str(context.environ.get(name, "")).strip()
    )
    if not present:
        return ProbeResult(
            CheckStatus.SKIP,
            "no credentials configured",
            {"expected_any_of": list(slots)},
        )
    # Naming a slot is safe; a value would not be.
    return ProbeResult(CheckStatus.PASS, "credentials present", {"slots": present})


def _plugin_load(context: ProbeContext) -> ProbeResult:
    from pathlib import Path

    from tabletop.plugins.discovery import discover_plugins, load_plugin

    systems = Path(context.repo_root) / "systems"
    if not systems.is_dir():
        return ProbeResult(CheckStatus.FAIL, "no systems directory")
    try:
        plugins = [load_plugin(candidate) for candidate in discover_plugins((systems,))]
    except Exception as exc:  # noqa: BLE001 - report, never raise
        return ProbeResult(CheckStatus.FAIL, f"a plugin failed to load: {exc}")
    if not plugins:
        return ProbeResult(CheckStatus.FAIL, "no plugins discovered")
    return ProbeResult(
        CheckStatus.PASS, "plugins load", {"plugins": sorted(p.info.id for p in plugins)}
    )


def _retrieval_indexed(context: ProbeContext) -> ProbeResult:
    import sqlite3

    path = Path(context.database_path)
    if not path.is_file():
        return ProbeResult(CheckStatus.FAIL, "database file is missing")
    conn = sqlite3.connect(str(path))
    try:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        conn.close()
    if not any(name.startswith("retrieval_") or "chunk" in name for name in tables):
        return ProbeResult(CheckStatus.SKIP, "no retrieval tables in this database")
    return ProbeResult(CheckStatus.PASS, "retrieval tables present")


def _documents_readable(context: ProbeContext) -> ProbeResult:
    import sqlite3

    path = Path(context.database_path)
    if not path.is_file():
        return ProbeResult(CheckStatus.FAIL, "database file is missing")
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(
            "SELECT source_path FROM documents WHERE campaign_id IS NULL OR 1 = 1"
        ).fetchall()
    except sqlite3.OperationalError:
        return ProbeResult(CheckStatus.NOT_APPLICABLE, "no documents table")
    finally:
        conn.close()
    missing = [str(row[0]) for row in rows if not Path(str(row[0])).is_file()]
    if missing:
        return ProbeResult(
            CheckStatus.FAIL,
            "an indexed document is no longer readable",
            {"missing_count": len(missing)},
        )
    return ProbeResult(CheckStatus.PASS, f"{len(rows)} documents readable")


def run_probes(
    probes: Sequence[Probe],
    *,
    timeout_s: float = 30.0,
) -> list[ValidationCheck]:
    """Run each probe, turning a crash into a failed check rather than a raise."""

    import time

    checks: list[ValidationCheck] = []
    for probe in probes:
        started = time.perf_counter()
        try:
            result = probe.run()
        except Exception as exc:  # noqa: BLE001 - a probe crash is a result
            result = ProbeResult(CheckStatus.FAIL, f"probe raised: {exc}")
        elapsed = time.perf_counter() - started
        if elapsed > timeout_s:
            result = ProbeResult(
                CheckStatus.FAIL, f"probe exceeded {timeout_s:g}s", result.detail
            )
        checks.append(result.to_check(probe.probe_id))
    return checks


def merge_live(
    report: ValidationReport, checks: Sequence[ValidationCheck]
) -> ValidationReport:
    """Fold live results into a static report.

    A live failure does not by itself make a campaign unready: a missing
    credential is an environment fact, not a broken campaign. It is reported
    as its own check so the operator can see it, and ``--live`` still fails
    the process through its own exit class.
    """

    return ValidationReport(
        campaign_id=report.campaign_id,
        checks=(*report.checks, *checks),
        live=True,
    )
