"""Deterministic plugin discovery and trusted loading.

Two strictly separated steps:

``discover_plugins(roots)``
    inspects immediate child directories of configured roots for
    ``plugin.yaml``, parses and validates manifests, and returns metadata
    candidates. It never executes plugin Python.

``load_plugin(candidate)``
    imports the entrypoint, verifies the ``GameSystemPlugin`` contract,
    cross-checks identity against the manifest, initializes, and returns the
    instance. This is the only point where plugin code runs.

Configured plugin roots are the only executable trust boundaries. Campaigns,
library mounts, uploads, and content packs are never scanned.
"""

from __future__ import annotations

import importlib
import logging
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from tabletop.api.errors import (
    PluginApiVersionError,
    PluginIdentityMismatchError,
    PluginInitializationError,
    PluginLoadError,
    PluginManifestError,
)
from tabletop.api.plugin import (
    TABLETOP_PLUGIN_API_VERSION,
    GameSystemPlugin,
    is_compatible_api_version,
)
from tabletop.plugins.manifest import (
    MANIFEST_FILENAME,
    PluginManifest,
    load_manifest,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveredPlugin:
    """One validated installation candidate. No code has run yet."""

    manifest: PluginManifest
    root: Path


def discover_plugins(roots: Iterable[Path]) -> tuple[DiscoveredPlugin, ...]:
    """Discover plugin candidates as immediate children of configured roots.

    Immediate children only; no recursive scanning. A child directory
    without ``plugin.yaml`` is ignored. A malformed manifest fails closed.
    Candidates are sorted by manifest id for deterministic output. Symlink
    escapes are rejected: a plugin directory must resolve to a direct
    descendant of its root.
    """
    candidates: list[DiscoveredPlugin] = []
    for root in roots:
        resolved_root = Path(root).resolve()
        if not resolved_root.is_dir():
            raise PluginManifestError(
                f"configured plugin root {resolved_root} does not exist"
            )
        logger.debug("scanning plugin root %s", resolved_root)
        for child in sorted(resolved_root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            manifest_path = child / MANIFEST_FILENAME
            if not manifest_path.is_file():
                continue
            resolved_child = child.resolve()
            if resolved_child.parent != resolved_root:
                raise PluginManifestError(
                    f"plugin directory {child} resolves to {resolved_child}, "
                    f"outside configured root {resolved_root}"
                )
            manifest = load_manifest(manifest_path)
            logger.info("discovered plugin candidate %s at %s", manifest.id, resolved_child)
            candidates.append(DiscoveredPlugin(manifest=manifest, root=resolved_child))
    return tuple(sorted(candidates, key=lambda candidate: candidate.manifest.id))


@contextmanager
def plugin_import_path(path: Path) -> Iterator[None]:
    """Temporarily put ``path`` on sys.path, restoring it afterwards.

    sys.modules keeps loaded module names for the process lifetime, so
    plugin top-level module names must be globally unique within one
    process; the temporary path entry itself is always removed.
    """
    sys.path.insert(0, str(path))
    try:
        yield
    finally:
        try:
            sys.path.remove(str(path))
        except ValueError:  # pragma: no cover - defensive
            pass


def load_plugin(candidate: DiscoveredPlugin) -> GameSystemPlugin:
    """Load, validate, initialize, and return one discovered plugin.

    Order: re-check API compatibility, import the entrypoint module from the
    plugin directory, instantiate, verify the GameSystemPlugin contract,
    cross-check manifest identity, then initialize. Registration happens
    only after this succeeds. Any failure prevents a half-initialized plugin
    from being used.
    """
    manifest = candidate.manifest
    if not is_compatible_api_version(manifest.api_version):
        raise PluginApiVersionError(
            f"plugin {manifest.id!r} declares api_version {manifest.api_version!r}; "
            f"this runtime supports exactly {TABLETOP_PLUGIN_API_VERSION!r}"
        )

    module_name, _, class_name = manifest.entrypoint.partition(":")
    with plugin_import_path(_import_base(candidate.root, module_name)):
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise PluginLoadError(
                f"plugin {manifest.id!r}: cannot import module {module_name!r} "
                f"from {candidate.root}: {exc}"
            ) from exc
        try:
            plugin_class = getattr(module, class_name)
        except AttributeError as exc:
            raise PluginLoadError(
                f"plugin {manifest.id!r}: module {module_name!r} has no class "
                f"{class_name!r}"
            ) from exc
        try:
            plugin = plugin_class()
        except Exception as exc:
            raise PluginLoadError(
                f"plugin {manifest.id!r}: class {class_name!r} failed to construct: {exc}"
            ) from exc

    if not isinstance(plugin, GameSystemPlugin):
        raise PluginLoadError(
            f"plugin {manifest.id!r}: {type(plugin).__name__} does not implement "
            "GameSystemPlugin"
        )

    _check_identity(manifest, plugin)

    try:
        plugin.initialize()
    except Exception as exc:
        raise PluginInitializationError(
            f"plugin {manifest.id!r} failed during initialize(): {exc}"
        ) from exc

    logger.info("initialized plugin %s (%s)", manifest.id, manifest.name)
    return plugin


def _import_base(root: Path, module_name: str) -> Path:
    """Choose the sys.path base that makes ``module_name`` importable.

    Two supported layouts: the module is a package or module *inside* the
    plugin directory, or the plugin directory itself is the package (module
    name == directory name, parent is the import base). Anything else is a
    load error.
    """
    top_level = module_name.split(".")[0]
    if (root / top_level).is_dir() or (root / f"{top_level}.py").is_file():
        return root
    if (root / "__init__.py").is_file() and top_level == root.name:
        return root.parent
    raise PluginLoadError(
        f"module {module_name!r} not found inside plugin directory {root}; "
        "expected a package inside the directory, or the directory itself as "
        "a package named after the module"
    )


def _check_identity(manifest: PluginManifest, plugin: GameSystemPlugin) -> None:
    """Reject manifests that impersonate a different plugin."""
    mismatches = []
    if plugin.info.id != manifest.id:
        mismatches.append(f"id: manifest {manifest.id!r} vs plugin {plugin.info.id!r}")
    if plugin.info.api_version != manifest.api_version:
        mismatches.append(
            f"api_version: manifest {manifest.api_version!r} vs "
            f"plugin {plugin.info.api_version!r}"
        )
    if plugin.info.name != manifest.name:
        mismatches.append(f"name: manifest {manifest.name!r} vs plugin {plugin.info.name!r}")
    if manifest.version is not None and plugin.info.version != manifest.version:
        mismatches.append(
            f"version: manifest {manifest.version!r} vs plugin {plugin.info.version!r}"
        )
    if mismatches:
        raise PluginIdentityMismatchError(
            f"plugin identity mismatch for {manifest.id!r}: " + "; ".join(mismatches)
        )
