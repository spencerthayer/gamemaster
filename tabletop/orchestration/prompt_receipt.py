"""Diagnostic receipts for the pre-message tabletop prompt snapshot."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

from tabletop.orchestration.prompt_context import PromptContextSnapshot

logger = logging.getLogger(__name__)


def record_prompt_context_receipt(
    connection: sqlite3.Connection,
    snapshot: PromptContextSnapshot,
) -> bool:
    """Store one receipt when the digest changes. Failures do not raise."""

    try:
        latest = _latest_digest(connection, snapshot)
        if latest == snapshot.context_sha256:
            return True
        connection.execute(
            "INSERT INTO prompt_context_receipts ("
            "receipt_id, created_at, campaign_id, workspace, context_budget, "
            "considered_count, selected_count, compacted_count, dropped_count, "
            "estimated_tokens, source_kinds, context_sha256"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex,
                datetime.now(timezone.utc).isoformat(),
                snapshot.campaign_id,
                snapshot.workspace,
                snapshot.context_budget,
                snapshot.considered_count,
                snapshot.selected_count,
                snapshot.compacted_count,
                snapshot.dropped_count,
                snapshot.estimated_tokens,
                json.dumps(list(snapshot.source_kinds), separators=(",", ":")),
                snapshot.context_sha256,
            ),
        )
        connection.commit()
    except Exception:
        logger.warning(
            "prompt context receipt was not stored",
            exc_info=True,
        )
        return False
    return True


def _latest_digest(
    connection: sqlite3.Connection,
    snapshot: PromptContextSnapshot,
) -> str | None:
    if snapshot.campaign_id is None:
        row = connection.execute(
            "SELECT context_sha256 FROM prompt_context_receipts "
            "WHERE workspace = ? AND campaign_id IS NULL "
            "ORDER BY created_at DESC, receipt_id DESC LIMIT 1",
            (snapshot.workspace,),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT context_sha256 FROM prompt_context_receipts "
            "WHERE workspace = ? AND campaign_id = ? "
            "ORDER BY created_at DESC, receipt_id DESC LIMIT 1",
            (snapshot.workspace, snapshot.campaign_id),
        ).fetchone()
    if row is None:
        return None
    return str(row[0])
