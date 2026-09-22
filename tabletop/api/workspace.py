"""Workspace capability surfaces for Omega skill registration.

One active workspace per runtime instance, selected at startup. Omega's
``add-skill`` mutates a process-wide registry, so the tool surface is fixed
for the life of the process: forbidden operations are absent from the
registered list rather than discouraged by prompt text.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import assert_never


@dataclass(frozen=True, slots=True)
class SkillSpec:
    """One Omega skill bound to a ``TabletopRuntime`` method."""

    name: str
    description: str
    parameters: tuple[str, ...]
    runtime_method: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("skill name must be non-empty")
        if not self.description.strip():
            raise ValueError(f"skill {self.name!r} requires a non-empty description")
        if not self.runtime_method.strip():
            raise ValueError(f"skill {self.name!r} requires a runtime method")


# Setting workspace: author the shared world. No session, quest, party, or
# campaign-secret operations appear on this surface.
_SETTING_SKILLS: tuple[SkillSpec, ...] = (
    SkillSpec(
        name="query-setting",
        description=(
            "Query setting metadata and setting-scoped world facts visible "
            "in the current setting workspace"
        ),
        parameters=("query_in_quotes",),
        runtime_method="query_setting",
    ),
    SkillSpec(
        name="edit-setting",
        description="Edit setting metadata for the active setting workspace",
        parameters=("edit_in_quotes",),
        runtime_method="edit_setting",
    ),
    SkillSpec(
        name="get-world-entity",
        description="Return one setting-owned world entity by stable identifier",
        parameters=("entity_id_in_quotes",),
        runtime_method="get_world_entity",
    ),
    SkillSpec(
        name="upsert-world-entity",
        description="Create or update one setting-owned world entity",
        parameters=("entity_in_quotes",),
        runtime_method="upsert_world_entity",
    ),
    SkillSpec(
        name="query-world-history",
        description="Query setting-scoped world history and durable world facts",
        parameters=("query_in_quotes",),
        runtime_method="query_world_history",
    ),
    SkillSpec(
        name="record-world-history",
        description="Record a setting-scoped world history entry",
        parameters=("entry_in_quotes",),
        runtime_method="record_world_history",
    ),
)

# Campaign inherits setting *read* access only; setting mutation stays setting-only.
_SETTING_READ_SKILLS: tuple[SkillSpec, ...] = tuple(
    skill
    for skill in _SETTING_SKILLS
    if skill.name
    in {
        "query-setting",
        "get-world-entity",
        "query-world-history",
    }
)

_CAMPAIGN_ONLY_SKILLS: tuple[SkillSpec, ...] = (
    SkillSpec(
        name="read-session",
        description="Read one campaign session record by session identifier",
        parameters=("session_id_in_quotes",),
        runtime_method="read_session",
    ),
    SkillSpec(
        name="start-session",
        description="Open one campaign session through the authoritative runtime",
        parameters=("session_in_quotes",),
        runtime_method="start_session",
    ),
    SkillSpec(
        name="end-session",
        description="Close the active tabletop session through the authoritative runtime",
        parameters=(),
        runtime_method="end_session",
    ),
    SkillSpec(
        name="current-scene",
        description="Return the active scene from authoritative campaign state",
        parameters=(),
        runtime_method="current_scene",
    ),
    SkillSpec(
        name="get-party-state",
        description="Return the current party membership and party-visible state",
        parameters=(),
        runtime_method="get_party_state",
    ),
    SkillSpec(
        name="get-open-threads",
        description="Return open campaign threads tracked in authoritative state",
        parameters=(),
        runtime_method="get_open_threads",
    ),
    SkillSpec(
        name="mutate-quest",
        description="Create or update a campaign quest record",
        parameters=("quest_in_quotes",),
        runtime_method="mutate_quest",
    ),
    SkillSpec(
        name="read-campaign-secret",
        description="Read one GM-scoped campaign secret by stable identifier",
        parameters=("secret_id_in_quotes",),
        runtime_method="read_campaign_secret",
    ),
    SkillSpec(
        name="current-campaign",
        description=(
            "Return the active tabletop campaign or an explicit configuration error"
        ),
        parameters=(),
        runtime_method="current_campaign",
    ),
    SkillSpec(
        name="query-campaign",
        description=(
            "Query authoritative campaign information visible to the current viewpoint"
        ),
        parameters=("query_in_quotes",),
        runtime_method="query_campaign",
    ),
    SkillSpec(
        name="query-rules",
        description=(
            "Query active tabletop rules and return source-aware runtime results"
        ),
        parameters=("query_in_quotes",),
        runtime_method="query_rules",
    ),
    SkillSpec(
        name="resolve-action",
        description=(
            "Resolve a structured game action through the active game-system plugin"
        ),
        parameters=("action_in_quotes",),
        runtime_method="resolve_action",
    ),
    SkillSpec(
        name="roll",
        description=(
            "Roll a system-agnostic dice expression through the deterministic "
            "runtime dice engine"
        ),
        parameters=("expression_in_quotes",),
        runtime_method="roll",
    ),
    SkillSpec(
        name="get-entity",
        description=(
            "Return one campaign entity by stable identifier with visibility filtering"
        ),
        parameters=("entity_id_in_quotes",),
        runtime_method="get_entity",
    ),
    SkillSpec(
        name="get-fact",
        description="Return one campaign or owned-setting fact by stable identifier",
        parameters=("fact_id_in_quotes",),
        runtime_method="get_fact",
    ),
    SkillSpec(
        name="get-relationships",
        description="Return visible relationships for one campaign entity",
        parameters=("entity_id_in_quotes",),
        runtime_method="get_relationships",
    ),
    SkillSpec(
        name="record-ruling",
        description="Record a durable campaign ruling through the authoritative runtime",
        parameters=("ruling_in_quotes",),
        runtime_method="record_ruling",
    ),
)


class Workspace(str, Enum):
    """Runtime capability surface selected once at process startup."""

    SETTING = "setting"
    CAMPAIGN = "campaign"

    @property
    def skills(self) -> tuple[SkillSpec, ...]:
        match self:
            case Workspace.SETTING:
                return _SETTING_SKILLS
            case Workspace.CAMPAIGN:
                return (*_SETTING_READ_SKILLS, *_CAMPAIGN_ONLY_SKILLS)
            case _:
                assert_never(self)


def parse_workspace(value: str | None) -> Workspace:
    """Parse ``TABLETOP_WORKSPACE``. Unset or unknown values fail closed."""

    if value is None or not str(value).strip():
        raise ValueError(
            "TABLETOP_WORKSPACE is required and must be 'setting' or 'campaign'; "
            "refusing to default to the larger skill surface"
        )
    normalized = str(value).strip().lower()
    try:
        return Workspace(normalized)
    except ValueError as exc:
        raise ValueError(
            f"unknown TABLETOP_WORKSPACE {value!r}; expected 'setting' or 'campaign'"
        ) from exc


def skill_registration_entries(workspace: Workspace) -> tuple[dict[str, object], ...]:
    """JSON-safe registration payload entries for the active workspace."""

    return tuple(
        {
            "name": skill.name,
            "description": skill.description,
            "parameters": list(skill.parameters),
            "runtime_method": skill.runtime_method,
        }
        for skill in workspace.skills
    )
