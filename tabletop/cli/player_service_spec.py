"""Canonical structural specification for generated Omega player services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tabletop.campaign.membership import validate_participant_id


@dataclass(frozen=True)
class PlayerServiceSpec:
    """Compose fields shared by every generated player service."""

    image: str = "${OMEGA_IMAGE}"
    init: bool = True
    restart: str = "unless-stopped"
    security_options: tuple[str, ...] = ("no-new-privileges:true",)
    command: tuple[str, ...] = (
        "commchannel=${OMEGA_COMMCHANNEL:-}",
        "provider=${OMEGA_PROVIDER}",
        "embeddingprovider=${OMEGA_EMBEDDING_PROVIDER}",
        "securityPolicyPath=${OMEGA_SECURITY_POLICY_PATH}",
    )
    credential_slots: tuple[str, ...] = (
        "ASI_API_KEY",
        "OMEGA_AUTH_SECRET",
        "TELEGRAM_TOKEN",
        "TG_BOT_TOKEN",
        "SLACK_TOKEN",
        "SL_BOT_TOKEN",
        "MATTERMOST_TOKEN",
        "MM_BOT_TOKEN",
        "IRC_TOKEN",
        "WS_TOKEN",
    )

    def service(self, participant_id: str) -> dict[str, Any]:
        """Return one channel-neutral service definition."""

        participant_id = validate_participant_id(participant_id)
        return {
            "image": self.image,
            "init": self.init,
            "restart": self.restart,
            "security_opt": list(self.security_options),
            "environment": {
                "ASI_API_KEY": "${ASI_API_KEY:-}",
                "IMPORT_KB_ON_START": "0",
                "IRC_TOKEN": "${IRC_TOKEN:-}",
                "MATTERMOST_TOKEN": "${MATTERMOST_TOKEN:-}",
                "MM_BOT_TOKEN": "${MM_BOT_TOKEN:-${MATTERMOST_TOKEN:-}}",
                "OMEGA_AUTH_SECRET": "${OMEGA_AUTH_SECRET:-}",
                "OMEGA_COMMCHANNEL": "${OMEGA_COMMCHANNEL:-}",
                "OMEGA_EXPECTED_SENDER": "${OMEGA_EXPECTED_SENDER:-}",
                "SLACK_TOKEN": "${SLACK_TOKEN:-}",
                "SL_BOT_TOKEN": "${SL_BOT_TOKEN:-${SLACK_TOKEN:-}}",
                "TABLETOP_CAMPAIGN": "${TABLETOP_CAMPAIGN}",
                "TABLETOP_CAMPAIGN_PATHS": "${TABLETOP_CONTAINER_CAMPAIGNS_PATH}",
                "TABLETOP_CAMPAIGNS_MOUNT": "${TABLETOP_CAMPAIGNS_MOUNT}",
                "TABLETOP_DATABASE_PATH": "${TABLETOP_DATABASE_PATH}",
                "TABLETOP_LIBRARY_MOUNT": "${TABLETOP_LIBRARY_MOUNT}",
                "TABLETOP_PARTICIPANT": "${TABLETOP_PARTICIPANT}",
                "TABLETOP_PLUGIN_MOUNT": "${TABLETOP_PLUGIN_MOUNT}",
                "TABLETOP_PLUGIN_PATH": "${TABLETOP_CONTAINER_PLUGIN_PATH}",
                "TABLETOP_WORKSPACE": "player",
                "TELEGRAM_TOKEN": "${TELEGRAM_TOKEN:-}",
                "TG_BOT_TOKEN": "${TG_BOT_TOKEN:-${TELEGRAM_TOKEN:-}}",
                "WS_TOKEN": "${WS_TOKEN:-}",
            },
            "command": list(self.command),
            "volumes": [
                {
                    "type": "volume",
                    "source": f"omega-memory-{participant_id}",
                    "target": "${OMEGA_MEMORY_PATH}",
                },
                {
                    "type": "volume",
                    "source": "tabletop-state",
                    "target": "${TABLETOP_STATE_PATH}",
                },
                {
                    "type": "bind",
                    "source": "${TABLETOP_PLUGIN_HOST_PATH}",
                    "target": "${TABLETOP_PLUGIN_MOUNT}",
                    "read_only": True,
                },
                {
                    "type": "bind",
                    "source": "${TABLETOP_LIBRARY_HOST_PATH}",
                    "target": "${TABLETOP_LIBRARY_MOUNT}",
                    "read_only": True,
                },
                {
                    "type": "bind",
                    "source": "${TABLETOP_CAMPAIGNS_HOST_PATH}",
                    "target": "${TABLETOP_CAMPAIGNS_MOUNT}",
                },
            ],
        }


PLAYER_SERVICE_SPEC = PlayerServiceSpec()
PLAYER_CREDENTIAL_SLOTS = frozenset(PLAYER_SERVICE_SPEC.credential_slots)


def player_compose_definition(participant_id: str) -> dict[str, Any]:
    """Return a complete Compose override mapping for one participant."""

    participant_id = validate_participant_id(participant_id)
    return {
        "services": {f"omega-player-{participant_id}": PLAYER_SERVICE_SPEC.service(participant_id)},
        "volumes": {
            f"omega-memory-{participant_id}": {
                "name": f"gamemaster-omega-memory-{participant_id}"
            }
        },
    }


__all__ = [
    "PLAYER_CREDENTIAL_SLOTS",
    "PLAYER_SERVICE_SPEC",
    "PlayerServiceSpec",
    "player_compose_definition",
]
