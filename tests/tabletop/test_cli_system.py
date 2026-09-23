"""CLI tests for system list and inspect."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy() if env is None else env
    return subprocess.run(
        [sys.executable, "-m", "tabletop.cli", *args],
        cwd=REPO_ROOT,
        env=merged,
        capture_output=True,
        text=True,
        check=False,
    )


def test_system_list_contains_builtins() -> None:
    result = _run("system", "list")
    assert result.returncode == 0
    for system_id in ("freeform", "dnd5e", "gurps"):
        assert system_id in result.stdout


def test_system_inspect_freeform() -> None:
    result = _run("system", "inspect", "freeform")
    assert result.returncode == 0
    assert "id: freeform" in result.stdout
    assert "api_version: tabletop/v1" in result.stdout
    assert "version:" in result.stdout
    assert "capabilities:" in result.stdout
    assert "entity_validation: available" in result.stdout


def test_system_inspect_missing_exits_nonzero() -> None:
    result = _run("system", "inspect", "missing-system")
    assert result.returncode != 0


def test_system_inspect_custom_state_schema(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugins"
    plugin_dir = plugin_root / "customstate"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        textwrap.dedent(
            """\
            id: customstate
            name: Custom State
            api_version: tabletop/v1
            version: 0.0.1
            entrypoint: customstate:CustomStatePlugin
            description: test plugin
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

            class CustomStatePlugin(GameSystemPlugin):
                @property
                def info(self):
                    return GameSystemInfo(
                        id="customstate",
                        name="Custom State",
                        api_version=TABLETOP_PLUGIN_API_VERSION,
                        version="0.0.1",
                    )

                def capabilities(self):
                    return frozenset({Capability.DICE})

                def resolve(self, action, context):
                    raise NotImplementedError

                def state_schema(self):
                    return {"custom_key": {"type": "string"}}

                def validate_entity_state(self, entity_type, state):
                    if entity_type == "character" and "bad" in state:
                        return ValidationResult(
                            valid=False,
                            issues=(
                                ValidationIssue(
                                    path="bad",
                                    code="forbidden",
                                    message="bad key",
                                ),
                            ),
                        )
                    return ValidationResult.ok()
            """
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["TABLETOP_PLUGIN_PATH"] = str(plugin_root)
    result = _run("system", "inspect", "customstate", env=env)
    assert result.returncode == 0
    assert "custom_key" in result.stdout
    assert "entity_validation: available" in result.stdout

    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"ok": True}), encoding="utf-8")
    validated = _run(
        "system",
        "inspect",
        "customstate",
        "--state",
        str(state_file),
        env=env,
    )
    assert validated.returncode == 0
    assert "state: valid" in validated.stdout
