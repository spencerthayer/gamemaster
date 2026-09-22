"""Closed transport contracts for model-proposed extraction data."""

from __future__ import annotations

import ast
import json
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from tabletop.api._contract import to_jsonable
from tabletop.documents.extraction import (
    InvalidProposedExtractionError,
    ProposedEntity,
    ProposedExtraction,
    ProposedFact,
)


def _entity() -> ProposedEntity:
    return ProposedEntity(
        entity_id="mara",
        owner_scope="campaign",
        setting_id=None,
        campaign_id="campaign-1",
        overrides_id=None,
        entity_type="npc",
        name="Mara",
        system_state={"level": 3},
        metadata={"source_name": "Chapter 1"},
    )


def _fact() -> ProposedFact:
    return ProposedFact(
        fact_id="fact-1",
        fact_scope="campaign",
        setting_id=None,
        campaign_id="campaign-1",
        subject_id="mara",
        predicate="lives_in",
        value="Greyhaven",
        source_document_id="document-1",
        source_chunk_id="chunk-1",
    )


def test_extraction_has_closed_frozen_proposal_schemas() -> None:
    extraction = ProposedExtraction(
        job_id="job-1",
        slice_index=2,
        extractor_version="extractor-1",
        entities=[_entity()],
        facts=[_fact()],
    )

    assert extraction.entities == (_entity(),)
    assert extraction.facts == (_fact(),)
    assert {field.name for field in fields(ProposedEntity)} == {
        "entity_id",
        "owner_scope",
        "setting_id",
        "campaign_id",
        "overrides_id",
        "entity_type",
        "name",
        "system_state",
        "metadata",
    }
    assert {field.name for field in fields(ProposedFact)} == {
        "fact_id",
        "fact_scope",
        "setting_id",
        "campaign_id",
        "subject_id",
        "predicate",
        "value",
        "canon_state",
        "visibility",
        "valid_from",
        "valid_until",
        "source_document_id",
        "source_chunk_id",
    }
    with pytest.raises(TypeError, match="unexpected"):
        ProposedFact(
            fact_id="fact-2",
            fact_scope="campaign",
            setting_id=None,
            campaign_id="campaign-1",
            subject_id=None,
            predicate="weather",
            value="rain",
            unknown="not allowed",
        )
    with pytest.raises(FrozenInstanceError):
        extraction.job_id = "changed"  # type: ignore[misc]


def test_fact_proposal_cannot_be_confirmed() -> None:
    with pytest.raises(InvalidProposedExtractionError, match="canon_state"):
        ProposedFact(
            fact_id="fact-1",
            fact_scope="campaign",
            setting_id=None,
            campaign_id="campaign-1",
            subject_id="mara",
            predicate="lives_in",
            value="Greyhaven",
            canon_state="confirmed",
        )


def test_json_round_trip_uses_contract_json_shape() -> None:
    extraction = ProposedExtraction(
        job_id="job-1",
        slice_index=0,
        extractor_version="extractor-1",
        entities=(_entity(),),
        facts=(_fact(),),
    )

    payload = json.loads(json.dumps(to_jsonable(extraction)))
    restored = ProposedExtraction.from_dict(payload)

    assert restored == extraction
    assert isinstance(restored.entities, tuple)
    assert isinstance(restored.entities[0].system_state, dict) is False
    assert to_jsonable(restored) == payload


def test_json_input_rejects_unknown_fields_at_every_level() -> None:
    payload = to_jsonable(
        ProposedExtraction(
            job_id="job-1",
            slice_index=0,
            extractor_version="extractor-1",
            entities=(_entity(),),
            facts=(_fact(),),
        )
    )

    for key_path in ("envelope", "entity", "fact"):
        changed = json.loads(json.dumps(payload))
        target = changed
        if key_path == "entity":
            target = changed["entities"][0]
        elif key_path == "fact":
            target = changed["facts"][0]
        target["unknown"] = True
        with pytest.raises(InvalidProposedExtractionError, match="unknown fields"):
            ProposedExtraction.from_dict(changed)


def test_extraction_module_has_no_campaign_store_write_path() -> None:
    module_path = (
        Path(__file__).resolve().parents[2]
        / "tabletop"
        / "documents"
        / "extraction.py"
    )
    tree = ast.parse(module_path.read_text())

    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    imports.update(
        f"{node.module}.{alias.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
        for alias in node.names
    )
    assert "tabletop.campaign.store" not in imports
