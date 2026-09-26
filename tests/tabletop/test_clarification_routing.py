"""Ambiguity is resolved in a fixed order, and lookups write nothing.

The order matters: attached rules, then a current unsuperseded ruling, then
the GM. A model-proposed value never reaches the plugin, and no lookup
silently records a fact or a ruling as a side effect.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.api.actions import (
    ActionProposal,
    MechanicalParameter,
    ParameterSource,
)
from tabletop.campaign.models import CanonState, KnowledgeState
from tabletop.campaign.rulings import Ruling, RulingStore
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore
from tabletop.orchestration.clarification import (
    ClarificationStore,
    RuleLookup,
    StateLookup,
    resolve_lookup,
)
from tabletop.orchestration.planner import Disposition, PlanContext, plan_resolution
from tabletop.storage.sqlite import connect, migrate

_CAMPAIGN = "campaign-1"


class _Rules:
    """A rule source that answers a configured lookup and records its calls."""

    def __init__(self, answers: dict[str, object]) -> None:
        self._answers = answers
        self.calls: list[tuple[str, ...]] = []

    def lookup(self, names: tuple[str, ...], campaign_id: str) -> dict[str, object]:
        self.calls.append(names)
        return {name: self._answers[name] for name in names if name in self._answers}


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "clarification.db")
    migrate(connection)
    CampaignStore(connection).create_campaign(_CAMPAIGN, "First", "freeform")
    for entity_id in ("pc-ada", "npc-gate", "npc-hound"):
        CampaignStore(connection).upsert_entity(_CAMPAIGN, entity_id, entity_id)
    SceneStore(connection).open_scene(_CAMPAIGN, "scene-1", "Crossroads")
    yield connection
    connection.close()


def _proposal(**overrides: object) -> ActionProposal:
    payload: dict[str, object] = {
        "actor_id": "pc-ada",
        "intent": "Ada forces the gate",
        "proposed_action_type": "ability_check",
        "target_refs": ("npc-gate",),
        "parameters": {"ability": "strength"},
        "uncertainty": None,
        "needs_resolution": True,
    }
    payload.update(overrides)
    return ActionProposal(**payload)  # type: ignore[arg-type]


def _context(present: tuple[str, ...] = ("pc-ada", "npc-gate", "npc-hound")) -> PlanContext:
    return PlanContext(
        present_entity_ids=present,
        actor_controls_actor=True,
        player_controlled_entity_ids=("pc-ada",),
    )


# -- multiple hostiles is a player question, not a GM question ---------------


def test_multiple_present_hostiles_produce_a_player_clarification(conn: sqlite3.Connection) -> None:
    plan = plan_resolution(
        _proposal(target_refs=()),
        plugin=_TestPlugin({"ability_check": ("dc",)}),
        context=_context(),
        parameters=(
            MechanicalParameter(name="dc", value=15, source=ParameterSource.RULING),
        ),
    )
    assert plan.disposition is Disposition.PLAYER_CLARIFICATION
    assert plan.disposition is not Disposition.GM_RULING
    assert plan.clarification is not None
    assert set(plan.clarification.candidate_refs) == {"npc-gate", "npc-hound"}


def test_a_clarification_binds_to_exactly_one_pending_turn(conn: sqlite3.Connection) -> None:
    store = ClarificationStore(conn)
    first = store.open("turn-1", question="Which one?", candidates=("npc-gate", "npc-hound"))
    second = store.open("turn-2", question="Which one?", candidates=("npc-a", "npc-b"))
    assert store.pending_for_turn("turn-1") is not None
    assert store.pending_for_turn("turn-2") is not None

    resolved = store.answer("turn-1", "npc-gate")
    assert resolved == "npc-gate"
    assert store.pending_for_turn("turn-1") is None
    # Answering one turn must not resolve another.
    assert store.pending_for_turn("turn-2") is not None
    assert first.turn_id == "turn-1"
    assert second.turn_id == "turn-2"


def test_answering_a_clarification_with_an_unlisted_choice_is_rejected(
    conn: sqlite3.Connection,
) -> None:
    store = ClarificationStore(conn)
    store.open("turn-1", question="Which one?", candidates=("npc-gate", "npc-hound"))
    with pytest.raises(ValueError):
        store.answer("turn-1", "npc-somewhere-else")


def test_answering_a_turn_with_no_pending_clarification_is_rejected(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(LookupError):
        ClarificationStore(conn).answer("turn-1", "npc-gate")


# -- lookup order -----------------------------------------------------------


def test_attached_rules_are_consulted_before_a_ruling(conn: sqlite3.Connection) -> None:
    rules = _Rules({"dc": 13})
    order = resolve_lookup(
        missing=("dc",),
        rules=RuleLookup(rules),
        rulings=RulingStore(conn),
        state=StateLookup({}),
        campaign_id=_CAMPAIGN,
    )
    assert order.disposition is Disposition.RESOLVE
    assert rules.calls == [("dc",)]
    assert order.parameters["dc"].source is ParameterSource.ATTACHED_RULES
    assert order.parameters["dc"].value == 13


def test_a_current_ruling_answers_when_rules_do_not(conn: sqlite3.Connection) -> None:
    RulingStore(conn).record(
        Ruling(
            ruling_id="ruling-1",
            campaign_id=_CAMPAIGN,
            system_id="freeform",
            question="What is the gate's DC?",
            decision="15",
            scope="gate",
            source_references=(),
            session_id=None,
            created_at="2026-09-25T00:00:00Z",
            canon_state=CanonState.CONFIRMED,
            knowledge_state=KnowledgeState.KNOWN,
        )
    )
    order = resolve_lookup(
        missing=("dc",),
        rules=RuleLookup(_Rules({})),
        rulings=RulingStore(conn),
        state=StateLookup({}),
        campaign_id=_CAMPAIGN,
        query="gate",
    )
    assert order.disposition is Disposition.RESOLVE
    assert order.parameters["dc"].source is ParameterSource.RULING


def test_a_superseded_ruling_does_not_answer(conn: sqlite3.Connection) -> None:
    store = RulingStore(conn)
    store.record(
        Ruling(
            ruling_id="ruling-old",
            campaign_id=_CAMPAIGN,
            system_id="freeform",
            question="Gate DC?",
            decision="10",
            scope="gate",
            source_references=(),
            session_id=None,
            created_at="2026-09-25T00:00:00Z",
            canon_state=CanonState.CONFIRMED,
            knowledge_state=KnowledgeState.KNOWN,
        )
    )
    store.promote("ruling-old")
    store.record(
        Ruling(
            ruling_id="ruling-new",
            campaign_id=_CAMPAIGN,
            system_id="freeform",
            question="Gate DC?",
            decision="15",
            scope="gate",
            source_references=(),
            session_id=None,
            created_at="2026-09-25T01:00:00Z",
            canon_state=CanonState.CONFIRMED,
            knowledge_state=KnowledgeState.KNOWN,
            supersedes="ruling-old",
        )
    )
    order = resolve_lookup(
        missing=("dc",),
        rules=RuleLookup(_Rules({})),
        rulings=store,
        state=StateLookup({}),
        campaign_id=_CAMPAIGN,
        query="gate",
    )
    assert order.parameters["dc"].value == 15


def test_campaign_state_answers_a_non_rules_parameter(conn: sqlite3.Connection) -> None:
    order = resolve_lookup(
        missing=("path",),
        rules=RuleLookup(_Rules({})),
        rulings=RulingStore(conn),
        state=StateLookup({"path": ("scene", "system", "lock")}),
        campaign_id=_CAMPAIGN,
    )
    assert order.disposition is Disposition.RESOLVE
    assert order.parameters["path"].source is ParameterSource.CAMPAIGN_STATE


def test_nothing_answering_escalates_to_the_gm(conn: sqlite3.Connection) -> None:
    order = resolve_lookup(
        missing=("dc",),
        rules=RuleLookup(_Rules({})),
        rulings=RulingStore(conn),
        state=StateLookup({}),
        campaign_id=_CAMPAIGN,
        query="gate",
    )
    assert order.disposition is Disposition.GM_RULING
    assert order.missing_parameters == ("dc",)


def test_a_lookup_writes_no_facts_or_rulings(conn: sqlite3.Connection) -> None:
    before_facts = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    before_rulings = conn.execute("SELECT COUNT(*) FROM rulings").fetchone()[0]
    before_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    resolve_lookup(
        missing=("dc",),
        rules=RuleLookup(_Rules({"dc": 13})),
        rulings=RulingStore(conn),
        state=StateLookup({}),
        campaign_id=_CAMPAIGN,
    )
    assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == before_facts
    assert conn.execute("SELECT COUNT(*) FROM rulings").fetchone()[0] == before_rulings
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before_events


def test_a_model_proposed_dc_cannot_reach_the_plugin(conn: sqlite3.Connection) -> None:
    """The whole point: a guessed number must not become a rules value."""

    order = resolve_lookup(
        missing=("dc",),
        rules=RuleLookup(_Rules({})),
        rulings=RulingStore(conn),
        state=StateLookup({}),
        campaign_id=_CAMPAIGN,
        model_proposed=(
            MechanicalParameter(
                name="dc", value=15, source=ParameterSource.MODEL_PROPOSAL
            ),
        ),
        query="gate",
    )
    assert order.disposition is Disposition.GM_RULING
    assert "dc" not in order.parameters


def test_a_ruling_citation_is_preserved(conn: sqlite3.Connection) -> None:
    RulingStore(conn).record(
        Ruling(
            ruling_id="ruling-1",
            campaign_id=_CAMPAIGN,
            system_id="freeform",
            question="Gate DC?",
            decision="15",
            scope="gate",
            source_references=(),
            session_id=None,
            created_at="2026-09-25T00:00:00Z",
            canon_state=CanonState.CONFIRMED,
            knowledge_state=KnowledgeState.KNOWN,
        )
    )
    order = resolve_lookup(
        missing=("dc",),
        rules=RuleLookup(_Rules({})),
        rulings=RulingStore(conn),
        state=StateLookup({}),
        campaign_id=_CAMPAIGN,
        query="gate",
    )
    assert order.parameters["dc"].reference is not None
    assert "ruling-1" in order.parameters["dc"].reference


# -- shared test plugin -----------------------------------------------------


from tabletop.api.capabilities import Capability  # noqa: E402
from tabletop.api.plugin import (  # noqa: E402
    TABLETOP_PLUGIN_API_VERSION,
    GameSystemInfo,
    GameSystemPlugin,
)


class _TestPlugin(GameSystemPlugin):
    def __init__(self, requirements: dict[str, tuple[str, ...]]) -> None:
        self._requirements = requirements

    @property
    def info(self) -> GameSystemInfo:
        return GameSystemInfo(
            id="clarification-test",
            name="Clarification Test",
            api_version=TABLETOP_PLUGIN_API_VERSION,
        )

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.ACTION_RESOLUTION})

    def action_requirements(self, action_type: str) -> tuple[str, ...]:
        return self._requirements.get(action_type, ())

    def handles_action(self, action_type: str) -> bool:
        return action_type in self._requirements

    def resolve(self, action, context):  # pragma: no cover
        raise AssertionError("the planner must not call the plugin")
