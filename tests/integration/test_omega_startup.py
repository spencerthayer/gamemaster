"""Boot Omega in the image and require the tabletop plugin to load."""

from __future__ import annotations

import os
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.docker, pytest.mark.omega]

_REPO = Path(__file__).resolve().parents[2]
_PROJECT = "gamemaster-omega-startup"


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["TABLETOP_STATE_VOLUME"] = "gamemaster-omega-startup-state"
    env["OMEGA_MEMORY_VOLUME"] = "gamemaster-omega-startup-memory"
    env["TEST_SERVER_IP"] = "127.0.0.1"
    env["TABLETOP_WORKSPACE"] = "campaign"
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


_SEAL_PROBE = textwrap.dedent(
    """
    import os
    from pathlib import Path

    assert os.geteuid() == 65534, os.geteuid()

    def refuse(path: Path) -> None:
        assert path.exists(), path
        assert os.access(path, os.R_OK), path
        assert not os.access(path, os.W_OK), path
        try:
            if path.is_dir():
                (path / "uid65534-write-probe").write_text("nope")
            else:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write("nope")
        except OSError:
            return
        raise SystemExit(f"write succeeded: {path}")

    def allow(path: Path) -> None:
        assert path.is_dir(), path
        assert os.access(path, os.W_OK), path
        probe = path / "uid65534-write-probe"
        probe.write_text("ok", encoding="utf-8")
        assert probe.read_text(encoding="utf-8") == "ok"
        probe.unlink()

    refuse(Path("/PeTTa/repos/Omega/plugins/tabletop/tabletop.metta"))
    refuse(Path("/PeTTa/repos/Omega/plugins/tabletop"))
    refuse(Path("/PeTTa/repos/Omega/library/raw/seal-probe.txt"))
    refuse(Path("/PeTTa/repos/Omega/library"))
    allow(Path("/PeTTa/repos/Omega/campaigns"))
    """
)


def test_omega_loads_tabletop_and_shuts_down() -> None:
    library_probe = _REPO / "library" / "raw" / "seal-probe.txt"
    library_probe.parent.mkdir(parents=True, exist_ok=True)
    library_probe.write_text("sealed\n", encoding="utf-8")
    env = _env()
    campaigns = _REPO / "campaigns"
    campaigns.mkdir(exist_ok=True)
    try:
        _compose(env, "down", "--volumes")
        up = _compose(env, "up", "-d", "--build")
        assert up.returncode == 0, up.stderr
        deadline = time.monotonic() + 90
        logs = ""
        while time.monotonic() < deadline:
            logs = _compose(env, "logs", "--no-color").stdout
            if "tabletop-plugin" in logs and "tabletop-prompt-extension" in logs:
                break
            time.sleep(2)
        else:
            marker = logs.find("tabletop")
            window = logs[max(0, marker - 500): marker + 500] if marker >= 0 else logs[:2000]
            pytest.fail(f"plugin log did not appear within 90s:\n{window}")
        before = logs.split("tabletop-prompt-extension", 1)[0]
        assert "Traceback" not in before
        assert "tabletop-plugin-workspace" in logs
        assert "campaign" in logs
        container = subprocess.run(
            ["docker", "compose", "-p", _PROJECT, "ps", "-q"],
            cwd=_REPO,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        container_id = container.stdout.strip().splitlines()[0]
        uid = subprocess.run(
            [
                "docker",
                "exec",
                container_id,
                "sh",
                "-c",
                "awk '/^Uid:/{print $2; exit}' /proc/1/status",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        # pid 1 may be `su`; the run.sh child must be nobody (65534).
        child_uid = subprocess.run(
            [
                "docker",
                "exec",
                container_id,
                "sh",
                "-c",
                "for d in /proc/[0-9]*; do "
                "comm=$(cat \"$d/comm\"); "
                "[ \"$comm\" = swipl ] || continue; "
                "tr '\\0' ' ' < \"$d/cmdline\" | grep -q 'run.metta' || continue; "
                "awk '/^Uid:/{print $2; exit}' \"$d/status\"; exit; done",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert child_uid.stdout.strip() == "65534", (
            f"pid1={uid.stdout.strip()} child={child_uid.stdout.strip()} "
            f"err={child_uid.stderr}"
        )
        write = subprocess.run(
            [
                "docker",
                "exec",
                "--user",
                "65534:65534",
                container_id,
                "python3",
                "-c",
                "open('/PeTTa/repos/Omega/tabletop/data/startup-probe','w').write('ok')",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert write.returncode == 0, write.stderr
        skills = subprocess.run(
            [
                "docker",
                "exec",
                container_id,
                "python3",
                "-c",
                "import sys; sys.path[:0]=['/PeTTa/repos/Omega'];"
                "from tabletop.api.workspace import Workspace;"
                "print(','.join(skill.name for skill in Workspace.CAMPAIGN.skills))",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert skills.returncode == 0, skills.stderr
        # The model submits proposals; it never builds a structured action.
        assert "submit-action" in skills.stdout
        assert "resolve-action" not in skills.stdout
        assert "promote-ruling" in skills.stdout
        sealed = subprocess.run(
            [
                "docker",
                "exec",
                "--user",
                "65534:65534",
                container_id,
                "python3",
                "-c",
                _SEAL_PROBE,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert sealed.returncode == 0, sealed.stdout + sealed.stderr
    finally:
        library_probe.unlink(missing_ok=True)
        stop = _compose(env, "stop")
        assert stop.returncode == 0, stop.stderr
        _compose(env, "down", "--volumes")
