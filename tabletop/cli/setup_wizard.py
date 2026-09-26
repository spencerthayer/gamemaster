"""Interactive campaign setup.

The wizard is prompts and nothing else. It builds the same
``CampaignSetupManifest`` a ``campaign.setup.yaml`` would produce and calls
the same plan and apply services, so an interactive run and a declarative run
cannot drift apart or end up with two implementations.

It never starts Docker or any other process. Setup configures and validates;
launching a campaign is a separate, explicit command.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from tabletop.campaign.setup import (
    CampaignSetupManifest,
    SetupContent,
    SetupManifestError,
    SetupParticipant,
    SetupScene,
    parse_setup_manifest,
)

#: Systems a new campaign may declare. Kept in step with the built-ins so the
#: wizard cannot produce a manifest that names a system nothing implements.
KNOWN_SYSTEMS = ("freeform", "dnd5e", "gurps")


class SetupCancelled(Exception):
    """The operator chose not to continue."""


@dataclass(frozen=True)
class WizardPrompts:
    """Injectable input and output so the wizard is testable without a TTY."""

    ask: Callable[[str], str]
    say: Callable[[str], None] = print

    def question(self, prompt: str, *, default: str | None = None) -> str:
        suffix = f" [{default}]" if default else ""
        answer = self.ask(f"{prompt}{suffix}: ").strip()
        if not answer:
            if default is None:
                raise SetupCancelled(f"no answer for {prompt!r}")
            return default
        return answer

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        answer = self.question(f"{prompt} (y/n)", default="y" if default else "n").lower()
        if answer not in {"y", "yes", "n", "no"}:
            raise SetupCancelled(f"expected yes or no, got {answer!r}")
        return answer in {"y", "yes"}


def run_wizard(prompts: WizardPrompts) -> CampaignSetupManifest:
    """Ask for a campaign and return a validated manifest.

    Validation runs before returning, so a wizard run that produced an invalid
    manifest would fail here rather than at apply time.
    """

    prompts.say("Setting up a new campaign.")
    campaign_id = _ask_id(prompts, "Campaign id", "black-company")
    name = prompts.question("Campaign name", default=campaign_id)
    system_id = prompts.question(
        f"System ({', '.join(KNOWN_SYSTEMS)})", default="freeform"
    )
    if system_id not in KNOWN_SYSTEMS:
        raise SetupManifestError(
            f"unknown system {system_id!r}; choose one of {', '.join(KNOWN_SYSTEMS)}"
        )

    participants: list[SetupParticipant] = []
    seen: set[str] = set()
    while True:
        participant_id = _ask_id(prompts, "Participant id (blank to finish)", "")
        if not participant_id:
            break
        if participant_id in seen:
            raise SetupManifestError(f"duplicate participant {participant_id!r}")
        seen.add(participant_id)
        display_name = prompts.question("Display name", default=participant_id)
        role = prompts.question("Role (gm/player)", default="player")
        if role not in {"gm", "player"}:
            raise SetupManifestError(f"role must be gm or player, got {role!r}")
        principals: list[tuple[str, str]] = []
        while prompts.confirm(f"Bind a channel principal for {participant_id}?"):
            channel = prompts.question("Channel", default="websocket")
            external_id = prompts.question("External id")
            principals.append((channel, external_id))
        participants.append(
            SetupParticipant(
                participant_id=participant_id,
                display_name=display_name,
                role=role,
                principals=tuple(principals),
            )
        )

    characters: list[tuple[str, str, str | None]] = []
    if participants:
        while True:
            entity_id = _ask_id(prompts, "Character id (blank to finish)", "")
            if not entity_id:
                break
            character_name = prompts.question("Character name", default=entity_id)
            characters.append((entity_id, character_name, None))

    scene: SetupScene | None = None
    if prompts.confirm("Define a starting scene?", default=True):
        scene_id = _ask_id(prompts, "Scene id", "scene-1")
        scene_name = prompts.question("Scene name", default=scene_id)
        scene = SetupScene(scene_id=scene_id, name=scene_name)

    payload = {
        "campaign_id": campaign_id,
        "name": name,
        "system_id": system_id,
        "participants": [
            {
                "participant_id": p.participant_id,
                "display_name": p.display_name,
                "role": p.role,
                "character_ids": list(p.character_ids),
                "principals": [list(item) for item in p.principals],
            }
            for p in participants
        ],
        "characters": [
            {"entity_id": item[0], "name": item[1], "participant_id": item[2]}
            for item in characters
        ],
        "content": [],
        "starting_scene": (
            None if scene is None else {"scene_id": scene.scene_id, "name": scene.name}
        ),
    }
    # The wizard's base directory is the current one: it names no content, so
    # nothing is resolved against a manifest that does not exist.
    return parse_setup_manifest(payload, base_dir=Path.cwd())


def _ask_id(prompts: WizardPrompts, prompt: str, default: str) -> str:
    """Ask for a slug, allowing a blank answer only when a default exists."""
    from tabletop.campaign.setup import _ID_PATTERN

    while True:
        answer = prompts.ask(f"{prompt}: ").strip() or default
        if not answer:
            # A blank answer with no default means the operator is done.
            return ""
        if not _ID_PATTERN.fullmatch(answer):
            prompts.say(f"  {answer!r} must match {_ID_PATTERN.pattern}")
            continue
        return answer
