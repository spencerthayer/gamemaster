"""Render allocated context as prompt text.

The frozen snapshot type is the transport contract for later callers.
``render_allocated_context`` returns a string and does not build that snapshot.
``build_prompt_context_snapshot`` is the first constructor.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping

from tabletop.api.visibility import Viewpoint, gm_viewpoint
from tabletop.api.workspace import Workspace
from tabletop.campaign.models import FactScope
from tabletop.campaign.scene_snapshot import build_scene_snapshot
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.context import (
    Context,
    ContextRequest,
    ContextSource,
    build_context,
    compute_budget,
)

WRAPPER_TOKEN_CAP = 64

PROMPT_CONTEXT_HEADER = (
    "Allocated tabletop context. Snapshot from before the current human "
    "message. Not campaign truth."
)

NO_CAMPAIGN_TEXT = "No campaign is selected."

# Fixed budget for the live GM snapshot. Tighter ceilings belong in direct
# ``build_context`` tests, not on this model-facing path.
PROMPT_CONTEXT_MODEL_TOKENS = 8192
PROMPT_CONTEXT_ENTRY_CEILING = 2000


@dataclass(frozen=True, slots=True)
class PromptContextSnapshot:
    """Pre-message tabletop contribution. Task 02 is the first constructor."""

    text: str
    campaign_id: str | None
    workspace: str
    context_budget: int
    considered_count: int
    selected_count: int
    compacted_count: int
    dropped_count: int
    estimated_tokens: int
    source_kinds: tuple[str, ...]
    context_sha256: str


def render_allocated_context(context: Context) -> str:
    """Serialize selected entries. Dropped candidates are not in ``context.entries``."""

    blocks = [PROMPT_CONTEXT_HEADER]
    for entry in context.entries:
        block = entry.content
        tool = entry.refetch_tool
        if tool and tool.strip():
            arguments = " ".join(
                f"{key}={value}" for key, value in entry.refetch_args.items()
            )
            suffix = tool if not arguments else f"{tool} {arguments}"
            block = f"{block}\n{suffix}"
        blocks.append(block)
    return "\n\n".join(blocks)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _render_scene_snapshot(snapshot: Mapping[str, Any]) -> str:
    """Render the structured snapshot as prompt text for one viewpoint."""

    scene = snapshot.get("scene")
    lines: list[str] = []
    if scene is None:
        lines.append("No scene has been opened yet.")
    else:
        lines.append(
            f"Scene: {scene['name']} ({scene['status']}, id {scene['scene_id']})"
        )
    present = snapshot.get("present_entities") or []
    lines.append(
        "Present: "
        + (
            ", ".join(
                f"{item['name'] or item['entity_id']} [{item['presence_type']}]"
                for item in present
            )
            or "nobody recorded."
        )
    )
    clock = snapshot.get("game_time")
    if clock is not None and clock.get("in_world_label") is not None:
        lines.append(f"In-world time: {clock['in_world_label']}")
    recent = snapshot.get("recent_events") or []
    if recent:
        lines.append("Recent authoritative events:")
        lines.extend(
            f"- {item['event_type']} (sequence {item['sequence']})" for item in recent
        )
    rulings = snapshot.get("active_rulings") or []
    if rulings:
        lines.append("Active campaign rulings:")
        lines.extend(f"- {item['question']} -> {item['decision']}" for item in rulings)
    return "\n".join(lines)


class _LibraryContextSource:
    """Read facts through the campaign store. No SQL lives in this module."""

    def __init__(
        self,
        connection: sqlite3.Connection | None,
        setting_id: str | None,
    ) -> None:
        self._connection = connection
        self._setting_id = setting_id

    def read(self, source: ContextSource, request: ContextRequest) -> tuple[object, ...]:
        if self._connection is None:
            return ()
        store = CampaignStore(self._connection)
        if source is ContextSource.CURRENT_SCENE and request.campaign_id:
            # Rendered as text rather than handed over raw: a dict would reach
            # the model as a Python repr, not as readable context.
            return (
                _render_scene_snapshot(
                    build_scene_snapshot(
                        self._connection,
                        request.campaign_id,
                        viewpoint=request.viewpoint,
                    )
                ),
            )
        if source is ContextSource.FACTS and request.campaign_id:
            facts = store.get_facts(request.campaign_id, viewpoint=request.viewpoint)
            return tuple(fact for fact in facts if fact.fact_scope is FactScope.CAMPAIGN)
        if source is ContextSource.SETTING_FACTS and self._setting_id:
            return tuple(
                store.get_setting_facts(self._setting_id, viewpoint=request.viewpoint)
            )
        return ()


def build_prompt_context_snapshot(
    connection: sqlite3.Connection | None,
    *,
    workspace: Workspace,
    campaign_id: str | None,
    setting_id: str | None = None,
) -> PromptContextSnapshot:
    """Build the GM snapshot. Viewpoint is not a parameter."""

    if workspace is Workspace.CAMPAIGN and not campaign_id:
        return _snapshot_without_context(workspace, NO_CAMPAIGN_TEXT, campaign_id=None)
    budget = compute_budget(PROMPT_CONTEXT_MODEL_TOKENS, 0, 0, 0)
    context = build_context(
        ContextRequest(
            source=_LibraryContextSource(connection, setting_id),
            workspace=workspace,
            viewpoint=gm_viewpoint(),
            campaign_id=campaign_id,
            query="",
            model_context_size=PROMPT_CONTEXT_MODEL_TOKENS,
            prompt_reserve=0,
            response_reserve=0,
            session_reserve=0,
            per_entry_ceiling=PROMPT_CONTEXT_ENTRY_CEILING,
        )
    )
    text = render_allocated_context(context)
    selected_count = sum(1 for entry in context.entries if not entry.compacted)
    compacted_count = sum(1 for entry in context.entries if entry.compacted)
    dropped_count = sum(1 for item in context.trace if item.action == "dropped")
    source_kinds = tuple(
        source.value
        for source in ContextSource
        if any(entry.source is source for entry in context.entries)
    )
    return PromptContextSnapshot(
        text=text,
        campaign_id=campaign_id,
        workspace=workspace.value,
        context_budget=budget,
        considered_count=selected_count + compacted_count + dropped_count,
        selected_count=selected_count,
        compacted_count=compacted_count,
        dropped_count=dropped_count,
        estimated_tokens=context.used_tokens,
        source_kinds=source_kinds,
        context_sha256=_digest(text),
    )


def build_player_prompt_context_snapshot(
    connection: sqlite3.Connection | None,
    *,
    workspace: Workspace,
    campaign_id: str | None,
    viewpoint: Viewpoint,
    setting_id: str | None = None,
) -> PromptContextSnapshot:
    """Build a player snapshot from an explicit non-GM viewpoint."""

    if viewpoint.scope.kind.value == "GM" and not viewpoint.character_ids:
        # Refuse the pure GM constructor path.
        if viewpoint == gm_viewpoint():
            raise ValueError("player prompt refuses gm_viewpoint()")
    if workspace is Workspace.PLAYER and not campaign_id:
        return _snapshot_without_context(workspace, NO_CAMPAIGN_TEXT, campaign_id=None)
    budget = compute_budget(PROMPT_CONTEXT_MODEL_TOKENS, 0, 0, 0)
    context = build_context(
        ContextRequest(
            source=_LibraryContextSource(connection, setting_id),
            workspace=workspace,
            viewpoint=viewpoint,
            campaign_id=campaign_id,
            query="",
            model_context_size=PROMPT_CONTEXT_MODEL_TOKENS,
            prompt_reserve=0,
            response_reserve=0,
            session_reserve=0,
            per_entry_ceiling=PROMPT_CONTEXT_ENTRY_CEILING,
        )
    )
    text = render_allocated_context(context)
    selected_count = sum(1 for entry in context.entries if not entry.compacted)
    compacted_count = sum(1 for entry in context.entries if entry.compacted)
    dropped_count = sum(1 for item in context.trace if item.action == "dropped")
    source_kinds = tuple(
        source.value
        for source in ContextSource
        if any(entry.source is source for entry in context.entries)
    )
    return PromptContextSnapshot(
        text=text,
        campaign_id=campaign_id,
        workspace=workspace.value,
        context_budget=budget,
        considered_count=selected_count + compacted_count + dropped_count,
        selected_count=selected_count,
        compacted_count=compacted_count,
        dropped_count=dropped_count,
        estimated_tokens=context.used_tokens,
        source_kinds=source_kinds,
        context_sha256=_digest(text),
    )


def _snapshot_without_context(
    workspace: Workspace,
    text: str,
    *,
    campaign_id: str | None,
) -> PromptContextSnapshot:
    return PromptContextSnapshot(
        text=text,
        campaign_id=campaign_id,
        workspace=workspace.value,
        context_budget=0,
        considered_count=0,
        selected_count=0,
        compacted_count=0,
        dropped_count=0,
        estimated_tokens=0,
        source_kinds=(),
        context_sha256=_digest(text),
    )
