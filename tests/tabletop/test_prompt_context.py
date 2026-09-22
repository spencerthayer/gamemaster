"""Allocated prompt text stays inside the selected context entries."""

from __future__ import annotations

from tabletop.orchestration.context import (
    Context,
    ContextDecision,
    ContextEntry,
    ContextSource,
    estimate_tokens,
)
from tabletop.orchestration.prompt_context import (
    PROMPT_CONTEXT_HEADER,
    WRAPPER_TOKEN_CAP,
    render_allocated_context,
)


def _entry(
    content: str,
    *,
    tool: str | None = None,
    args: dict[str, object] | None = None,
    source: ContextSource | None = None,
    compacted: bool = False,
) -> ContextEntry:
    return ContextEntry(
        content=content,
        token_cost=estimate_tokens(content),
        refetch_tool=tool,
        refetch_args={} if args is None else args,
        priority=1,
        compacted=compacted,
        source=source,
    )


def _context(*entries: ContextEntry) -> Context:
    budget = sum(entry.token_cost for entry in entries)
    return Context(
        entries=entries,
        budget=budget,
        used_tokens=budget,
        trace=(
            ContextDecision(
                action="dropped",
                reason="over budget",
                estimated_tokens=4,
            ),
        ),
    )


def test_render_includes_selected_entries_and_omits_dropped_ones() -> None:
    """Selected content is in the string. A dropped candidate is not.

    A compacted selected entry contributes its compacted content, which
    already names the refetch tool and args. The string has no provider,
    model, or token-telemetry field.
    """

    dropped = "SECRET-DROPPED-SENTINEL"
    chunk = _entry(
        "Compacted chunk record. Refetch with get-chunk.",
        tool="get-chunk",
        args={"chunk_id": "c1"},
        compacted=True,
        source=ContextSource.RETRIEVED_RULES,
    )
    ruling = _entry(
        "Compacted ruling record.",
        tool="get-ruling",
        args={"ruling_id": "r1"},
        compacted=True,
        source=ContextSource.CAMPAIGN_RULINGS,
    )
    plain = _entry("Visible fact about the lantern.", tool=None)
    rendered = render_allocated_context(_context(chunk, ruling, plain))
    again = render_allocated_context(_context(chunk, ruling, plain))

    assert rendered == again
    assert dropped not in rendered
    assert chunk.content in rendered
    assert "get-chunk" in rendered
    assert "c1" in rendered
    assert ruling.content in rendered
    assert "get-ruling" in rendered
    assert "r1" in rendered
    assert plain.content in rendered
    assert "provider" not in rendered
    assert "tokens_in" not in rendered
    assert "tokens_out" not in rendered
    plain_only = render_allocated_context(_context(plain))
    assert "get-" not in plain_only


def test_rendered_extension_stays_within_the_wrapper_ceiling() -> None:
    normal = _context(_entry("A short selected fact."))
    rendered = render_allocated_context(normal)
    assert estimate_tokens(rendered) <= normal.budget + WRAPPER_TOKEN_CAP
    assert estimate_tokens(PROMPT_CONTEXT_HEADER) <= WRAPPER_TOKEN_CAP

    per_source = _context(
        *[
            _entry(f"Record for {source.value}.", source=source)
            for source in ContextSource
        ]
    )
    wide = render_allocated_context(per_source)
    assert estimate_tokens(wide) <= per_source.budget + WRAPPER_TOKEN_CAP
    for entry in per_source.entries:
        assert entry.content in wide
