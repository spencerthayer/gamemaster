"""Shared helpers for plugin discovery tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

PLUGIN_TEMPLATE = '''\
"""Synthetic test plugin."""

from typing import Any, Mapping

from tabletop.api.capabilities import Capability
from tabletop.api.plugin import GameSystemInfo, GameSystemPlugin, ValidationResult


class {class_name}(GameSystemPlugin):
    @property
    def info(self) -> GameSystemInfo:
        return GameSystemInfo(
            id={plugin_id!r},
            name={name!r},
            api_version={api_version!r},
            version={version!r},
        )

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({capabilities!r})

    def resolve(self, action, context):
        raise NotImplementedError

{extra_methods}
'''


def _make_plugin(
    root: Path,
    plugin_id: str,
    *,
    module_name: str | None = None,
    class_name: str = "TestPlugin",
    name: str | None = None,
    api_version: str = "tabletop/v1",
    version: str | None = "0.1.0",
    capabilities: tuple[Any, ...] = (),
    init_body: str = "pass",
    manifest_overrides: dict[str, Any] | None = None,
) -> Path:
    """Create one synthetic plugin directory under ``root`` and return it.

    Layout: ``root/<plugin_id>/plugin.yaml`` plus a Python package
    ``root/<plugin_id>/<module_name>/__init__.py`` implementing the
    GameSystemPlugin contract.
    """
    module = module_name or plugin_id.replace("-", "_")
    name = name or plugin_id
    plugin_dir = root / plugin_id
    package = plugin_dir / module
    package.mkdir(parents=True)

    methods = f"    def initialize(self) -> None:\n        {init_body}\n" if init_body else ""
    (package / "__init__.py").write_text(
        PLUGIN_TEMPLATE.format(
            class_name=class_name,
            plugin_id=plugin_id,
            name=name,
            api_version=api_version,
            version=version,
            capabilities=capabilities,
            extra_methods=methods,
        )
    )

    manifest: dict[str, Any] = {
        "id": plugin_id,
        "name": name,
        "api_version": api_version,
        "version": version,
        "entrypoint": f"{module}:{class_name}",
    }
    manifest.update(manifest_overrides or {})
    manifest_lines = []
    for key, value in manifest.items():
        if value is None:
            continue
        manifest_lines.append(f"{key}: {value}")
    (plugin_dir / "plugin.yaml").write_text("\n".join(manifest_lines) + "\n")
    return plugin_dir


@pytest.fixture
def make_plugin():
    """Factory fixture creating synthetic plugin directories under a root."""
    return _make_plugin
