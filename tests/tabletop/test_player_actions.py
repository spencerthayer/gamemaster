"""Player resolve-action requires character control."""

from __future__ import annotations

from pathlib import Path

from tabletop.api.workspace import Workspace
from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.store import CampaignStore
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate


def test_player_resolve_rejects_uncontrolled_actor(tmp_path: Path) -> None:
    conn = connect(tmp_path / "player.db")
    migrate(conn)
    store = CampaignStore(conn)
    store.create_campaign("night", "Night", "freeform")
    store.upsert_entity("night", "ada", "Ada", entity_type="character")
    store.upsert_entity("night", "bo", "Bo", entity_type="character")
    membership = MembershipStore(conn)
    membership.add_participant("night", "p1", "Ada", "player")
    membership.grant_control("night", "p1", "ada", "owner")
    runtime = TabletopRuntime(
        tmp_path,
        workspace=Workspace.PLAYER,
        plugin_roots=[],
        active_campaign="night",
        connection=conn,
        participant_id="p1",
    )
    try:
        denied = runtime.resolve_action(
            '{"actor":{"id":"bo"},"verb":"look","action_type":"look"}'
        )
        assert denied["ok"] is False
        assert denied["error"]["code"] == "actor_not_controlled"
    finally:
        runtime.shutdown()
        conn.close()
