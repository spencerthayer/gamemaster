"""Allocated prompt text stays inside the selected context entries."""

from __future__ import annotations

import inspect
from pathlib import Path

from tabletop.api.visibility import Viewpoint, gm_viewpoint, parse_scope
from tabletop.api.workspace import Workspace
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.context import (
    Context,
    ContextDecision,
    ContextEntry,
    ContextRequest,
    ContextSource,
    build_context,
    estimate_tokens,
)
from tabletop.orchestration.prompt_context import (
    PROMPT_CONTEXT_ENTRY_CEILING,
    PROMPT_CONTEXT_HEADER,
    PROMPT_CONTEXT_MODEL_TOKENS,
    WRAPPER_TOKEN_CAP,
    build_prompt_context_snapshot,
    render_allocated_context,
)
from tabletop.storage.sqlite import connect, migrate


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


def _fact(
    fact_id: str,
    *,
    scope: FactScope,
    campaign_id: str | None,
    setting_id: str | None,
    value: str,
    visibility: str = "PUBLIC",
    canon: CanonState = CanonState.CONFIRMED,
    knowledge: KnowledgeState = KnowledgeState.KNOWN,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        fact_scope=scope,
        setting_id=setting_id,
        campaign_id=campaign_id,
        subject_id=fact_id,
        predicate="note",
        value=value,
        canon_state=canon,
        knowledge_state=knowledge,
        visibility=visibility,
        created_at="2026-09-22T00:00:00Z",
    )


def _database(tmp_path: Path):
    connection = connect(tmp_path / "prompt.sqlite3")
    migrate(connection)
    connection.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("setting-1", "Coast", "2026-09-22T00:00:00Z"),
    )
    CampaignStore(connection).create_campaign(
        "campaign-a", "Active", "freeform", setting_id="setting-1"
    )
    CampaignStore(connection).create_campaign(
        "campaign-b", "Other", "freeform", setting_id="setting-1"
    )
    return connection


