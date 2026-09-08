"""Plugin manifest model and strict parser.

``plugin.yaml`` is installation metadata only; the instantiated plugin object
remains behavioral truth (capabilities come from ``capabilities()``, never
from the manifest). Parsing is strict: unknown top-level fields are rejected
so typos and obsolete configuration fail loudly instead of being ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

from tabletop.api.errors import PluginApiVersionError, PluginManifestError
from tabletop.api.plugin import (
    TABLETOP_PLUGIN_API_VERSION,
    is_compatible_api_version,
    is_valid_system_id,
)

MANIFEST_FILENAME = "plugin.yaml"

_REQUIRED_FIELDS = ("id", "name", "api_version", "entrypoint")
_OPTIONAL_FIELDS = ("version", "description")
_ALLOWED_FIELDS = frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS)

# module.path:ClassName -- identifier characters and dots only; no slashes,
# no traversal, no shell syntax, no attribute chains or calls.
_ENTRYPOINT_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"


@dataclass(frozen=True)
class PluginManifest:
    """Strict installation metadata for one game-system plugin."""

    id: str
    name: str
    api_version: str
    entrypoint: str
    version: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        if not is_valid_system_id(self.id):
            raise PluginManifestError(f"manifest id {self.id!r} is not a valid system id")
        if not re.match(_ENTRYPOINT_PATTERN, self.entrypoint):
            raise PluginManifestError(
                f"manifest entrypoint {self.entrypoint!r} must be 'module:ClassName' "
                "with identifier characters only"
            )


def load_manifest(path: Path) -> PluginManifest:
    """Parse and validate one plugin.yaml.

    Raises PluginManifestError for unreadable, non-mapping, missing-field,
    unknown-field, or malformed-entrypoint manifests, and
    PluginApiVersionError for incompatible API versions. Safe YAML only:
    object construction tags are never executed.
    """
    try:
        raw = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise PluginManifestError(f"cannot read manifest {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise PluginManifestError(f"malformed YAML in {path}: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise PluginManifestError(f"manifest {path} must be a mapping")

    missing = [field for field in _REQUIRED_FIELDS if not raw.get(field)]
    if missing:
        raise PluginManifestError(f"manifest {path} missing required fields: {missing}")

    unknown = sorted(set(raw) - _ALLOWED_FIELDS)
    if unknown:
        raise PluginManifestError(
            f"manifest {path} has unknown fields {unknown}; allowed: {sorted(_ALLOWED_FIELDS)}"
        )

    for field in _OPTIONAL_FIELDS:
        if raw.get(field) is not None and not isinstance(raw[field], str):
            raise PluginManifestError(f"manifest {path} field {field!r} must be a string")

    manifest = PluginManifest(
        id=str(raw["id"]),
        name=str(raw["name"]),
        api_version=str(raw["api_version"]),
        entrypoint=str(raw["entrypoint"]),
        version=raw.get("version"),
        description=raw.get("description"),
    )

    if not is_compatible_api_version(manifest.api_version):
        raise PluginApiVersionError(
            f"manifest {path} declares api_version {manifest.api_version!r}; "
            f"this runtime supports exactly {TABLETOP_PLUGIN_API_VERSION!r}"
        )
    return manifest

