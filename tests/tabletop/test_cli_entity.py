"""CLI tests for campaign entity create/update."""

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


def _prepare(database: Path, cwd: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    assert (
        _run(
            "campaign",
            "create",
            "--id",
            "night",
            "--name",
            "Night",
            "--system",
            "freeform",
            database=database,
            cwd=cwd,
        ).returncode
        == 0
    )
    assert (
        _run("campaign", "select", "night", database=database, cwd=cwd).returncode == 0
    )


def test_entity_create_and_reject_duplicate(tmp_path: Path) -> None:
    database = tmp_path / "campaign.db"
    _prepare(database, tmp_path)
    state = tmp_path / "character.json"
    state.write_text(json.dumps({"trait": "bold"}), encoding="utf-8")
    create = _run(
        "campaign",
        "entity",
        "create",
        "--id",
        "ada",
        "--kind",
        "character",
        "--name",
        "Ada",
        "--state",
        "character.json",
        database=database,
        cwd=tmp_path,
    )
    assert create.returncode == 0, create.stderr
    dup = _run(
        "campaign",
        "entity",
        "create",
        "--id",
        "ada",
        "--kind",
        "character",
        "--name",
        "Ada2",
        "--state",
        "character.json",
        database=database,
        cwd=tmp_path,
    )
    assert dup.returncode != 0
    update = _run(
        "campaign",
        "entity",
        "update",
        "--id",
        "ada",
        "--name",
        "Ada Updated",
        "--state",
        "character.json",
        database=database,
        cwd=tmp_path,
    )
    assert update.returncode == 0, update.stderr
    replace = _run(
        "campaign",
        "entity",
        "create",
        "--id",
        "ada",
        "--kind",
        "character",
        "--name",
        "Ada Replaced",
        "--state",
        "character.json",
        "--replace",
        database=database,
        cwd=tmp_path,
    )
    assert replace.returncode == 0, replace.stderr

    conn = connect(database)
    try:
        migrate(conn)
        entity = CampaignStore(conn).get_entity("night", "ada")
        assert entity is not None
        assert entity["name"] == "Ada Replaced"
        assert entity["entity_type"] == "character"
    finally:
        conn.close()


def test_entity_validation_hook_rejects(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugins"
    plugin_dir = plugin_root / "strictent"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        textwrap.dedent(
            """\
            id: strictent
            name: Strict Entity
            api_version: tabletop/v1
            version: 0.0.1
            entrypoint: strictent:StrictEntityPlugin
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

            class StrictEntityPlugin(GameSystemPlugin):
                @property
                def info(self):
                    return GameSystemInfo(
                        id="strictent",
                        name="Strict Entity",
                        api_version=TABLETOP_PLUGIN_API_VERSION,
                        version="0.0.1",
                    )

                def capabilities(self):
                    return frozenset({Capability.DICE})

                def resolve(self, action, context):
                    raise NotImplementedError

                def validate_entity_state(self, entity_type, state):
                    if entity_type == "companion" and "bond" not in state:
                        return ValidationResult(
                            valid=False,
                            issues=(
                                ValidationIssue(
                                    path="bond",
                                    code="missing",
                                    message="bond required",
                                ),
                            ),
                        )
                    if entity_type == "character" and state.get("hp", 0) < 0:
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
    database.parent.mkdir(parents=True, exist_ok=True)
    assert (
        _run(
            "campaign",
            "create",
            "--id",
            "night",
            "--name",
            "Night",
            "--system",
            "strictent",
            database=database,
            cwd=tmp_path,
            plugin_path=plugin_root,
        ).returncode
        == 0
    )
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"hp": -1}), encoding="utf-8")
    rejected = _run(
        "campaign",
        "entity",
        "create",
        "--id",
        "ada",
        "--kind",
        "character",
        "--name",
        "Ada",
        "--state",
        "bad.json",
        database=database,
        cwd=tmp_path,
        plugin_path=plugin_root,
    )
    assert rejected.returncode != 0
    companion = tmp_path / "companion.json"
    companion.write_text(json.dumps({}), encoding="utf-8")
    rejected_companion = _run(
        "campaign",
        "entity",
        "create",
        "--id",
        "dog",
        "--kind",
        "companion",
        "--name",
        "Dog",
        "--state",
        "companion.json",
        database=database,
        cwd=tmp_path,
        plugin_path=plugin_root,
    )
    assert rejected_companion.returncode != 0
