"""Docker tests skip unless opted in, and fail closed when the daemon is down.

Docker creates a missing bind-mount source as root. ``library/`` is gitignored,
so a later test cannot create ``library/raw`` after the first ``compose up``.
Create the tree as the current user before any container starts.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]


def pytest_configure() -> None:
    for relative in ("library/raw", "campaigns", "plugins"):
        (_REPO / relative).mkdir(parents=True, exist_ok=True)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("GAMEMASTER_RUN_DOCKER") == "1":
        return
    skip = pytest.mark.skip(reason="set GAMEMASTER_RUN_DOCKER=1 to run container tests")
    for item in items:
        if item.get_closest_marker("docker") or item.get_closest_marker("omega"):
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def docker_daemon_when_opted_in() -> None:
    if os.environ.get("GAMEMASTER_RUN_DOCKER") != "1":
        return
    result = subprocess.run(
        ["docker", "info"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or "docker daemon is not available")
