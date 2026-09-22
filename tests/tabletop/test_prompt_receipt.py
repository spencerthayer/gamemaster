"""Prompt context receipts are diagnostic and outside campaign replay."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from tabletop.campaign.event_store import EventStore
from tabletop.campaign.projections import project_campaign
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.prompt_context import PromptContextSnapshot
from tabletop.orchestration.prompt_receipt import record_prompt_context_receipt
from tabletop.storage.sqlite import connect, migrate


def _snapshot(
    *,
    workspace: str = "campaign",
    campaign_id: str | None = "campaign-a",
    digest: str = "a" * 64,
) -> PromptContextSnapshot:
    return PromptContextSnapshot(
        text="rendered text stays out of the receipt",
        campaign_id=campaign_id,
        workspace=workspace,
        context_budget=100,
        considered_count=3,
        selected_count=2,
        compacted_count=1,
        dropped_count=0,
        estimated_tokens=12,
        source_kinds=("facts", "setting_facts"),
        context_sha256=digest,
    )


def _prepare(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "receipts.sqlite3")
    migrate(connection)
    CampaignStore(connection).create_campaign("campaign-a", "Active", "freeform")
    return connection


def test_receipt_table_is_diagnostic_and_restart_stable(tmp_path: Path) -> None:
    connection = _prepare(tmp_path)
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(prompt_context_receipts)")
    }
    assert columns == {
        "receipt_id",
        "created_at",
        "campaign_id",
        "workspace",
        "context_budget",
        "considered_count",
        "selected_count",
        "compacted_count",
        "dropped_count",
        "estimated_tokens",
        "source_kinds",
        "context_sha256",
    }
    forbidden = {
        "utterance",
        "provider",
        "model",
        "tokens_in",
        "tokens_out",
        "cost",
        "retrieval_tier",
        "rendered_text",
    }
    assert columns.isdisjoint(forbidden)

    assert record_prompt_context_receipt(connection, _snapshot()) is True
    assert record_prompt_context_receipt(connection, _snapshot()) is True
    assert connection.execute(
        "SELECT COUNT(*) FROM prompt_context_receipts"
    ).fetchone()[0] == 1
    assert record_prompt_context_receipt(
        connection, _snapshot(digest="b" * 64)
    ) is True
    assert connection.execute(
        "SELECT COUNT(*) FROM prompt_context_receipts"
    ).fetchone()[0] == 2
    assert record_prompt_context_receipt(
        connection, _snapshot(workspace="setting", campaign_id=None, digest="a" * 64)
    ) is True
    assert record_prompt_context_receipt(
        connection, _snapshot(workspace="campaign", campaign_id=None, digest="a" * 64)
    ) is True
    assert connection.execute(
        "SELECT COUNT(*) FROM prompt_context_receipts"
    ).fetchone()[0] == 4
    kinds = connection.execute(
        "SELECT source_kinds FROM prompt_context_receipts LIMIT 1"
    ).fetchone()[0]
    assert kinds == '["facts","setting_facts"]'
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM setting_events").fetchone()[0] == 0
    project_campaign(EventStore(connection).read("campaign-a"))
    connection.execute("DELETE FROM prompt_context_receipts")
    connection.commit()
    assert connection.execute(
        "SELECT COUNT(*) FROM prompt_context_receipts"
    ).fetchone()[0] == 0
    connection.execute(
        "INSERT INTO events (campaign_id, sequence, event_type, payload, occurred_at) "
        "VALUES ('campaign-a', 1, 'fact.proposed', '{}', '2026-09-22T00:00:00Z')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM events")
    path = tmp_path / "receipts.sqlite3"
    record_prompt_context_receipt(connection, _snapshot(digest="c" * 64))
    connection.close()
    reopened = connect(path)
    migrate(reopened)
    row = reopened.execute(
        "SELECT workspace, context_sha256 FROM prompt_context_receipts "
        "WHERE context_sha256 = ?",
        ("c" * 64,),
    ).fetchone()
    assert row["workspace"] == "campaign"
    assert row["context_sha256"] == "c" * 64
    reopened.close()


def test_receipt_failure_is_visible_and_does_not_raise(caplog: pytest.LogCaptureFixture) -> None:
    class _Failing:
        def execute(self, *_args: object, **_kwargs: object) -> None:
            raise sqlite3.OperationalError("disk full")

    with caplog.at_level(logging.WARNING):
        stored = record_prompt_context_receipt(_Failing(), _snapshot())  # type: ignore[arg-type]
    assert stored is False
    assert any("prompt context receipt" in message for message in caplog.messages)
