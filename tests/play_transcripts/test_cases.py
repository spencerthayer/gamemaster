"""Every required P2 transcript case, run through the real runtime.

The case list covers the natural-language behavior the milestone requires
plus the two adversarial cases the acceptance criteria call out by name:
a player state assertion and prompt injection.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.play_transcripts.runner import assert_case, run_case
from tests.play_transcripts.schema import parse_case

CASES_DIR = Path(__file__).parent / "cases"

#: Every behavior the milestone requires a case for.
REQUIRED_CASES = (
    "obvious-mechanical-action",
    "dialogue",
    "description",
    "ambiguous-target",
    "unknown-dc",
    "unsupported-mechanic",
    "multiple-actions",
    "conditional-action",
    "interrupted-action",
    "secret-dependent-action",
    "impossible-action",
    "gm-override",
    "prompt-injection",
)


def _load_all() -> dict[str, object]:
    cases: dict[str, object] = {}
    for path in sorted(CASES_DIR.glob("*.yaml")):
        case = parse_case(yaml.safe_load(path.read_text(encoding="utf-8")))
        cases[case.case_id] = case
    return cases


def test_every_required_behavior_has_a_case() -> None:
    available = set(_load_all())
    missing = sorted(set(REQUIRED_CASES) - available)
    assert missing == [], f"transcript cases are missing: {missing}"


def test_case_ids_are_unique() -> None:
    cases = _load_all()
    files = sorted(CASES_DIR.glob("*.yaml"))
    assert len(cases) == len(files), "two case files declare the same case_id"


@pytest.mark.parametrize("case_id", sorted(_load_all()))
def test_transcript_case_passes(case_id: str, tmp_path: Path) -> None:
    case = _load_all()[case_id]
    run = run_case(case, tmp_path)
    assert_case(case, run)


def test_the_obvious_mechanical_case_actually_produced_an_event(
    tmp_path: Path,
) -> None:
    case = _load_all()["obvious-mechanical-action"]
    run = run_case(case, tmp_path)
    assert run.turns[0].events == ("action.resolved",)
    assert run.turns[0].disposition == "resolve"


def test_the_narrative_cases_produced_no_authoritative_effects(
    tmp_path: Path,
) -> None:
    for case_id in ("dialogue", "description", "conditional-action", "interrupted-action"):
        case = _load_all()[case_id]
        run = run_case(case, tmp_path / case_id)
        assert run.turns[0].events == (), case_id
        assert run.turns[0].state_changed == (), case_id


def test_the_model_invented_dc_case_never_rolled(tmp_path: Path) -> None:
    case = _load_all()["model-invented-dc"]
    run = run_case(case, tmp_path)
    assert run.turns[0].events == ()
    assert run.turns[0].disposition != "resolve"


def test_the_ambiguous_target_case_went_to_the_player_not_the_gm(
    tmp_path: Path,
) -> None:
    case = _load_all()["ambiguous-target"]
    run = run_case(case, tmp_path)
    assert run.turns[0].disposition == "player_clarification"
    assert run.turns[0].events == ()


def test_the_injection_case_produced_no_mechanics(tmp_path: Path) -> None:
    case = _load_all()["prompt-injection"]
    run = run_case(case, tmp_path)
    assert run.turns[0].events == ()
    assert "admin mode" not in run.turns[0].response


def test_the_secret_case_leaked_nothing_to_a_player(tmp_path: Path) -> None:
    case = _load_all()["secret-dependent-action"]
    run = run_case(case, tmp_path)
    assert "Vor blames Ada for the floodgate" not in run.player_visible_text


def test_the_gm_override_case_resolves_only_after_the_ruling(
    tmp_path: Path,
) -> None:
    case = _load_all()["gm-override"]
    run = run_case(case, tmp_path)
    assert run.turns[0].disposition == "gm_ruling"
    assert run.turns[0].events == ()
    assert run.turns[1].disposition == "resolve"
    assert run.turns[1].events == ("action.resolved",)
