"""Shared CLI helpers for database and plugin loading."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Mapping

from tabletop.plugins.discovery import discover_plugins, load_plugin
from tabletop.plugins.registry import PluginRegistry
from tabletop.storage.sqlite import connect, migrate

DATABASE_PATH_ENV_VAR = "TABLETOP_DATABASE_PATH"
PLUGIN_PATH_ENV_VAR = "TABLETOP_PLUGIN_PATH"
CAMPAIGN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def migrations_dir() -> Path:
    return repo_root() / "tabletop" / "storage" / "migrations"


def require_database_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    value = env.get(DATABASE_PATH_ENV_VAR)
    if not value:
        raise SystemExit(f"{DATABASE_PATH_ENV_VAR} is required for this command")
    return Path(value).expanduser()


def open_database(environ: Mapping[str, str] | None = None) -> sqlite3.Connection:
    path = require_database_path(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    migrate(conn)
    return conn


def plugin_roots(environ: Mapping[str, str] | None = None) -> tuple[Path, ...]:
    env = os.environ if environ is None else environ
    roots: list[Path] = []
    raw = env.get(PLUGIN_PATH_ENV_VAR)
    if raw:
        roots.extend(Path(part).expanduser() for part in raw.split(os.pathsep) if part)
    systems = repo_root() / "systems"
    if systems.is_dir():
        roots.append(systems)
    # Preserve order while dropping duplicates.
    return tuple(dict.fromkeys(Path(root).resolve() for root in roots))


def load_plugin_registry(environ: Mapping[str, str] | None = None) -> PluginRegistry:
    registry = PluginRegistry()
    for candidate in discover_plugins(plugin_roots(environ)):
        registry.register(load_plugin(candidate))
    return registry


def validate_campaign_id(campaign_id: str) -> str:
    if not CAMPAIGN_ID_PATTERN.fullmatch(campaign_id):
        raise SystemExit(
            f"campaign id {campaign_id!r} must match {CAMPAIGN_ID_PATTERN.pattern}"
        )
    return campaign_id
