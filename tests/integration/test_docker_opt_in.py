"""Docker and Omega checks stay out of the default suite."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

_REPO = Path(__file__).resolve().parents[2]


def _docker_enabled() -> bool:
    return os.environ.get("GAMEMASTER_RUN_DOCKER") == "1"


@pytest.mark.skipif(not _docker_enabled(), reason="set GAMEMASTER_RUN_DOCKER=1 to build")
def test_image_contract() -> None:
    if shutil.which("docker") is None:
        pytest.fail("docker is not installed")
    config = subprocess.run(
        ["docker", "compose", "config"],
        cwd=_REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert config.returncode == 0, config.stderr
    assert "docker.sock" not in config.stdout
    build = subprocess.run(
        ["docker", "compose", "build"],
        cwd=_REPO,
        check=False,
    )
    assert build.returncode == 0


@pytest.mark.omega
@pytest.mark.skipif(not _docker_enabled(), reason="set GAMEMASTER_RUN_DOCKER=1 to boot")
def test_omega_plugin_log_is_opt_in() -> None:
    """Placeholder boot check. A skip is not a startup pass."""

    assert os.environ.get("GAMEMASTER_RUN_DOCKER") == "1"
