"""CLI helpers for constructing an operator TabletopRuntime."""

from __future__ import annotations

import os
from typing import Mapping

from tabletop.api.workspace import Workspace
from tabletop.cli.util import open_database, repo_root, require_database_path
from tabletop.campaign.selection import read_active_campaign_file
from tabletop.campaign.store import CampaignStore
from tabletop.runtime import TabletopRuntime


def resolve_campaign_id(
    *,
    campaign_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve which campaign a command applies to.

    The selection file lives beside the database and is shared by every
    database in that directory, so it can name a campaign that does not exist
    here. Trusting it blindly makes every command fail with a confusing
    "campaign not found". A selection that does not resolve in this database
    is stale, and the single-campaign fallback below is more useful than a
    hard failure.
    """
    env = os.environ if environ is None else environ
    if campaign_id:
        return campaign_id
    explicit = env.get("TABLETOP_CAMPAIGN")
    database_path = require_database_path(env)
    conn = open_database(env)
    try:
        # Archived campaigns are included here. A command that targets one
        # must fail with "archived", not with "not found", and excluding them
        # would turn a specific, actionable error into a vague one.
        available = [
            str(row["campaign_id"])
            for row in CampaignStore(conn).list_campaigns(include_archived=True)
        ]
        active = [
            str(row["campaign_id"])
            for row in CampaignStore(conn).list_campaigns(include_archived=False)
        ]
    finally:
        conn.close()
    if not explicit:
        try:
            selected = read_active_campaign_file(database_path)
        except ValueError:
            # A malformed selection file should not strand every command.
            selected = None
        if selected and selected in available:
            return selected
    elif explicit in available:
        return explicit
    elif explicit:
        raise SystemExit(
            f"campaign {explicit!r} not found in {database_path}"
        )

    if len(active) == 1:
        return active[0]
    if not active:
        raise SystemExit("no campaign selected; run campaign select <id>")
    raise SystemExit("multiple campaigns exist; run campaign select <id>")


def open_operator_runtime(
    *,
    campaign_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> TabletopRuntime:
    env = dict(os.environ if environ is None else environ)
    env.setdefault("TABLETOP_WORKSPACE", Workspace.CAMPAIGN.value)
    resolved = resolve_campaign_id(campaign_id=campaign_id, environ=env)
    env["TABLETOP_CAMPAIGN"] = resolved
    require_database_path(env)
    return TabletopRuntime.from_environment(repo_root(), environ=env)
