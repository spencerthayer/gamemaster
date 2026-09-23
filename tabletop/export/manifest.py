"""Native campaign package digests and validation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

FORMAT = "gamemaster-campaign/v1"
SETTING_AUTHORITATIVE_TABLES = frozenset(
    {"settings", "entities", "facts", "setting_events"}
)
_CREDENTIAL_KEYS = frozenset({"api_key", "token", "password", "secret", "credential"})


class PackageError(ValueError):
    """Unsafe or incompatible campaign package."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_package_digest(manifest: Mapping[str, Any], file_hashes: Mapping[str, str]) -> str:
    without = {key: value for key, value in manifest.items() if key != "package_digest"}
    payload = {"manifest.json": sha256_text(canonical_json(without))}
    for path in sorted(file_hashes):
        if path == "manifest.json":
            continue
        payload[path] = file_hashes[path]
    return sha256_text(canonical_json(payload))


def validate_package_directory(package_dir: Path) -> dict[str, Any]:
    """Validate a package on disk. Writes nothing."""

    root = package_dir.resolve()
    if not root.is_dir():
        raise PackageError(f"package directory {root} does not exist")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise PackageError(f"symlink rejected: {path}")
        if path.is_file():
            try:
                path.resolve().relative_to(root)
            except ValueError as exc:
                raise PackageError(f"path escapes package: {path}") from exc
            relative = path.relative_to(root).as_posix()
            if ".." in relative.split("/"):
                raise PackageError(f"path traversal rejected: {relative}")
            if path.name == "plugin.yaml" or relative.endswith("/plugin.yaml"):
                raise PackageError("plugin.yaml members are rejected")
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise PackageError("manifest.json is required")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise PackageError("manifest must be an object")
    for key in manifest:
        if key in _CREDENTIAL_KEYS or key.lower() in _CREDENTIAL_KEYS:
            raise PackageError(f"credential-shaped key rejected: {key}")
    if manifest.get("format") != FORMAT:
        raise PackageError(f"unsupported package format: {manifest.get('format')!r}")
    unknown = set(manifest) - {
        "format",
        "package_digest",
        "schema_migrations",
        "event_schema_version",
        "system_id",
        "system_version",
        "api_version",
        "content_pack_ids",
        "document_hashes",
        "campaign_id",
        "setting_id",
        "setting_digest",
    }
    if unknown:
        raise PackageError(f"unknown manifest members: {sorted(unknown)}")
    file_hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        file_hashes[relative] = sha256_bytes(path.read_bytes())
    expected = compute_package_digest(manifest, file_hashes)
    if manifest.get("package_digest") != expected:
        raise PackageError("package_digest mismatch")
    return manifest
