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

from tabletop.campaign.models import PresenceType

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
        participants=_parse_participants(_require_list(payload.get("participants"), "participants")),
        characters=_parse_characters(_require_list(payload.get("characters"), "characters")),
        content=_parse_content(
            _require_list(payload.get("content"), "content"), base_dir=base_dir
        ),
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


def _require_list(value: Any, label: str) -> list[Any]:
    """Coerce an optional field to a list, or explain what it must be.

    A scalar where a list belongs is a manifest mistake an operator needs to
    see, not a TypeError from iterating an integer.
    """

    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise SetupManifestError(f"{label} must be a list")
    return list(value)


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
                principals=_parse_principals(entry.get("principals")),
            )
        )
    if sum(1 for p in parsed if p.role == "gm") > 1:
        raise SetupManifestError("a campaign may declare at most one gm")
    return tuple(parsed)


def _parse_principals(entries: Any) -> tuple[tuple[str, str], ...]:
    """Parse channel principal bindings as [channel, external_id] pairs."""
    pairs: list[tuple[str, str]] = []
    for pair in _require_list(entries, "principals"):
        if isinstance(pair, str) or not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise SetupManifestError(
                "each principal must be a [channel, external_id] pair"
            )
        channel, external_id = pair
        if not isinstance(channel, str) or not channel.strip():
            raise SetupManifestError("principal channel must be a non-empty string")
        if not isinstance(external_id, str) or not external_id.strip():
            raise SetupManifestError("principal external_id must be a non-empty string")
        pairs.append((channel.strip(), external_id.strip()))
    return tuple(pairs)


def _parse_characters(entries: Sequence[Any]) -> tuple[tuple[str, str, str | None], ...]:
    parsed: list[tuple[str, str, str | None]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise SetupManifestError("each character must be a mapping")
        entity_id = _require_id(entry, "entity_id")
        if entity_id in seen:
            # Caught here rather than as a post-commit conflict, which would
            # leave the campaign half-configured.
            raise SetupManifestError(f"duplicate character {entity_id!r}")
        seen.add(entity_id)
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


# -- planning and idempotent application ------------------------------------


class SetupConflictError(RuntimeError):
    """An existing row disagrees with the manifest and must not be overwritten."""


@dataclass(frozen=True, kw_only=True)
class SetupAction:
    """One planned step, with enough detail to review it before applying."""

    kind: str
    target: str
    detail: str = ""
    #: True when the row already exists and is identical, so applying is a no-op.
    already_satisfied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target": self.target,
            "detail": self.detail,
            "already_satisfied": self.already_satisfied,
        }


@dataclass(frozen=True, kw_only=True)
class SetupPlan:
    """The full set of steps a manifest implies, in application order."""

    manifest: CampaignSetupManifest
    actions: tuple[SetupAction, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.manifest.campaign_id,
            "actions": [action.to_dict() for action in self.actions],
            "summary": {
                "total": len(self.actions),
                "create": sum(1 for a in self.actions if not a.already_satisfied),
                "already_satisfied": sum(1 for a in self.actions if a.already_satisfied),
            },
        }

    @property
    def has_work(self) -> bool:
        """True when applying would change something."""
        return any(not action.already_satisfied for action in self.actions)


