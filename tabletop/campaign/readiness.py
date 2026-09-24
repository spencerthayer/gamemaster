"""Campaign readiness report for operator processes."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from tabletop.api.plugin import is_compatible_api_version
from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.store import CampaignStore
from tabletop.importing.store import derived_batch_status, item_state_counts
from tabletop.plugins.discovery import discover_plugins, load_plugin
from tabletop.plugins.registry import PluginRegistry

EXPECTED_SENDER_ENV_VAR = "OMEGA_EXPECTED_SENDER"
CHANNEL_ENV_VAR = "OMEGA_COMMCHANNEL"


def _load_registry() -> PluginRegistry:
    registry = PluginRegistry()
    systems = Path(__file__).resolve().parents[2] / "systems"
    roots = [systems] if systems.is_dir() else []
    for candidate in discover_plugins(tuple(roots)):
        registry.register(load_plugin(candidate))
    return registry


def readiness_report(
    conn: sqlite3.Connection,
    campaign_id: str,
    *,
    environ: dict[str, str] | None = None,
    require_reviewed: bool = False,
    check_persisted: bool = True,
    check_environment: bool = True,
) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    errors: list[str] = []
    warnings: list[str] = []
    notices: list[str] = []
    breakdown = {
        "pending_review": 0,
        "applied": 0,
        "rejected": 0,
        "unapplyable": 0,
    }

    if check_persisted:
        campaign = CampaignStore(conn).get_campaign(campaign_id)
        if campaign is None:
            errors.append(f"campaign {campaign_id!r} not found")
        else:
            _check_persisted_state(
                conn,
                campaign_id,
                campaign,
                errors=errors,
                warnings=warnings,
                notices=notices,
                require_reviewed=require_reviewed,
                breakdown=breakdown,
            )
    if check_environment:
        _check_environment(env, errors=errors, notices=notices)
    has_errors = bool(errors)
    return {
        "campaign_id": campaign_id,
        "errors": errors,
        "warnings": warnings,
        "notices": notices,
        "ok": not has_errors,
        "exit_nonzero": has_errors,
        "import_breakdown": breakdown,
    }


def _check_environment(
    env: dict[str, str], *, errors: list[str], notices: list[str]
) -> None:
    channel = str(env.get(CHANNEL_ENV_VAR) or "").strip() or None
    expected = str(env.get(EXPECTED_SENDER_ENV_VAR) or "").strip() or None
    participant = env.get("TABLETOP_PARTICIPANT")
    if channel and not expected:
        errors.append(
            f"{EXPECTED_SENDER_ENV_VAR} missing for channel {channel}"
        )
    if participant and channel and expected:
        notices.append(
            f"participant {participant} expected sender {expected} on {channel}"
        )
    if channel in {"websocket", "wschat"} and not str(env.get("WS_TOKEN") or "").strip():
        errors.append("player workspace with WebSocket requires WS_TOKEN")


def _check_persisted_state(
    conn: sqlite3.Connection,
    campaign_id: str,
    campaign: dict[str, Any],
    *,
    errors: list[str],
    warnings: list[str],
    notices: list[str],
    require_reviewed: bool,
    breakdown: dict[str, int],
) -> None:
    if campaign.get("archived_at"):
        errors.append("campaign is archived")

    system_id = campaign.get("system_id")
    try:
        registry = _load_registry()
    except Exception as exc:  # pragma: no cover - defensive
        registry = None
        errors.append(f"plugin registry unavailable: {exc}")
    if system_id and (registry is None or not registry.contains(system_id)):
        errors.append(f"missing plugin {system_id!r}")
    elif system_id and registry is not None:
        plugin = registry.get(system_id)
        api_version = plugin.info.api_version
        if not is_compatible_api_version(str(api_version)):
            errors.append(f"incompatible plugin API version {api_version!r}")

    membership = MembershipStore(conn)
    participants = membership.list_participants(campaign_id)
    gm_count = sum(1 for row in participants if row["role"] == "gm")
    player_count = sum(1 for row in participants if row["role"] == "player")
    if gm_count != 1:
        errors.append(
            f"campaign requires exactly one GM participant, found {gm_count}"
        )
    if player_count == 0:
        notices.append("no players yet")

    principals = membership.list_principals(campaign_id)
    seen: set[tuple[str, str]] = set()
    for row in principals:
        key = (row["channel"], row["external_id"])
        if key in seen:
            errors.append(
                f"duplicate principal {row['channel']}:{row['external_id']}"
            )
        seen.add(key)

    open_session = conn.execute(
        "SELECT 1 FROM sessions WHERE campaign_id = ? AND ended_at IS NULL",
        (campaign_id,),
    ).fetchone()
    if open_session is None:
        notices.append("no open session")

    unresolved = int(
        conn.execute(
            "SELECT COUNT(*) FROM events "
            "WHERE campaign_id = ? AND event_type = ?",
            (campaign_id, "canon.contradiction_detected"),
        ).fetchone()[0]
    )
    if unresolved:
        warnings.append(
            f"{unresolved} authoritative unresolved contradiction(s)"
        )

    pending_items = 0
    pending_batches = 0
    try:
        batches = conn.execute(
            "SELECT import_id FROM import_batches WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        batches = []
    for row in batches:
        counts = item_state_counts(conn, row["import_id"])
        for key, value in counts.items():
            breakdown[key] = breakdown.get(key, 0) + value
        pending_items += counts.get("pending_review", 0)
        status = derived_batch_status(conn, row["import_id"])
        if status in {"pending_review", "partially_applied"}:
            pending_batches += 1

    if pending_items or pending_batches:
        message = (
            f"pending import proposals "
            f"(pending_review={breakdown['pending_review']}, "
            f"applied={breakdown['applied']}, "
            f"rejected={breakdown['rejected']}, "
            f"unapplyable={breakdown['unapplyable']})"
        )
        if require_reviewed:
            errors.append(message)
        else:
            warnings.append(message)
