"""Participant-bound sender binding and revocation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tabletop.api.workspace import Workspace
from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.sender_binding import (
    SenderBindingError,
    verify_startup_binding,
    verify_turn_sender,
)
from tabletop.campaign.store import CampaignStore
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate

_REPO = Path(__file__).resolve().parents[2]


def _seed(conn) -> None:
    CampaignStore(conn).create_campaign("night", "Night", "freeform")
    CampaignStore(conn).upsert_entity(
        "night", "e1", "Hero", entity_type="character"
    )
    membership = MembershipStore(conn)
    membership.add_participant("night", "gm", "GM", "gm")
    membership.add_participant("night", "p1", "Pat", "player")
    membership.bind_principal("night", "p1", "telegram", "42")
    membership.bind_principal("night", "gm", "telegram", "7")
    membership.grant_control("night", "p1", "e1", "owner", control_id="c1")


def test_startup_requires_matching_expected_sender(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    conn = connect(db)
    migrate(conn)
    _seed(conn)
    env = {
        "TABLETOP_WORKSPACE": "player",
        "TABLETOP_CAMPAIGN": "night",
        "TABLETOP_PARTICIPANT": "p1",
        "TABLETOP_DATABASE_PATH": str(db),
        "OMEGA_COMMCHANNEL": "telegram",
        "OMEGA_EXPECTED_SENDER": "42",
        "TABLETOP_PLUGIN_PATH": str(_REPO / "systems"),
    }
    runtime = TabletopRuntime.from_environment(_REPO, environ=env)
    assert runtime.participant_id == "p1"
    runtime._connection.close()

    env["OMEGA_EXPECTED_SENDER"] = "99"
    with pytest.raises(SenderBindingError, match="does not match"):
        TabletopRuntime.from_environment(_REPO, environ=env)


def test_startup_requires_expected_sender_when_channel_set(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    conn = connect(db)
    migrate(conn)
    _seed(conn)
    env = {
        "TABLETOP_WORKSPACE": "player",
        "TABLETOP_CAMPAIGN": "night",
        "TABLETOP_PARTICIPANT": "p1",
        "TABLETOP_DATABASE_PATH": str(db),
        "OMEGA_COMMCHANNEL": "telegram",
        "TABLETOP_PLUGIN_PATH": str(_REPO / "systems"),
    }
    with pytest.raises(SenderBindingError, match="OMEGA_EXPECTED_SENDER"):
        TabletopRuntime.from_environment(_REPO, environ=env)


def test_startup_requires_campaign_env(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    conn = connect(db)
    migrate(conn)
    _seed(conn)
    # Write active campaign file so resolution would otherwise succeed.
    (db.parent / f"{db.name}.active-campaign").write_text("night\n", encoding="utf-8")
    env = {
        "TABLETOP_WORKSPACE": "player",
        "TABLETOP_PARTICIPANT": "p1",
        "TABLETOP_DATABASE_PATH": str(db),
        "OMEGA_COMMCHANNEL": "telegram",
        "OMEGA_EXPECTED_SENDER": "42",
        "TABLETOP_PLUGIN_PATH": str(_REPO / "systems"),
    }
    with pytest.raises(SenderBindingError, match="TABLETOP_CAMPAIGN"):
        TabletopRuntime.from_environment(_REPO, environ=env)


def test_turn_sender_mismatch_and_rebind(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    conn = connect(db)
    migrate(conn)
    _seed(conn)
    env = {
        "OMEGA_COMMCHANNEL": "telegram",
        "OMEGA_EXPECTED_SENDER": "42",
    }
    assert (
        verify_turn_sender(
            conn,
            workspace=Workspace.PLAYER,
            campaign_id="night",
            participant_id="p1",
            authenticated_sender="42",
            environ=env,
        )
        is None
    )
    assert (
        verify_turn_sender(
            conn,
            workspace=Workspace.PLAYER,
            campaign_id="night",
            participant_id="p1",
            authenticated_sender="99",
            environ=env,
        )
        == "sender_mismatch"
    )
    assert (
        verify_turn_sender(
            conn,
            workspace=Workspace.PLAYER,
            campaign_id="night",
            participant_id="p1",
            authenticated_sender=None,
            environ=env,
        )
        == "sender_required"
    )

    MembershipStore(conn).unbind_principal("night", "p1", "telegram")
    MembershipStore(conn).bind_principal("night", "p1", "telegram", "43")
    # Stale expected sender 42 is rejected by active-binding check.
    assert (
        verify_turn_sender(
            conn,
            workspace=Workspace.PLAYER,
            campaign_id="night",
            participant_id="p1",
            authenticated_sender="42",
            environ=env,
        )
        == "sender_mismatch"
    )
    # New principal 43 is also rejected until process env is regenerated.
    assert (
        verify_turn_sender(
            conn,
            workspace=Workspace.PLAYER,
            campaign_id="night",
            participant_id="p1",
            authenticated_sender="43",
            environ=env,
        )
        == "sender_mismatch"
    )


def test_removed_participant_is_not_active(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    conn = connect(db)
    migrate(conn)
    _seed(conn)
    MembershipStore(conn).remove_participant("night", "p1")
    assert (
        verify_turn_sender(
            conn,
            workspace=Workspace.PLAYER,
            campaign_id="night",
            participant_id="p1",
            authenticated_sender="42",
            environ={"OMEGA_COMMCHANNEL": "telegram", "OMEGA_EXPECTED_SENDER": "42"},
        )
        == "participant_not_active"
    )


def test_gm_process_rejects_player_sender(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    conn = connect(db)
    migrate(conn)
    _seed(conn)
    assert (
        verify_turn_sender(
            conn,
            workspace=Workspace.CAMPAIGN,
            campaign_id="night",
            participant_id="gm",
            authenticated_sender="42",
            environ={"OMEGA_COMMCHANNEL": "telegram", "OMEGA_EXPECTED_SENDER": "7"},
        )
        == "gm_required"
    )
    assert (
        verify_turn_sender(
            conn,
            workspace=Workspace.CAMPAIGN,
            campaign_id="night",
            participant_id="gm",
            authenticated_sender="7",
            environ={"OMEGA_COMMCHANNEL": "telegram", "OMEGA_EXPECTED_SENDER": "7"},
        )
        is None
    )


def test_verify_startup_binding_direct(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    conn = connect(db)
    migrate(conn)
    _seed(conn)
    verify_startup_binding(
        conn,
        workspace=Workspace.PLAYER,
        campaign_id="night",
        participant_id="p1",
        environ={
            "TABLETOP_CAMPAIGN": "night",
            "OMEGA_COMMCHANNEL": "telegram",
            "OMEGA_EXPECTED_SENDER": "42",
        },
    )
