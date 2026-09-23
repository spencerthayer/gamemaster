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
    env = os.environ if environ is None else environ
    if campaign_id:
        return campaign_id
    explicit = env.get("TABLETOP_CAMPAIGN")
    if explicit:
        return explicit
    database_path = require_database_path(env)
    selected = read_active_campaign_file(database_path)
    if selected:
        return selected
    conn = open_database(env)
    try:
        campaigns = CampaignStore(conn).list_campaigns(include_archived=False)
    finally:
        conn.close()
    if len(campaigns) == 1:
        return str(campaigns[0]["campaign_id"])
    if not campaigns:
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
