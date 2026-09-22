"""Context builds record why entries were kept or dropped."""

from __future__ import annotations

from tabletop.api.visibility import gm_viewpoint
from tabletop.api.workspace import Workspace
from tabletop.orchestration.context import ContextSource, build_context, ContextRequest


class _Source:
    def read(self, source, request):
        if source is ContextSource.SETTING_FACTS:
            return ("short fact", "x" * 400)
        return ()


def test_build_context_traces_selected_and_dropped_entries() -> None:
    context = build_context(
        ContextRequest(
            source=_Source(),
            workspace=Workspace.SETTING,
            viewpoint=gm_viewpoint(),
            campaign_id=None,
            query="",
            model_context_size=80,
            prompt_reserve=0,
            response_reserve=0,
            session_reserve=0,
            per_entry_ceiling=50,
        )
    )
    actions = {item.action for item in context.trace}
    assert "selected" in actions or "dropped" in actions
    assert all(item.estimated_tokens > 0 for item in context.trace)
    assert all(item.reason for item in context.trace)
