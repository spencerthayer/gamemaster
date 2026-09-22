"""Tests for the authoritative SQLite campaign store."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from tabletop.api.errors import FactInvariantError, InvalidResolutionError
from tabletop.api.resolution import StateChange, StateOperation
from tabletop.campaign.models import (
    CanonState,
    Fact,
    FactScope,
    KnowledgeState,
)
from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate


@pytest.fixture
def store(tmp_path: Path) -> CampaignStore:
    conn = connect(tmp_path / "campaign.db")
    migrate(conn)
    result = CampaignStore(conn)
    try:
        yield result
    finally:
        conn.close()


def _create_campaign(store: CampaignStore, campaign_id: str = "campaign-1") -> None:
    store.create_campaign(
        campaign_id=campaign_id,
        name="The Long Night",
        system_id="test-system",
        created_at="2026-09-22T00:00:00Z",
    )


def _fact(
    *,
    fact_id: str = "fact-1",
    canon_state: CanonState = CanonState.CONFIRMED,
    knowledge_state: KnowledgeState = KnowledgeState.KNOWN,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        fact_scope=FactScope.CAMPAIGN,
        setting_id=None,
        campaign_id="campaign-1",
        subject_id="hero",
        predicate="has-title",
        value="Warden",
        canon_state=canon_state,
        knowledge_state=knowledge_state,
        created_at="2026-09-22T00:00:00Z",
    )


def test_creates_reads_and_lists_campaigns(store: CampaignStore) -> None:
    _create_campaign(store)

    campaign = store.get_campaign("campaign-1")

    assert campaign == {
        "campaign_id": "campaign-1",
        "name": "The Long Night",
        "system_id": "test-system",
        "setting_id": None,
        "created_at": "2026-09-22T00:00:00Z",
        "system_state": {},
    }
    assert store.list_campaigns() == [campaign]


def test_upserts_and_fetches_campaign_entity(store: CampaignStore) -> None:
    _create_campaign(store)

    store.upsert_entity(
        campaign_id="campaign-1",
        entity_id="hero",
        name="Mara",
        entity_type="npc",
        system_state={"resources": {"hp": 12}},
        metadata={"portrait": "mara.png"},
    )
    store.upsert_entity(
        campaign_id="campaign-1",
        entity_id="hero",
        name="Mara the Warden",
        entity_type="npc",
        system_state={"resources": {"hp": 10}},
        metadata={"portrait": "mara.png"},
    )

    assert store.get_entity("campaign-1", "hero") == {
        "entity_id": "hero",
        "owner_scope": "campaign",
        "setting_id": None,
        "campaign_id": "campaign-1",
        "overrides_id": None,
        "entity_type": "npc",
        "name": "Mara the Warden",
        "system_state": {"resources": {"hp": 10}},
        "metadata": {"portrait": "mara.png"},
    }


def test_adds_and_fetches_facts_after_invariant_check(store: CampaignStore) -> None:
    _create_campaign(store)
    fact = _fact()

    store.add_fact(fact)

    assert store.get_facts("campaign-1") == [fact]

    invalid = _fact(
        fact_id="invalid",
        canon_state=CanonState.PROPOSED,
        knowledge_state=KnowledgeState.KNOWN,
    )
    with pytest.raises(FactInvariantError):
        store.add_fact(invalid)
    assert store.get_facts("campaign-1") == [fact]


def test_applies_campaign_set_with_literal_dotted_key(store: CampaignStore) -> None:
    _create_campaign(store)

    store.apply_state_changes(
        "campaign-1",
        (
            StateChange(
                operation=StateOperation.SET,
                path=("campaign", "system", "status.effects", "blinded"),
                value=True,
            ),
        ),
    )

    assert store.get_campaign("campaign-1")["system_state"] == {
        "status.effects": {"blinded": True}
    }


def test_applies_entity_set_with_existing_list_index(store: CampaignStore) -> None:
    _create_campaign(store)
    store.upsert_entity(
        campaign_id="campaign-1",
        entity_id="hero",
        name="Mara",
        system_state={"slots": [{"used": False}]},
    )

    store.apply_state_changes(
        "campaign-1",
        (
            StateChange(
                operation=StateOperation.SET,
                path=("entities", "hero", "system", "slots", 0, "used"),
                value=True,
            ),
        ),
    )

    assert store.get_entity("campaign-1", "hero")["system_state"] == {
        "slots": [{"used": True}]
    }


def test_delete_removes_key_and_absent_key_is_noop(store: CampaignStore) -> None:
    _create_campaign(store)
    store.apply_state_changes(
        "campaign-1",
        (
            StateChange(
                operation=StateOperation.SET,
                path=("campaign", "system", "weather"),
                value="rain",
            ),
            StateChange(
                operation=StateOperation.DELETE,
                path=("campaign", "system", "weather"),
            ),
            StateChange(
                operation=StateOperation.DELETE,
                path=("campaign", "system", "already-absent"),
            ),
        ),
    )

    assert store.get_campaign("campaign-1")["system_state"] == {}


def test_scene_changes_require_matching_campaign_scene(store: CampaignStore) -> None:
    _create_campaign(store)
    store.conn.execute(
        "INSERT INTO scenes "
        "(scene_id, campaign_id, name, opened_at, system_state) "
        "VALUES (?, ?, ?, ?, ?)",
        ("scene-1", "campaign-1", "Crossroads", "2026-09-22T00:00:00Z", "{}"),
    )
    change = StateChange(
        operation=StateOperation.SET,
        path=("scene", "system", "round"),
        value=2,
    )

    with pytest.raises(InvalidResolutionError, match="scene_id"):
        store.apply_state_changes("campaign-1", (change,))

    store.apply_state_changes("campaign-1", (change,), scene_id="scene-1")
    row = store.conn.execute(
        "SELECT system_state FROM scenes WHERE scene_id = ?", ("scene-1",)
    ).fetchone()
    assert row["system_state"] == '{"round":2}'


@pytest.mark.parametrize(
    "path",
    [
        ("unknown", "system", "x"),
        ("campaign", "metadata", "x"),
        ("entities", "hero", "metadata", "x"),
        ("entities", "missing", "system", "x"),
        ("scene", "visibility", "x"),
    ],
)
def test_invalid_batch_applies_no_changes(
    store: CampaignStore, path: tuple[str | int, ...]
) -> None:
    _create_campaign(store)
    store.upsert_entity(
        campaign_id="campaign-1",
        entity_id="hero",
        name="Mara",
    )
    valid = StateChange(
        operation=StateOperation.SET,
        path=("campaign", "system", "should-not-exist"),
        value=True,
    )
    invalid = StateChange(operation=StateOperation.SET, path=path, value=True)

    with pytest.raises(InvalidResolutionError):
        store.apply_state_changes("campaign-1", (valid, invalid))

    assert store.get_campaign("campaign-1")["system_state"] == {}


def test_integer_path_requires_existing_list_and_valid_index(
    store: CampaignStore,
) -> None:
    _create_campaign(store)
    change = StateChange(
        operation=StateOperation.SET,
        path=("campaign", "system", "slots", 0),
        value="used",
    )

    with pytest.raises(InvalidResolutionError, match="list"):
        store.apply_state_changes("campaign-1", (change,))
    assert store.get_campaign("campaign-1")["system_state"] == {}


def test_state_change_has_no_plugin_supplied_previous_value() -> None:
    assert "previous_value" not in {field.name for field in fields(StateChange)}
