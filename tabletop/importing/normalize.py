"""Normalize opaque external payloads into proposed import items."""

from __future__ import annotations

from typing import Any, Mapping

from tabletop.importing.interface import ImportItem


def normalize_entity(raw: Mapping[str, Any], *, provenance: Mapping[str, Any]) -> ImportItem:
    entity_id = str(raw.get("id") or raw.get("entity_id") or "").strip()
    system_state = dict(raw.get("system_state") or {})
    for key in ("level",):
        if key in raw and key not in system_state:
            system_state[key] = raw[key]
    payload = {
        "entity_id": entity_id,
        "name": raw.get("name") or entity_id,
        "entity_type": raw.get("entity_type") or raw.get("kind") or "character",
        "system_state": system_state,
    }
    return ImportItem(
        kind="entity",
        payload=payload,
        provenance=dict(provenance),
        proposed_key=entity_id or None,
    )


def normalize_fact(raw: Mapping[str, Any], *, provenance: Mapping[str, Any]) -> ImportItem:
    payload = {
        "fact_id": raw.get("fact_id"),
        "subject_id": raw.get("subject_id"),
        "predicate": raw.get("predicate"),
        "value": raw.get("value"),
        "visibility": raw.get("visibility", "GM"),
    }
    return ImportItem(
        kind="fact",
        payload=payload,
        provenance=dict(provenance),
        proposed_key=str(raw.get("fact_id") or "") or None,
    )


def normalize_participant(
    raw: Mapping[str, Any], *, provenance: Mapping[str, Any]
) -> ImportItem:
    participant_id = str(raw.get("id") or raw.get("participant_id") or "").strip()
    payload = {
        "participant_id": participant_id,
        "display_name": raw.get("display_name") or raw.get("name") or participant_id,
        "role": raw.get("role", "player"),
    }
    return ImportItem(
        kind="participant",
        payload=payload,
        provenance=dict(provenance),
        proposed_key=participant_id or None,
    )


def normalize_control(
    raw: Mapping[str, Any], *, provenance: Mapping[str, Any]
) -> ImportItem:
    payload = {
        "control_id": raw.get("control_id"),
        "participant_id": raw.get("participant_id"),
        "entity_id": raw.get("entity_id"),
        "control": raw.get("control", "owner"),
    }
    return ImportItem(
        kind="control",
        payload=payload,
        provenance=dict(provenance),
        proposed_key=str(raw.get("control_id") or "") or None,
    )
