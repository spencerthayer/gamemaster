"""Compose topology exposes GM plus generated per-participant player services."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tabletop.cli.handlers import _generate_compose_override

_REPO = Path(__file__).resolve().parents[2]


def test_compose_config_names_gm_and_generated_player(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.setdefault("OMEGA_BUILD_CONTEXT", ".")
    env.setdefault("OMEGA_IMAGE", "gamemaster:latest")
    override = _generate_compose_override(
        participant_id="ada-player", runtime_compose_dir=tmp_path
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(_REPO / ".env.example"),
            "-f",
            str(_REPO / "docker-compose.yml"),
            "-f",
            str(override),
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
    assert "omega-player-ada-player:" in text
    assert "TABLETOP_WORKSPACE: campaign" in text or "TABLETOP_WORKSPACE:campaign" in text
    assert "TABLETOP_WORKSPACE: player" in text or "TABLETOP_WORKSPACE:player" in text
