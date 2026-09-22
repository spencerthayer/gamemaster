"""Strict loader for non-executable content-pack manifests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

from tabletop.api.errors import ContentPackError

MANIFEST_FILENAME = "content-pack.yaml"

_REQUIRED_FIELDS = ("id", "name", "pack_type", "system_id", "version")
_OPTIONAL_FIELDS = ("gm_only",)
_ALLOWED_FIELDS = frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS)
_PACK_TYPES = frozenset(
    {"rules", "setting", "adventure", "campaign-seed", "supplement"}
)


@dataclass(frozen=True)
class ContentPackManifest:
    """Validated metadata for one data-only content pack."""

    id: str
    name: str
    pack_type: str
    system_id: str
    version: str
    gm_only: tuple[str, ...] = ()


def _validated_gm_only(raw: object, pack_root: Path, manifest_path: Path) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ContentPackError(
            f"manifest {manifest_path} field 'gm_only' must be a list of paths"
        )

    pack_root = pack_root.resolve()
    paths: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value:
            raise ContentPackError(
                f"manifest {manifest_path} field 'gm_only' must contain non-empty strings"
            )
        if Path(value).is_absolute():
            raise ContentPackError(
                f"manifest {manifest_path} gm_only path {value!r} must be relative, "
                "not absolute"
            )
        try:
            (pack_root / value).resolve().relative_to(pack_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ContentPackError(
                f"manifest {manifest_path} gm_only path {value!r} is outside pack directory"
            ) from exc
        paths.append(value)
    return tuple(paths)


def load_content_pack(directory: Path) -> ContentPackManifest:
    """Read and validate ``content-pack.yaml`` without importing pack code."""
    pack_root = Path(directory)
    manifest_path = pack_root / MANIFEST_FILENAME
    try:
        raw = yaml.safe_load(manifest_path.read_text())
    except OSError as exc:
        raise ContentPackError(f"cannot read manifest {manifest_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ContentPackError(f"malformed YAML in {manifest_path}: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise ContentPackError(f"manifest {manifest_path} must be a mapping")

    missing = [field for field in _REQUIRED_FIELDS if not raw.get(field)]
    if missing:
        raise ContentPackError(
            f"manifest {manifest_path} missing required fields: {missing}"
        )

    unknown = sorted(set(raw) - _ALLOWED_FIELDS)
    if unknown:
        raise ContentPackError(
            f"manifest {manifest_path} has unknown fields {unknown}; "
            f"allowed: {sorted(_ALLOWED_FIELDS)}"
        )

    for field in _REQUIRED_FIELDS:
        if not isinstance(raw[field], str):
            raise ContentPackError(
                f"manifest {manifest_path} field {field!r} must be a string"
            )

    pack_type = raw["pack_type"]
    if pack_type not in _PACK_TYPES:
        raise ContentPackError(
            f"manifest {manifest_path} field 'pack_type' must be one of "
            f"{sorted(_PACK_TYPES)}"
        )

    return ContentPackManifest(
        id=raw["id"],
        name=raw["name"],
        pack_type=pack_type,
        system_id=raw["system_id"],
        version=raw["version"],
        gm_only=_validated_gm_only(raw.get("gm_only"), pack_root, manifest_path),
    )
