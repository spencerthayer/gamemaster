"""Build the image and check mounts, FTS5, PDF import, and volume ownership."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

_REPO = Path(__file__).resolve().parents[2]


def _compose_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("TABLETOP_STATE_VOLUME", "gamemaster-image-contract-state")
    env.setdefault("OMEGA_MEMORY_VOLUME", "gamemaster-image-contract-memory")
    return env


def _compose(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "--env-file", str(_REPO / ".env.example"), *args],
        cwd=_REPO,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_image_contract() -> None:
    env = _compose_env()
    config = _compose("config", env=env)
    assert config.returncode == 0, config.stderr
    rendered = config.stdout
    assert "docker.sock" not in rendered
    assert "read_only: true" in rendered

    build = subprocess.run(
        ["docker", "compose", "--env-file", str(_REPO / ".env.example"), "build"],
        cwd=_REPO,
        env=env,
        check=False,
    )
    assert build.returncode == 0

    image = env.get("OMEGA_IMAGE", "gamemaster:latest")
    probe = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "python3",
            image,
            "-c",
            textwrap.dedent(
                """
                import sqlite3
                import pypdf
                options = {
                    row[0]
                    for row in sqlite3.connect(":memory:").execute("pragma compile_options")
                }
                assert "ENABLE_FTS5" in options, sorted(options)
                assert pypdf.__name__ == "pypdf"
                """
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr

    volume = env["TABLETOP_STATE_VOLUME"]
    subprocess.run(["docker", "volume", "rm", "-f", volume], check=False)
    prepare = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            "-v",
            f"{volume}:/state",
            image,
            "-c",
            "mkdir -p /state && chown 65534:65534 /state",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert prepare.returncode == 0, prepare.stderr
    write = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "65534:65534",
            "--entrypoint",
            "python3",
            "-v",
            f"{volume}:/state",
            image,
            "-c",
            "import sqlite3; c=sqlite3.connect('/state/probe.sqlite3');"
            "c.execute('create table probe(n int)'); c.execute('insert into probe values (7)');"
            "c.commit()",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert write.returncode == 0, write.stderr
    read = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "65534:65534",
            "--entrypoint",
            "python3",
            "-v",
            f"{volume}:/state",
            image,
            "-c",
            "import sqlite3; c=sqlite3.connect('/state/probe.sqlite3');"
            "assert c.execute('select n from probe').fetchone()[0] == 7",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert read.returncode == 0, read.stderr
    subprocess.run(["docker", "volume", "rm", "-f", volume], check=False)
