"""Participant-bound process sender verification (ADR 0013)."""

from __future__ import annotations

import os
import sqlite3
from typing import Mapping

from tabletop.api.workspace import Workspace
from tabletop.campaign.membership import MembershipStore

EXPECTED_SENDER_ENV_VAR = "OMEGA_EXPECTED_SENDER"
CHANNEL_ENV_VAR = "OMEGA_COMMCHANNEL"
CAMPAIGN_ENV_VAR = "TABLETOP_CAMPAIGN"
LOCAL_OPERATOR_ENV_VAR = "TABLETOP_LOCAL_OPERATOR"


class SenderBindingError(RuntimeError):
    """Process refused to start or turn rejected for sender binding."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def expected_sender_from_environ(environ: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    value = env.get(EXPECTED_SENDER_ENV_VAR)
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def channel_from_environ(environ: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    value = env.get(CHANNEL_ENV_VAR)
    if value is None or not str(value).strip():
        return None
    return str(value).strip().lower()


def is_local_operator(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(LOCAL_OPERATOR_ENV_VAR, "")).strip().lower() in {
        "1",
        "true",
        "yes",
    }


def bound_external_id(
    conn: sqlite3.Connection,
    campaign_id: str,
    participant_id: str,
    channel: str,
) -> str | None:
    row = conn.execute(
        "SELECT external_id FROM participant_principals "
        "WHERE campaign_id = ? AND participant_id = ? AND channel = ?",
        (campaign_id, participant_id, channel),
    ).fetchone()
    if row is None:
        return None
    return str(row["external_id"])


def verify_startup_binding(
    conn: sqlite3.Connection,
    *,
    workspace: Workspace,
    campaign_id: str | None,
    participant_id: str | None,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Refuse startup when a participant-bound channel process is misconfigured."""

    env = os.environ if environ is None else environ
    if not participant_id:
        return
    if not campaign_id:
        raise SenderBindingError(
            "campaign_required",
            "TABLETOP_CAMPAIGN is required for a participant-bound process",
        )
    if CAMPAIGN_ENV_VAR not in env or not str(env.get(CAMPAIGN_ENV_VAR, "")).strip():
        raise SenderBindingError(
            "campaign_env_required",
            "TABLETOP_CAMPAIGN must be present in the process environment",
        )

    participants = {
        row["participant_id"]: row
        for row in MembershipStore(conn).list_participants(campaign_id)
    }
    participant = participants.get(participant_id)
    if participant is None:
        raise SenderBindingError(
            "participant_not_found",
            f"participant {participant_id!r} is not in campaign {campaign_id!r}",
        )

    if workspace is Workspace.PLAYER and participant["role"] != "player":
        raise SenderBindingError(
            "player_role_required",
            "Workspace.PLAYER requires a player participant",
        )
    if workspace is Workspace.CAMPAIGN and participant["role"] != "gm":
        # GM process may bind the GM participant on campaign workspace.
        raise SenderBindingError(
            "gm_role_required",
            "campaign workspace participant binding requires the GM participant",
        )

    channel = channel_from_environ(env)
    if channel is None:
        return
    expected = expected_sender_from_environ(env)
    if expected is None:
        raise SenderBindingError(
            "expected_sender_required",
            "OMEGA_EXPECTED_SENDER is required for an externally reachable channel",
        )
    bound = bound_external_id(conn, campaign_id, participant_id, channel)
    if bound is None:
        raise SenderBindingError(
            "principal_unbound",
            f"no principal binding for {participant_id!r} on {channel}",
        )
    if bound != expected:
        raise SenderBindingError(
            "expected_sender_mismatch",
            "OMEGA_EXPECTED_SENDER does not match participant_principals",
        )


def verify_turn_sender(
    conn: sqlite3.Connection,
    *,
    workspace: Workspace,
    campaign_id: str | None,
    participant_id: str | None,
    authenticated_sender: str | None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return an error code when the turn must not reach a skill, else None."""

    env = os.environ if environ is None else environ
    if is_local_operator(env):
        return None
    if not participant_id or not campaign_id:
        return None

    channel = channel_from_environ(env)
    if channel is None and expected_sender_from_environ(env) is None:
        # No externally configured channel gate in this process.
        if authenticated_sender is None:
            return None
        # Defense in depth when a sender ContextVar is present without channel.
        bound_ids = {
            row["external_id"]
            for row in MembershipStore(conn).list_principals(campaign_id)
            if row["participant_id"] == participant_id
        }
        if authenticated_sender not in bound_ids:
            return "sender_mismatch"
        return None

    if authenticated_sender is None:
        return "sender_required"

    expected = expected_sender_from_environ(env)
    if expected is not None and authenticated_sender != expected:
        # Regenerated binding is rejected until the process env is regenerated.
        if workspace is Workspace.CAMPAIGN:
            return "gm_required"
        return "sender_mismatch"

    participants = {
        row["participant_id"]: row
        for row in MembershipStore(conn).list_participants(campaign_id)
    }
    if participant_id not in participants:
        return "participant_not_active"

    if channel is not None:
        bound = bound_external_id(conn, campaign_id, participant_id, channel)
        if bound is None:
            return "participant_not_active"
        if authenticated_sender != bound:
            if workspace is Workspace.CAMPAIGN:
                return "gm_required"
            return "sender_mismatch"
    return None
