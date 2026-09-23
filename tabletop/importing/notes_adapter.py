"""Historical notes adapter for gamemaster-notes/v1."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from tabletop.importing.interface import ImportBatch, ImportItem
from tabletop.importing.store import ImportStore

FORMAT_ID = "gamemaster-notes/v1"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _confirmed_conflicts(
    conn: sqlite3.Connection,
    campaign_id: str,
    *,
    subject_id: str,
    predicate: str,
    value: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT fact_id, value FROM facts "
        "WHERE campaign_id = ? AND subject_id = ? AND predicate = ? "
        "AND canon_state = 'confirmed'",
        (campaign_id, subject_id, predicate),
    ).fetchall()
    conflicts: list[dict[str, Any]] = []
    for row in rows:
        if row["value"] != value:
            conflicts.append(
                {
                    "type": "staged_vs_canon",
                    "fact_id": row["fact_id"],
                    "existing_value": row["value"],
                    "proposed_value": value,
                }
            )
    return conflicts


def _staged_conflicts(
    pending: list[ImportItem], item: ImportItem
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    subject = item.payload.get("subject_id")
    predicate = item.payload.get("predicate")
    value = item.payload.get("value")
    for other in pending:
        if other.proposed_key == item.proposed_key:
            continue
        if (
            other.payload.get("subject_id") == subject
            and other.payload.get("predicate") == predicate
            and other.payload.get("value") != value
        ):
            conflicts.append(
                {
                    "type": "staged_vs_staged",
                    "keys": [item.proposed_key, other.proposed_key],
                }
            )
    return conflicts


class NotesCampaignImporter:
    """Stage markdown or text notes as proposed fact items."""

    def __init__(self, *, import_root: Path | None = None) -> None:
        self._import_root = import_root.resolve() if import_root else None

    def load(
        self,
        path: str,
        *,
        conn: sqlite3.Connection | None = None,
        campaign_id: str | None = None,
    ) -> ImportBatch:
        root = Path(path).expanduser().resolve()
        if self._import_root is not None:
            try:
                root.relative_to(self._import_root)
            except ValueError as exc:
                raise ValueError(
                    f"path {root} is outside import root {self._import_root}"
                ) from exc
        if root.suffix.lower() == ".pdf":
            return ImportBatch(
                format_id=FORMAT_ID,
                source_label=str(root),
                items=(),
                report={
                    "unsupported": "pdf_no_text_layer",
                    "message": "scanned or image-only PDFs need manual review",
                },
            )
        text = root.read_text(encoding="utf-8")
        digest = _sha256_text(text)
        provenance = {
            "source_path": str(root),
            "source_hash": digest,
            "format": FORMAT_ID,
        }
        draft: list[ImportItem] = []
        for index, line in enumerate(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            subject_id = "scene"
            predicate = "note"
            lower = line.lower()
            if lower.startswith("ada"):
                subject_id = "ada"
                if "alive" in lower or "missing" in lower:
                    predicate = "status"
            payload = {
                "fact_id": f"note-{index}",
                "subject_id": subject_id,
                "predicate": predicate,
                "value": line,
                "visibility": "GM",
                "knowledge_state": "unrevealed",
            }
            draft.append(
                ImportItem(
                    kind="fact",
                    payload=payload,
                    provenance=provenance,
                    proposed_key=f"note-{index}",
                    review={},
                )
            )

        items: list[ImportItem] = []
        for item in draft:
            review: dict[str, Any] = {"conflicts": []}
            review["conflicts"].extend(_staged_conflicts(draft, item))
            if conn is not None and campaign_id is not None:
                review["conflicts"].extend(
                    _confirmed_conflicts(
                        conn,
                        campaign_id,
                        subject_id=str(item.payload["subject_id"]),
                        predicate=str(item.payload["predicate"]),
                        value=str(item.payload["value"]),
                    )
                )
            items.append(
                ImportItem(
                    kind=item.kind,
                    payload=item.payload,
                    provenance=item.provenance,
                    proposed_key=item.proposed_key,
                    review=review,
                )
            )
        return ImportBatch(
            format_id=FORMAT_ID,
            source_label=str(root),
            items=tuple(items),
            report={
                "line_count": len(items),
                "source_hash": digest,
                "conflicts": sum(
                    1 for item in items if item.review.get("conflicts")
                ),
            },
        )


def stage_notes(
    conn: sqlite3.Connection,
    campaign_id: str,
    path: str,
    *,
    import_root: Path | None = None,
) -> str:
    batch = NotesCampaignImporter(import_root=import_root).load(
        path, conn=conn, campaign_id=campaign_id
    )
    return ImportStore(conn).stage_batch(campaign_id, batch)
