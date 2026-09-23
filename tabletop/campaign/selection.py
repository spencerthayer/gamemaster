"""Active campaign selection file helpers."""

from __future__ import annotations

from pathlib import Path

ACTIVE_CAMPAIGN_FILENAME = "active-campaign"


def active_campaign_path(database_path: Path) -> Path:
    return Path(database_path).expanduser().resolve().parent / ACTIVE_CAMPAIGN_FILENAME


def read_active_campaign_file(database_path: Path) -> str | None:
    """Return the selected campaign id from ``<db parent>/active-campaign``.

    The file must contain a single campaign slug and nothing else.
    """
    path = active_campaign_path(database_path)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if len(lines) != 1:
        raise ValueError(f"{path} must contain exactly one campaign id line")
    campaign_id = lines[0].strip()
    if not campaign_id:
        raise ValueError(f"{path} is empty")
    if "/" in campaign_id or "\\" in campaign_id or ".." in campaign_id:
        raise ValueError(f"{path} contains an invalid campaign id")
    return campaign_id


def write_active_campaign_file(database_path: Path, campaign_id: str) -> Path:
    path = active_campaign_path(database_path)
    path.write_text(f"{campaign_id}\n", encoding="utf-8")
    return path


def clear_active_campaign_file(database_path: Path, campaign_id: str) -> None:
    """Remove the selection file when it names ``campaign_id``."""
    path = active_campaign_path(database_path)
    if not path.is_file():
        return
    try:
        current = read_active_campaign_file(database_path)
    except ValueError:
        return
    if current == campaign_id:
        path.unlink()
