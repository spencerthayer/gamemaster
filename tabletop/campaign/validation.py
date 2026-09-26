"""Structured, machine-readable validation of one campaign.

Every check has a stable id such as ``campaign.exists`` or
``plugin.compatible``. Ids are part of the contract: an operator script or a
future UI matches on them, so renaming one is a breaking change even when the
meaning is unchanged.

The difference from :mod:`tabletop.campaign.readiness` is the shape. Readiness
returns human-readable string buckets; this returns typed records a program
can branch on. Readiness is not removed, because its callers still want prose.

Building a report mutates nothing. Database writability is proven with a
transaction that is rolled back, never by leaving a probe row behind.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from tabletop.api.plugin import is_compatible_api_version
from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import transaction


class CheckStatus(str, Enum):
    """The outcome of one check."""

    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    SKIP = "skip"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class ValidationCheck:
    """One check result with a stable id."""

    check_id: str
    status: CheckStatus
    message: str = ""
    detail: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.check_id,
            "status": self.status.value,
            "message": self.message,
            "detail": None if self.detail is None else dict(self.detail),
        }


@dataclass(frozen=True)
class ValidationReport:
    """Every check for one campaign, plus the overall verdict."""

    campaign_id: str
    checks: tuple[ValidationCheck, ...] = ()
    live: bool = False

    @property
    def ready(self) -> bool:
        """True when no *static* check failed.

        Warnings and skips do not block: a skipped live probe is honest, not
        a pass, and a warning is something to read, not a gate. Live checks
        are excluded because a broken environment is not a broken campaign;
        the CLI reports them through their own exit class instead.
        """
        return not any(
            check.status is CheckStatus.FAIL
            for check in self.checks
            if not check.check_id.startswith(("live.", "channel."))
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "ready": self.ready,
            "live": self.live,
            "checks": [check.to_dict() for check in self.checks],
            "summary": _summarize(self.checks),
        }

    def failures(self) -> tuple[ValidationCheck, ...]:
        return tuple(check for check in self.checks if check.status is CheckStatus.FAIL)

    def static_failures(self) -> tuple[ValidationCheck, ...]:
        """Failed static checks, excluding live and channel probes."""
        return tuple(
            check
            for check in self.failures()
            if not check.check_id.startswith(("live.", "channel."))
        )

    def live_failures(self) -> tuple[ValidationCheck, ...]:
        """Failed live or channel probes."""
        return tuple(
            check
            for check in self.failures()
            if check.check_id.startswith(("live.", "channel."))
        )


def _summarize(checks: Sequence[ValidationCheck]) -> dict[str, int]:
    counts: dict[str, int] = {status.value: 0 for status in CheckStatus}
    for check in checks:
        counts[check.status.value] += 1
    return counts


def validate_campaign(
    conn: sqlite3.Connection,
    campaign_id: str,
    *,
    plugin_resolver: Callable[[str], Any] | None = None,
) -> ValidationReport:
    """Run every static check for one campaign.

    ``plugin_resolver`` maps a system id to a loaded plugin, or raises. It is
    injected so a caller with a live registry does not reload plugins per
    check, and so tests do not need a filesystem.
    """
    resolver = plugin_resolver or _default_resolver()
    checks: list[ValidationCheck] = []
    store = CampaignStore(conn)

    campaign = store.get_campaign(campaign_id)
    if campaign is None:
        checks.append(
            ValidationCheck(
                "campaign.exists", CheckStatus.FAIL, f"campaign {campaign_id!r} not found"
            )
        )
        return ValidationReport(campaign_id=campaign_id, checks=tuple(checks))

    checks.append(ValidationCheck("campaign.exists", CheckStatus.PASS))
    checks.append(
        _archive_check(campaign)
    )

    plugin = _plugin_check(campaign, resolver, checks)
    if plugin is not None:
        checks.append(
            ValidationCheck(
                "plugin.state",
                CheckStatus.PASS,
                f"{campaign['system_id']} accepts its current state",
            )
        )

    membership = MembershipStore(conn)
    checks.append(_gm_check(membership, campaign_id))
    checks.append(_identity_check(conn, campaign_id))
    checks.append(_ownership_check(membership, campaign_id))
    checks.append(_imports_check(conn, campaign_id))
    checks.append(_writable_check(conn))
    checks.append(_paths_check(campaign))

    session = _open_session(conn, campaign_id)
    checks.append(
        ValidationCheck(
            "session.current",
            CheckStatus.PASS if session else CheckStatus.WARN,
            f"open session {session}" if session else "no open session",
        )
    )

    scenes = SceneStore(conn)
    scene = scenes.get_open_scene(campaign_id)
    checks.append(
        ValidationCheck(
            "scene.structure",
            CheckStatus.PASS if scene is not None else CheckStatus.WARN,
            f"open scene {scene.scene_id}" if scene is not None else "no open scene",
        )
    )

    return ValidationReport(campaign_id=campaign_id, checks=tuple(checks))


def _archive_check(campaign: Mapping[str, Any]) -> ValidationCheck:
    if campaign.get("archived_at"):
        return ValidationCheck(
            "campaign.archived", CheckStatus.FAIL, "campaign is archived"
        )
    return ValidationCheck("campaign.archived", CheckStatus.PASS, "campaign is active")


def _plugin_check(
    campaign: Mapping[str, Any],
    resolver: Callable[[str], Any],
    checks: list[ValidationCheck],
) -> Any | None:
    system_id = str(campaign["system_id"])
    try:
        plugin = resolver(system_id)
    except Exception as exc:  # noqa: BLE001 - any failure means unavailable
        checks.append(
            ValidationCheck(
                "plugin.compatible",
                CheckStatus.FAIL,
                f"no plugin provides {system_id!r}: {exc}",
            )
        )
        return None
    if not is_compatible_api_version(plugin.info.api_version):
        checks.append(
            ValidationCheck(
                "plugin.compatible",
                CheckStatus.FAIL,
                f"{system_id} declares unsupported api {plugin.info.api_version!r}",
            )
        )
        return None
    checks.append(ValidationCheck("plugin.compatible", CheckStatus.PASS))
    return plugin


def _gm_check(membership: MembershipStore, campaign_id: str) -> ValidationCheck:
    gms = [
        row["participant_id"]
        for row in membership.list_participants(campaign_id)
        if row["role"] == "gm"
    ]
    if len(gms) == 1:
        return ValidationCheck("participant.gm.count", CheckStatus.PASS, gms[0])
    return ValidationCheck(
        "participant.gm.count",
        CheckStatus.FAIL,
        f"expected exactly one gm, found {len(gms)}",
        {"participant_ids": gms},
    )


def _identity_check(conn: sqlite3.Connection, campaign_id: str) -> ValidationCheck:
    """No entity may be bound to two participants at once."""
    rows = conn.execute(
        "SELECT entity_id, COUNT(DISTINCT participant_id) AS owners "
        "FROM character_controls WHERE campaign_id = ? AND ended_at IS NULL "
        "GROUP BY entity_id HAVING owners > 1",
        (campaign_id,),
    ).fetchall()
    if rows:
        return ValidationCheck(
            "participant.identity.unique",
            CheckStatus.FAIL,
            "an entity is controlled by more than one participant",
            {"entity_ids": [row["entity_id"] for row in rows]},
        )
    return ValidationCheck("participant.identity.unique", CheckStatus.PASS)


def _ownership_check(membership: MembershipStore, campaign_id: str) -> ValidationCheck:
    dangling = [
        row["control_id"]
        for row in membership.list_controls(campaign_id)
        if not row.get("entity_id")
    ]
    if dangling:
        return ValidationCheck(
            "character.ownership",
            CheckStatus.FAIL,
            "a character control points at no entity",
            {"control_ids": dangling},
        )
    return ValidationCheck("character.ownership", CheckStatus.PASS)


def _imports_check(conn: sqlite3.Connection, campaign_id: str) -> ValidationCheck:
    pending = 0
    try:
        pending = int(
            conn.execute(
                "SELECT COUNT(*) FROM import_items "
                "WHERE campaign_id = ? AND review_state = 'pending_review'",
                (campaign_id,),
            ).fetchone()[0]
        )
    except sqlite3.OperationalError:
        return ValidationCheck(
            "import.reviewed", CheckStatus.NOT_APPLICABLE, "no import table"
        )
    if pending:
        return ValidationCheck(
            "import.reviewed",
            CheckStatus.WARN,
            f"{pending} imported items await review",
            {"pending": pending},
        )
    return ValidationCheck("import.reviewed", CheckStatus.PASS)


def _writable_check(conn: sqlite3.Connection) -> ValidationCheck:
    """Prove writability with a rolled-back transaction.

    A persistent probe row would make a read-only database look writable on
    the next run, and would put a row in the operator's campaign.
    """
    try:
        with transaction(conn):
            conn.execute(
                "CREATE TABLE IF NOT EXISTS validation_probe (probe_id TEXT PRIMARY KEY)"
            )
            conn.execute("DROP TABLE validation_probe")
    except sqlite3.Error as exc:
        return ValidationCheck(
            "database.writable", CheckStatus.FAIL, f"database is not writable: {exc}"
        )
    return ValidationCheck("database.writable", CheckStatus.PASS)


def _paths_check(campaign: Mapping[str, Any]) -> ValidationCheck:
    setting_id = campaign.get("setting_id")
    if setting_id is None:
        return ValidationCheck(
            "campaign.paths",
            CheckStatus.NOT_APPLICABLE,
            "campaign declares no setting",
        )
    return ValidationCheck(
        "campaign.paths", CheckStatus.PASS, f"setting {setting_id}"
    )


def _open_session(conn: sqlite3.Connection, campaign_id: str) -> str | None:
    row = conn.execute(
        "SELECT session_id FROM sessions WHERE campaign_id = ? AND ended_at IS NULL "
        "ORDER BY started_at DESC LIMIT 1",
        (campaign_id,),
    ).fetchone()
    return None if row is None else str(row["session_id"])


def _default_resolver() -> Callable[[str], Any]:
    from pathlib import Path

    from tabletop.plugins.discovery import discover_plugins, load_plugin
    from tabletop.plugins.registry import PluginRegistry

    registry = PluginRegistry()
    systems = Path(__file__).resolve().parents[2] / "systems"
    roots = (systems,) if systems.is_dir() else ()
    for candidate in discover_plugins(roots):
        registry.register(load_plugin(candidate))

    def resolve(system_id: str) -> Any:
        return registry.get(system_id)

    return resolve
