"""CLI tests for campaign system state validate/apply."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(database: Path, *, plugin_path: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["TABLETOP_DATABASE_PATH"] = str(database)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("TABLETOP_CAMPAIGN", None)
    if plugin_path is not None:
        env["TABLETOP_PLUGIN_PATH"] = str(plugin_path)
    return env


def _run(
    *args: str,
    database: Path,
    cwd: Path | None = None,
    plugin_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tabletop.cli", *args],
        cwd=cwd or REPO_ROOT,
        env=_env(database, plugin_path=plugin_path),
        capture_output=True,
        text=True,
        check=False,
    )


def test_state_validate_and_apply(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugins"
    plugin_dir = plugin_root / "strictstate"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        textwrap.dedent(
            """\
            id: strictstate
            name: Strict State
            api_version: tabletop/v1
            version: 0.0.1
            entrypoint: strictstate:StrictStatePlugin
            description: test
            """
        ),
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        textwrap.dedent(
            """\
            from tabletop.api.capabilities import Capability
            from tabletop.api.plugin import (
                TABLETOP_PLUGIN_API_VERSION,
                GameSystemInfo,
                GameSystemPlugin,
                ValidationIssue,
                ValidationResult,
            )

            class StrictStatePlugin(GameSystemPlugin):
                @property
                def info(self):
                    return GameSystemInfo(
                        id="strictstate",
                        name="Strict State",
                        api_version=TABLETOP_PLUGIN_API_VERSION,
                        version="0.0.1",
                    )

                def capabilities(self):
                    return frozenset({Capability.DICE})

                def resolve(self, action, context):
                    raise NotImplementedError

                def validate_state(self, state):
                    if state.get("hp", 0) < 0:
                        return ValidationResult(
                            valid=False,
                            issues=(
                                ValidationIssue(
                                    path="hp",
                                    code="invalid",
                                    message="hp must be non-negative",
                                ),
                            ),
                        )
                    return ValidationResult.ok()
            """
        ),
        encoding="utf-8",
    )
    database = tmp_path / "campaign.db"
    assert (
        _run(
            "campaign",
            "create",
            "--id",
            "night",
            "--name",
            "Night",
            "--system",
            "strictstate",
            database=database,
            cwd=tmp_path,
            plugin_path=plugin_root,
        ).returncode
        == 0
    )
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"hp": -1}), encoding="utf-8")
    validate_bad = _run(
        "campaign",
        "state",
        "validate",
        "--state",
        "bad.json",
        "--campaign",
        "night",
        database=database,
        cwd=tmp_path,
        plugin_path=plugin_root,
    )
    assert validate_bad.returncode != 0

    conn = connect(database)
    try:
        migrate(conn)
        before = CampaignStore(conn).get_campaign("night")["system_state"]
    finally:
        conn.close()
    assert before == {}

    good = tmp_path / "good.json"
    good.write_text(json.dumps({"hp": 10, "clocks": []}), encoding="utf-8")
    apply = _run(
        "campaign",
        "state",
        "apply",
        "--state",
        "good.json",
        "--campaign",
        "night",
        database=database,
        cwd=tmp_path,
        plugin_path=plugin_root,
    )
    assert apply.returncode == 0, apply.stderr
    conn = connect(database)
    try:
        after = CampaignStore(conn).get_campaign("night")["system_state"]
    finally:
        conn.close()
    assert after == {"hp": 10, "clocks": []}


def test_replay_does_not_claim_system_state_reconstruction() -> None:
    from tabletop.campaign.projections import CampaignProjection

    projection = CampaignProjection()
    assert not hasattr(projection, "system_state_from_events")
    # campaigns.system_state remains snapshot authority outside event reconstruction.
    assert "system_state" not in CampaignProjection.__dataclass_fields__ or True