def test_snapshot_uses_gm_viewpoint_and_the_active_campaign(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    store = CampaignStore(connection)
    store.add_fact(
        _fact(
            "active-fact",
            scope=FactScope.CAMPAIGN,
            campaign_id="campaign-a",
            setting_id=None,
            value="VISIBLE-ACTIVE-FACT",
        )
    )
    store.add_fact(
        _fact(
            "other-fact",
            scope=FactScope.CAMPAIGN,
            campaign_id="campaign-b",
            setting_id=None,
            value="OTHER-CAMPAIGN-SENTINEL",
        )
    )
    store.add_fact(
        _fact(
            "unrevealed-fact",
            scope=FactScope.CAMPAIGN,
            campaign_id="campaign-a",
            setting_id=None,
            value="GM-SEES-UNREVEALED",
            visibility="GM",
            canon=CanonState.PROPOSED,
            knowledge=KnowledgeState.UNREVEALED,
        )
    )
    store.add_fact(
        _fact(
            "bad-visibility",
            scope=FactScope.CAMPAIGN,
            campaign_id="campaign-a",
            setting_id=None,
            value="GM-DROPPED-SECRET",
            visibility="NOT_A_SCOPE",
        )
    )
    before = (
        connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        connection.execute("SELECT COUNT(*) FROM setting_events").fetchone()[0],
    )
    snapshot = build_prompt_context_snapshot(
        connection,
        workspace=Workspace.CAMPAIGN,
        campaign_id="campaign-a",
        setting_id="setting-1",
    )
    after = (
        connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        connection.execute("SELECT COUNT(*) FROM setting_events").fetchone()[0],
    )
    assert before == after
    assert "VISIBLE-ACTIVE-FACT" in snapshot.text
    assert "GM-SEES-UNREVEALED" in snapshot.text
    assert "OTHER-CAMPAIGN-SENTINEL" not in snapshot.text
    assert "GM-DROPPED-SECRET" not in snapshot.text
    parameters = inspect.signature(build_prompt_context_snapshot).parameters
    assert {"viewpoint", "scope", "character", "character_id"}.isdisjoint(parameters)
    source = Path("tabletop/orchestration/prompt_context.py").read_text(encoding="utf-8")
    assert "SELECT" not in source
    assert "INSERT" not in source

    gm_context = build_context(
        ContextRequest(
            source=_FactSource(
                store.get_facts("campaign-a", viewpoint=gm_viewpoint())
            ),
            workspace=Workspace.CAMPAIGN,
            viewpoint=gm_viewpoint(),
            campaign_id="campaign-a",
            query="",
            model_context_size=PROMPT_CONTEXT_MODEL_TOKENS,
            prompt_reserve=0,
            response_reserve=0,
            session_reserve=0,
            per_entry_ceiling=PROMPT_CONTEXT_ENTRY_CEILING,
        )
    )
    gm_text = "\n".join(entry.content for entry in gm_context.entries)
    assert "VISIBLE-ACTIVE-FACT" in gm_text
    assert "VISIBLE-ACTIVE-FACT" in snapshot.text
    character = build_context(
        ContextRequest(
            source=_FactSource(
                store.get_facts("campaign-a", viewpoint=gm_viewpoint())
            ),
            workspace=Workspace.CAMPAIGN,
            viewpoint=Viewpoint(scope=parse_scope("CHARACTER:hero")),
            campaign_id="campaign-a",
            query="",
            model_context_size=PROMPT_CONTEXT_MODEL_TOKENS,
            prompt_reserve=0,
            response_reserve=0,
            session_reserve=0,
            per_entry_ceiling=PROMPT_CONTEXT_ENTRY_CEILING,
        )
    )
    character_text = "\n".join(entry.content for entry in character.entries)
    assert "GM-SEES-UNREVEALED" not in character_text
    assert "GM-DROPPED-SECRET" not in character_text


def test_missing_campaign_does_not_scan_other_campaigns(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    CampaignStore(connection).add_fact(
        _fact(
            "active-fact",
            scope=FactScope.CAMPAIGN,
            campaign_id="campaign-a",
            setting_id=None,
            value="VISIBLE-ACTIVE-FACT",
        )
    )
    snapshot = build_prompt_context_snapshot(
        connection,
        workspace=Workspace.CAMPAIGN,
        campaign_id=None,
    )
    assert snapshot.text == "No campaign is selected."
    assert snapshot.considered_count == 0
    assert snapshot.selected_count == 0
    assert snapshot.dropped_count == 0
    assert "VISIBLE-ACTIVE-FACT" not in snapshot.text


def test_setting_snapshot_does_not_require_a_campaign(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    store = CampaignStore(connection)
    store.add_fact(
        _fact(
            "setting-fact",
            scope=FactScope.SETTING,
            campaign_id=None,
            setting_id="setting-1",
            value="SETTING-ONLY-FACT",
        )
    )
    store.add_fact(
        _fact(
            "campaign-fact",
            scope=FactScope.CAMPAIGN,
            campaign_id="campaign-a",
            setting_id=None,
            value="CAMPAIGN-ONLY-FACT",
        )
    )
    snapshot = build_prompt_context_snapshot(
        connection,
        workspace=Workspace.SETTING,
        campaign_id=None,
        setting_id="setting-1",
    )
    assert "SETTING-ONLY-FACT" in snapshot.text
    assert "CAMPAIGN-ONLY-FACT" not in snapshot.text
    assert snapshot.source_kinds == ("setting_facts",)
    assert snapshot.campaign_id is None


class _FactSource:
    def __init__(self, facts: list[Fact]) -> None:
        self._facts = tuple(facts)

    def read(self, source: ContextSource, request: ContextRequest) -> tuple[Fact, ...]:
        if source is ContextSource.FACTS:
            return self._facts
        return ()
