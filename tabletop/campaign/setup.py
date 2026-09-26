"""Declarative campaign setup.

A ``campaign.setup.yaml`` describes a campaign that does not exist yet:
who is in it, who plays them, what content it uses, and where play starts.
Loading one is strictly read-only. It opens no database connection, performs
no network access, and executes nothing it names.

The format is strict. An unknown field is an error rather than a silent
omission, because a typo in ``system_id`` would otherwise configure a campaign
the operator did not intend to create.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

#: Fields that look like credentials rather than configuration. A setup file
#: that carries a token is a mistake, and a mistake worth failing on.
_CREDENTIAL_KEY_PATTERN = re.compile(
    r"(?i)(token|secret|password|passwd|api[_-]?key|credential|private[_-]?key)"
)

_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

#: Suffixes that imply runnable content. Setup may reference data, never code.
_EXECUTABLE_SUFFIXES = frozenset({".py", ".sh", ".bash", ".exe", ".dll", ".so", ".bat", ".ps1"})

_REQUIRED_TOP_LEVEL = frozenset({"campaign_id", "name", "system_id"})
_ALLOWED_TOP_LEVEL = _REQUIRED_TOP_LEVEL | {
    "setting_id",
    "participants",
    "characters",
    "content",
    "starting_state",
    "starting_scene",
    "game_time",
}


class SetupManifestError(ValueError):
    """A setup manifest is malformed or names something unsafe."""


@dataclass(frozen=True, kw_only=True)
class SetupParticipant:
    """One person in the campaign, and which seat they take."""

    participant_id: str
    display_name: str
    role: str = "player"
    character_ids: tuple[str, ...] = ()
    principals: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "participant_id": self.participant_id,
            "display_name": self.display_name,
            "role": self.role,
            "character_ids": list(self.character_ids),
            "principals": [list(item) for item in self.principals],
        }


@dataclass(frozen=True, kw_only=True)
class SetupContent:
    """One piece of content to install and attach, with a semantic role."""

    path: Path
    role: str = "notes"
    pack_id: str | None = None
    system_id: str | None = None
    gm_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "role": self.role,
            "pack_id": self.pack_id,
            "system_id": self.system_id,
            "gm_only": self.gm_only,
        }


@dataclass(frozen=True, kw_only=True)
class SetupScene:
    """Where play starts."""

    scene_id: str
    name: str
    location_entity_id: str | None = None
    present: tuple[str, ...] = ()
    in_world_started_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "name": self.name,
            "location_entity_id": self.location_entity_id,
            "present": list(self.present),
            "in_world_started_at": self.in_world_started_at,
        }


@dataclass(frozen=True, kw_only=True)
class CampaignSetupManifest:
    """A fully validated description of a campaign to create."""

    campaign_id: str
    name: str
    system_id: str
    setting_id: str | None = None
    participants: tuple[SetupParticipant, ...] = ()
    characters: tuple[tuple[str, str, str | None], ...] = ()
    content: tuple[SetupContent, ...] = ()
    starting_state: Mapping[str, Any] = field(default_factory=dict)
    starting_scene: SetupScene | None = None
    game_time: Mapping[str, Any] | None = None
    source_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "name": self.name,
            "system_id": self.system_id,
            "setting_id": self.setting_id,
            "participants": [p.to_dict() for p in self.participants],
            "characters": [list(item) for item in self.characters],
            "content": [item.to_dict() for item in self.content],
            "starting_state": dict(self.starting_state),
            "starting_scene": (
                None if self.starting_scene is None else self.starting_scene.to_dict()
            ),
            "game_time": None if self.game_time is None else dict(self.game_time),
        }


def load_setup_manifest(path: Path) -> CampaignSetupManifest:
    """Read and validate one setup manifest.

    Every referenced path is resolved relative to the manifest's own
    directory and must stay inside it. A setup file is data: it may name
    documents to read, never code to run.
    """

    manifest_path = Path(path).expanduser().resolve()
    if not manifest_path.is_file():
        raise SetupManifestError(f"setup manifest not found: {path}")
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SetupManifestError(f"setup manifest is not valid YAML: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise SetupManifestError("setup manifest must be a mapping")
    return parse_setup_manifest(raw, base_dir=manifest_path.parent)


def parse_setup_manifest(
    payload: Mapping[str, Any], *, base_dir: Path
) -> CampaignSetupManifest:
    """Validate a decoded manifest, resolving paths under ``base_dir``."""

    _reject_credential_keys(payload)

    unexpected = sorted(set(payload) - _ALLOWED_TOP_LEVEL)
    if unexpected:
        raise SetupManifestError(
            f"setup manifest has unexpected fields: {', '.join(unexpected)}"
        )
    missing = sorted(_REQUIRED_TOP_LEVEL - set(payload))
    if missing:
        raise SetupManifestError(
            f"setup manifest is missing required fields: {', '.join(missing)}"
        )

    campaign_id = _require_id(payload, "campaign_id")
    system_id = _require_id(payload, "system_id")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SetupManifestError("setup manifest requires a non-empty name")

    setting_id = payload.get("setting_id")
    if setting_id is not None:
        setting_id = _require_id({"setting_id": setting_id}, "setting_id")

    return CampaignSetupManifest(
        campaign_id=campaign_id,
        name=name.strip(),
        system_id=system_id,
        setting_id=setting_id,
        participants=_parse_participants(payload.get("participants") or ()),
        characters=_parse_characters(payload.get("characters") or ()),
        content=_parse_content(payload.get("content") or (), base_dir=base_dir),
        starting_state=_parse_mapping(payload.get("starting_state")),
        starting_scene=_parse_scene(payload.get("starting_scene")),
        game_time=_parse_optional_mapping(payload.get("game_time"), "game_time"),
        source_path=base_dir,
    )


def _reject_credential_keys(value: Any, *, path: str = "") -> None:
    """Refuse a manifest that looks like it carries a secret.

    A setup file belongs in version control. A credential-shaped key in one is
    either a mistake or a leak, and both should stop the run before any
    database is touched.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            label = f"{path}.{key}" if path else str(key)
            if isinstance(key, str) and _CREDENTIAL_KEY_PATTERN.search(key):
                raise SetupManifestError(
                    f"setup manifest key {label!r} looks like a credential; "
                    "keep secrets in the environment"
                )
            _reject_credential_keys(item, path=label)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_credential_keys(item, path=f"{path}[{index}]")


