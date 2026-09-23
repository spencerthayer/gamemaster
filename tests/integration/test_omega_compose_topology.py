"""Compose topology exposes GM plus per-participant player services."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def test_compose_config_names_gm_and_two_players() -> None:
    env = os.environ.copy()
    # Provide required defaults so `compose config` can resolve.
    env.setdefault("OMEGA_BUILD_CONTEXT", ".")
    env.setdefault("OMEGA_IMAGE", "gamemaster:latest")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(_REPO / ".env.example"),
            "-f",
            "docker-compose.yml",
            "config",
        ],
        cwd=_REPO,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    text = result.stdout
    assert "omega:" in text or "omega:" in text.replace(" ", "")
    assert "omega-player-ada" in text
    assert "omega-player-bo" in text
    assert "TABLETOP_WORKSPACE: campaign" in text or "TABLETOP_WORKSPACE:campaign" in text
    assert "TABLETOP_WORKSPACE: player" in text or "TABLETOP_WORKSPACE:player" in text
