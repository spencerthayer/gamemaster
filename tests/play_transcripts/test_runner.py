"""The runner asserts structure and semantics, not exact prose."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.play_transcripts.runner import (
    TranscriptFailure,
    assert_case,
    run_case,
)
from tests.play_transcripts.schema import (
    ProposalScript,
    TranscriptCase,
    TurnExpectation,
    parse_case,
)


def _narrative_case() -> TranscriptCase:
    return TranscriptCase(
        case_id="runner-narrative",
        description="Ada talks and nothing mechanical happens",
        entities=(("pc-ada", "Ada"), ("npc-gate", "The Gate")),
        present=("npc-gate",),
        turns=(
            (
                ProposalScript(
                    actor_id="pc-ada",
                    intent="Ada asks the gate to open",
                    proposed_action_type=None,
                ),
                TurnExpectation(disposition="narrative", expect_events=()),
            ),
        ),
    )


def test_a_case_runs_through_the_real_runtime(tmp_path: Path) -> None:
    case = _narrative_case()
    run = run_case(case, tmp_path)
    assert len(run.turns) == 1
    assert run.turns[0].disposition == "narrative"
    assert_case(case, run)


def test_a_narrative_turn_produces_no_events(tmp_path: Path) -> None:
    case = _narrative_case()
    run = run_case(case, tmp_path)
    assert run.turns[0].events == ()


def test_a_narrative_turn_changes_no_state(tmp_path: Path) -> None:
    case = _narrative_case()
    run = run_case(case, tmp_path)
    assert run.turns[0].state_changed == ()


# -- failures name the phase ------------------------------------------------


def test_a_wrong_disposition_names_the_planner_phase(tmp_path: Path) -> None:
    case = _narrative_case()
    run = run_case(case, tmp_path)
    case.turns[0][1].__dict__  # frozen dataclass: rebuild below
    broken = TranscriptCase(
        case_id=case.case_id,
        description=case.description,
        entities=case.entities,
        present=case.present,
        turns=(
            (
                case.turns[0][0],
                TurnExpectation(disposition="resolve", expect_events=()),
            ),
        ),
    )
    with pytest.raises(TranscriptFailure, match="planner phase"):
        assert_case(broken, run)


def test_a_wrong_event_list_names_the_authoritative_phase(tmp_path: Path) -> None:
    case = _narrative_case()
    run = run_case(case, tmp_path)
    broken = TranscriptCase(
        case_id=case.case_id,
        description=case.description,
        entities=case.entities,
        present=case.present,
        turns=(
            (
                case.turns[0][0],
                TurnExpectation(
                    disposition="narrative", expect_events=("action.resolved",)
                ),
            ),
        ),
    )
    with pytest.raises(TranscriptFailure, match="authoritative phase"):
        assert_case(broken, run)


def test_a_missing_response_marker_names_the_response_phase(tmp_path: Path) -> None:
    case = _narrative_case()
    run = run_case(case, tmp_path)
    broken = TranscriptCase(
        case_id=case.case_id,
        description=case.description,
        entities=case.entities,
        present=case.present,
        turns=(
            (
                case.turns[0][0],
                TurnExpectation(
                    disposition="narrative",
                    response_contains_any=("the gate groans open",),
                ),
            ),
        ),
    )
    with pytest.raises(TranscriptFailure, match="response phase"):
        assert_case(broken, run)


def test_a_leaked_secret_names_the_visibility_phase(tmp_path: Path) -> None:
    case = _narrative_case()
    run = run_case(case, tmp_path)
    run.player_visible_text += "the passphrase is hunter2"
    broken = TranscriptCase(
        case_id=case.case_id,
        description=case.description,
        entities=case.entities,
        present=case.present,
        turns=(
            (
                case.turns[0][0],
                TurnExpectation(disposition="narrative", secret_sentinels=("hunter2",)),
            ),
        ),
    )
    with pytest.raises(TranscriptFailure, match="visibility phase"):
        assert_case(broken, run)


def test_a_turn_count_mismatch_is_reported(tmp_path: Path) -> None:
    case = _narrative_case()
    run = run_case(case, tmp_path)
    run.turns.append(run.turns[0])
    with pytest.raises(TranscriptFailure, match="expected 1 turns"):
        assert_case(case, run)


# -- case parsing -----------------------------------------------------------


def test_a_yaml_case_parses_into_the_schema() -> None:
    payload = yaml.safe_load(
        """
        case_id: yaml-case
        description: Ada talks
        entities:
          - [pc-ada, Ada]
        present: [npc-gate]
        turns:
          - proposal:
              actor_id: pc-ada
              intent: Ada asks the gate to open
            expect:
              disposition: narrative
              expect_events: []
        """
    )
    case = parse_case(payload)
    assert case.case_id == "yaml-case"
    assert case.entities == (("pc-ada", "Ada"),)
    assert case.turns[0][0].actor_id == "pc-ada"
    assert case.turns[0][1].disposition == "narrative"


def test_parsing_rejects_an_unknown_case_field() -> None:
    with pytest.raises(ValueError, match="unexpected fields"):
        parse_case(
            {
                "case_id": "c",
                "description": "d",
                "turns": [],
                "difficulty_class": 15,
            }
        )


def test_parsing_rejects_a_missing_required_field() -> None:
    with pytest.raises(ValueError, match="missing required fields"):
        parse_case({"case_id": "c", "turns": []})


# -- prose is not pinned ----------------------------------------------------


def test_the_runner_does_not_require_exact_prose(tmp_path: Path) -> None:
    """Two runs of the same case are both accepted despite differing text."""

    case = _narrative_case()
    first = run_case(case, tmp_path / "one")
    second = run_case(case, tmp_path / "two")
    assert_case(case, first)
    assert_case(case, second)
    # Structure must match even if the wording does not.
    assert first.turns[0].disposition == second.turns[0].disposition
    assert first.turns[0].events == second.turns[0].events
