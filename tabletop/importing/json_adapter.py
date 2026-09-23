"""JSON adapter for gamemaster-external/v1 structured campaigns."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tabletop.importing.interface import ImportBatch, ImportItem
from tabletop.importing.normalize import (
    normalize_control,
    normalize_entity,
    normalize_fact,
    normalize_participant,
)

FORMAT_ID = "gamemaster-external/v1"


class JsonCampaignImporter:
    def load(self, path: str) -> ImportBatch:
        root = Path(path).expanduser().resolve()
        payload = json.loads(root.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("external campaign must be a JSON object")
        if payload.get("format") != FORMAT_ID:
            raise ValueError(f"unsupported format {payload.get('format')!r}")
        provenance = {"source_path": str(root), "format": FORMAT_ID}
        items: list[ImportItem] = []
        for entity in payload.get("entities") or []:
            items.append(normalize_entity(entity, provenance=provenance))
        for participant in payload.get("participants") or []:
            items.append(normalize_participant(participant, provenance=provenance))
        for control in payload.get("controls") or []:
            items.append(normalize_control(control, provenance=provenance))
        for fact in payload.get("facts") or []:
            items.append(normalize_fact(fact, provenance=provenance))
        report: dict[str, Any] = {
            "entity_count": sum(1 for item in items if item.kind == "entity"),
            "fact_count": sum(1 for item in items if item.kind == "fact"),
            "unsupported": [],
        }
        return ImportBatch(
            format_id=FORMAT_ID,
            source_label=str(root),
            items=tuple(items),
            report=report,
        )