def _require_id(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise SetupManifestError(
            f"{key} must match {_ID_PATTERN.pattern}, got {value!r}"
        )
    return value


def _parse_participants(entries: Sequence[Any]) -> tuple[SetupParticipant, ...]:
    parsed: list[SetupParticipant] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise SetupManifestError("each participant must be a mapping")
        participant_id = _require_id(entry, "participant_id")
        if participant_id in seen:
            raise SetupManifestError(f"duplicate participant {participant_id!r}")
        seen.add(participant_id)
        role = entry.get("role", "player")
        if role not in {"player", "gm"}:
            raise SetupManifestError(f"participant role must be gm or player, got {role!r}")
        display_name = entry.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise SetupManifestError(
                f"participant {participant_id!r} requires a non-empty display_name"
            )
        parsed.append(
            SetupParticipant(
                participant_id=participant_id,
                display_name=display_name.strip(),
                role=role,
                character_ids=tuple(
                    _require_id({"entity_id": item}, "entity_id")
                    for item in entry.get("character_ids") or ()
                ),
                principals=tuple(
                    (str(pair[0]), str(pair[1])) for pair in entry.get("principals") or ()
                ),
            )
        )
    if sum(1 for p in parsed if p.role == "gm") > 1:
        raise SetupManifestError("a campaign may declare at most one gm")
    return tuple(parsed)


def _parse_characters(entries: Sequence[Any]) -> tuple[tuple[str, str, str | None], ...]:
    parsed: list[tuple[str, str, str | None]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise SetupManifestError("each character must be a mapping")
        entity_id = _require_id(entry, "entity_id")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SetupManifestError(
                f"character {entity_id!r} requires a non-empty name"
            )
        owner = entry.get("participant_id")
        if owner is not None:
            owner = _require_id({"participant_id": owner}, "participant_id")
        parsed.append((entity_id, name.strip(), owner))
    return tuple(parsed)


def _parse_content(entries: Sequence[Any], *, base_dir: Path) -> tuple[SetupContent, ...]:
    parsed: list[SetupContent] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise SetupManifestError("each content entry must be a mapping")
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise SetupManifestError("each content entry requires a path")
        resolved = _resolve_inside(base_dir, raw_path)
        if resolved.suffix.lower() in _EXECUTABLE_SUFFIXES:
            raise SetupManifestError(
                f"setup content may not reference executable material: {raw_path}"
            )
        role = entry.get("role", "notes")
        if role not in {"rules", "setting", "adventure", "character", "notes", "reference"}:
            raise SetupManifestError(f"unknown content role {role!r}")
        parsed.append(
            SetupContent(
                path=resolved,
                role=role,
                pack_id=entry.get("pack_id"),
                system_id=entry.get("system_id"),
                gm_only=bool(entry.get("gm_only", False)),
            )
        )
    return tuple(parsed)


def _parse_scene(entry: Any) -> SetupScene | None:
    if entry is None:
        return None
    if not isinstance(entry, Mapping):
        raise SetupManifestError("starting_scene must be a mapping")
    return SetupScene(
        scene_id=_require_id(entry, "scene_id"),
        name=str(entry.get("name") or entry["scene_id"]),
        location_entity_id=entry.get("location_entity_id"),
        present=tuple(
            _require_id({"entity_id": item}, "entity_id")
            for item in entry.get("present") or ()
        ),
        in_world_started_at=entry.get("in_world_started_at"),
    )


def _parse_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SetupManifestError("expected a mapping")
    return dict(value)


def _parse_optional_mapping(value: Any, label: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise SetupManifestError(f"{label} must be a mapping")
    return dict(value)


def _resolve_inside(base_dir: Path, raw_path: str) -> Path:
    """Resolve a referenced path, refusing anything outside the manifest dir."""

    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise SetupManifestError(f"setup content path must be relative: {raw_path}")
    root = base_dir.resolve()
    resolved = (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise SetupManifestError(
            f"setup content path escapes the manifest directory: {raw_path}"
        )
    if not resolved.exists():
        raise SetupManifestError(f"setup content path does not exist: {raw_path}")
    return resolved