def plan_setup(conn, manifest: CampaignSetupManifest) -> SetupPlan:
    """Describe what applying this manifest would do. Writes nothing.

    A step already satisfied is reported rather than repeated, which is what
    makes a rerun safe. A step whose existing row disagrees with the manifest
    is a conflict, not an update: setup configures, it does not silently
    rewrite authoritative data an operator may have changed by hand.
    """
    from tabletop.campaign.membership import MembershipStore
    from tabletop.campaign.scenes import SceneStore
    from tabletop.campaign.store import CampaignStore

    store = CampaignStore(conn)
    membership = MembershipStore(conn)
    scenes = SceneStore(conn)
    campaign_id = manifest.campaign_id
    actions: list[SetupAction] = []

    existing = store.get_campaign(campaign_id)
    if existing is None:
        actions.append(SetupAction(kind="CREATE campaign", target=campaign_id,
                                   detail=f"{manifest.name} on {manifest.system_id}"))
    else:
        if existing["system_id"] != manifest.system_id:
            raise SetupConflictError(
                f"campaign {campaign_id!r} uses system {existing['system_id']!r}, "
                f"but the manifest declares {manifest.system_id!r}"
            )
        if existing["name"] != manifest.name:
            raise SetupConflictError(
                f"campaign {campaign_id!r} is named {existing['name']!r}, "
                f"but the manifest declares {manifest.name!r}"
            )
        actions.append(
            SetupAction(
                kind="CREATE campaign",
                target=campaign_id,
                detail="already present and identical",
                already_satisfied=True,
            )
        )

    for participant in manifest.participants:
        rows = {row["participant_id"]: row for row in membership.list_participants(campaign_id)}
        row = rows.get(participant.participant_id)
        if row is None:
            actions.append(
                SetupAction(
                    kind="ADD participant",
                    target=participant.participant_id,
                    detail=f"{participant.display_name} ({participant.role})",
                )
            )
        elif row["role"] != participant.role or row["display_name"] != participant.display_name:
            raise SetupConflictError(
                f"participant {participant.participant_id!r} exists with different "
                "authoritative data"
            )
        else:
            actions.append(
                SetupAction(
                    kind="ADD participant",
                    target=participant.participant_id,
                    detail="already present and identical",
                    already_satisfied=True,
                )
            )

    for channel, external_id, _participant_id in _all_principals(manifest):
        key = f"{channel}:{external_id}"
        if (channel, external_id) in {
            (row["channel"], row["external_id"])
            for row in membership.list_principals(campaign_id)
        }:
            actions.append(
                SetupAction(
                    kind="BIND principal",
                    target=key,
                    detail="already bound",
                    already_satisfied=True,
                )
            )
        else:
            actions.append(SetupAction(kind="BIND principal", target=key, detail=channel))

    for entity_id, name, owner in manifest.characters:
        row = store.get_entity(campaign_id, entity_id)
        if row is None:
            actions.append(
                SetupAction(
                    kind="GRANT character",
                    target=entity_id,
                    detail=f"{name}" + (f" owned by {owner}" if owner else ""),
                )
            )
        elif row["name"] != name:
            raise SetupConflictError(
                f"entity {entity_id!r} is named {row['name']!r}, "
                f"but the manifest declares {name!r}"
            )
        else:
            actions.append(
                SetupAction(
                    kind="GRANT character",
                    target=entity_id,
                    detail="already present and identical",
                    already_satisfied=True,
                )
            )
        if owner is not None and not _has_control(membership, campaign_id, owner, entity_id):
            actions.append(
                SetupAction(
                    kind="GRANT control", target=entity_id, detail=f"{owner} owns it"
                )
            )

    installed = {
        str(row[0])
        for row in conn.execute("SELECT source_path FROM documents").fetchall()
    }
    attached = {
        str(row[0])
        for row in conn.execute(
            "SELECT document_id FROM campaign_documents WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchall()
    }
    for item in manifest.content:
        document_id = _document_id_for(conn, item)
        actions.append(
            SetupAction(
                kind="INSTALL content",
                target=item.path.name,
                detail=f"role={item.role}" + (" gm_only" if item.gm_only else ""),
                # A document already ingested from this path needs no second
                # install, so a rerun is a genuine no-op.
                already_satisfied=str(item.path) in installed,
            )
        )
        actions.append(
            SetupAction(
                kind="ATTACH content",
                target=item.path.name,
                detail=f"role={item.role}",
                already_satisfied=document_id is not None
                and document_id in attached,
            )
        )

    if manifest.starting_state:
        wanted = manifest.starting_state.get("scene") or {}
        existing_scene = (
            None
            if manifest.starting_scene is None
            else scenes.get_scene(campaign_id, manifest.starting_scene.scene_id)
        )
        already = bool(existing_scene) and existing_scene.system_state == dict(wanted)
        actions.append(
            SetupAction(
                kind="APPLY starting state",
                target=campaign_id,
                detail="scene state",
                already_satisfied=already,
            )
        )

    if manifest.starting_scene is not None:
        scene = manifest.starting_scene
        if scenes.get_scene(campaign_id, scene.scene_id) is not None:
            actions.append(
                SetupAction(
                    kind="OPEN scene",
                    target=scene.scene_id,
                    detail="already present and identical",
                    already_satisfied=True,
                )
            )
        else:
            actions.append(
                SetupAction(kind="OPEN scene", target=scene.scene_id, detail=scene.name)
            )

    if manifest.game_time is not None:
        satisfied = scenes.get_game_time(campaign_id) is not None
        actions.append(
            SetupAction(
                kind="SET game time",
                target=campaign_id,
                detail=str(manifest.game_time.get("in_world_label", "")),
                already_satisfied=satisfied,
            )
        )

    return SetupPlan(manifest=manifest, actions=tuple(actions))


def _document_id_for(conn, item: SetupContent) -> str | None:
    """Return the installed document id for a content path, if ingested."""
    from tabletop.documents.content_install import content_hash

    try:
        digest = content_hash(item.path)
    except OSError:
        return None
    row = conn.execute(
        "SELECT document_id FROM documents WHERE content_hash = ?", (digest,)
    ).fetchone()
    return None if row is None else str(row["document_id"])


def _all_principals(manifest: CampaignSetupManifest):
    for participant in manifest.participants:
        for channel, external_id in participant.principals:
            yield (channel, external_id, participant.participant_id)


def _has_control(membership, campaign_id: str, participant_id: str, entity_id: str) -> bool:
    return any(
        row["participant_id"] == participant_id and row["entity_id"] == entity_id
        for row in membership.list_controls(campaign_id)
    )


def apply_setup(conn, manifest: CampaignSetupManifest) -> SetupPlan:
    """Apply a manifest, creating only what is missing.

    The whole database portion runs in one ``BEGIN IMMEDIATE`` so a failure
    part-way leaves nothing half-configured. A rerun over the same manifest
    creates nothing the second time: every step is checked before it is
    written, and a step whose existing row disagrees raises rather than
    overwriting authoritative data an operator may have changed by hand.
    """

    from tabletop.campaign.membership import MembershipStore
    from tabletop.campaign.scenes import SceneStore
    from tabletop.campaign.store import CampaignStore
    from tabletop.storage.sqlite import transaction

    plan = plan_setup(conn, manifest)
    if not plan.has_work:
        return plan

    store = CampaignStore(conn)
    membership = MembershipStore(conn)
    scenes = SceneStore(conn)
    campaign_id = manifest.campaign_id

    # Content is installed before the campaign transaction opens. Installing
    # writes global document rows and indexes chunks in its own transaction,
    # and attaching is what activates it for this campaign. Declaring content
    # and never installing it would silently configure a campaign without the
    # rules it says it uses.
    document_ids: list[tuple[SetupContent, str]] = []
    if manifest.content:
        from tabletop.documents.content_install import ContentError, install_document

        for item in manifest.content:
            try:
                document_ids.append((item, install_document(conn, item.path)))
            except ContentError as exc:
                raise SetupManifestError(
                    f"cannot install {item.path.name}: {exc}"
                ) from exc

    with transaction(conn):
        if store.get_campaign(campaign_id) is None:
            store.create_campaign_in_transaction(
                campaign_id,
                manifest.name,
                manifest.system_id,
                setting_id=manifest.setting_id,
                system_state=manifest.starting_state.get("campaign"),
            )

        for participant in manifest.participants:
            rows = {
                row["participant_id"]: row
                for row in membership.list_participants(campaign_id)
            }
            if participant.participant_id not in rows:
                membership.add_participant_in_transaction(
                    campaign_id,
                    participant.participant_id,
                    participant.display_name,
                    participant.role,
                )

        bound = {
            (row["channel"], row["external_id"])
            for row in membership.list_principals(campaign_id)
        }
        for channel, external_id, participant_id in _all_principals(manifest):
            if (channel, external_id) not in bound:
                membership.bind_principal_in_transaction(
                    campaign_id, participant_id, channel, external_id
                )

        for entity_id, name, _owner in manifest.characters:
            if store.get_entity(campaign_id, entity_id) is None:
                store.upsert_entity_in_transaction(campaign_id, entity_id, name)

        for entity_id, _name, owner in manifest.characters:
            if owner is not None and not _has_control(
                membership, campaign_id, owner, entity_id
            ):
                membership.grant_control_in_transaction(
                    campaign_id, owner, entity_id, "owner"
                )

        from tabletop.documents.catalog import ContentCatalog

        catalog = ContentCatalog(conn)
        for item, document_id in document_ids:
            already = conn.execute(
                "SELECT 1 FROM campaign_documents "
                "WHERE campaign_id = ? AND document_id = ?",
                (campaign_id, document_id),
            ).fetchone()
            if already is None:
                catalog.attach_document_in_transaction(
                    campaign_id,
                    document_id,
                    item.role,
                    gm_only=item.gm_only,
                )

        scene = manifest.starting_scene
        if scene is not None and scenes.get_scene(campaign_id, scene.scene_id) is None:
            scenes.open_scene_in_transaction(
                campaign_id,
                scene.scene_id,
                scene.name,
                location_entity_id=scene.location_entity_id,
                in_world_started_at=scene.in_world_started_at,
                system_state=manifest.starting_state.get("scene"),
            )
            for entity_id in scene.present:
                scenes.enter_in_transaction(
                    campaign_id, scene.scene_id, entity_id, PresenceType.NPC
                )

        if manifest.game_time is not None and scenes.get_game_time(campaign_id) is None:
            scenes.set_game_time_in_transaction(
                campaign_id,
                in_world_label=manifest.game_time.get("in_world_label"),
                in_world_minutes=manifest.game_time.get("in_world_minutes"),
            )

    return plan_setup(conn, manifest)
