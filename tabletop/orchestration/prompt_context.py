"""Render allocated context as prompt text.

The frozen snapshot type is the transport contract for later callers.
This module's renderer returns a string and does not build that snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass

from tabletop.orchestration.context import Context

WRAPPER_TOKEN_CAP = 64

PROMPT_CONTEXT_HEADER = (
    "Allocated tabletop context. Snapshot from before the current human "
    "message. Not campaign truth."
)


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
