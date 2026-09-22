import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
METTA_PATH = REPO_ROOT / "plugins" / "tabletop" / "tabletop.metta"
PROMPT_PATH = REPO_ROOT / "plugins" / "tabletop" / "prompt.md"
ADAPTER_PATH = REPO_ROOT / "plugins" / "tabletop" / "omega_tabletop_adapter.py"
PROMPT_CHARACTER_BUDGET = 4_000
PROMPT_HANDLE = "tabletop-runtime-policy"


def _load_adapter():
    spec = importlib.util.spec_from_file_location("prompt_policy_adapter", ADAPTER_PATH)
    assert spec is not None and spec.loader is not None
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    return adapter


def test_prompt_extension_loads_reviewable_policy_with_stable_handle() -> None:
    metta = METTA_PATH.read_text(encoding="utf-8")

    assert '(joinPath ((projectRootDirectory) "plugins" "tabletop" "prompt.md"))' in metta
    assert (
        "(py-call (omega_tabletop_adapter.load_prompt_policy $prompt-path))" in metta
    )
    assert f"(add-prompt-extension {PROMPT_HANDLE} $prompt)" in metta


def test_startup_prompt_loader_rejects_missing_and_whitespace_only_files(
    tmp_path: Path,
) -> None:
    load_prompt_policy = _load_adapter().load_prompt_policy
    missing_path = tmp_path / "missing.md"

    with pytest.raises(FileNotFoundError):
        load_prompt_policy(missing_path)

    whitespace_path = tmp_path / "whitespace.md"
    whitespace_path.write_text(" \n\t\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_prompt_policy(whitespace_path)


def test_prompt_policy_states_runtime_authority_limits() -> None:
    policy = _load_adapter().load_prompt_policy(PROMPT_PATH)
    normalized = " ".join(policy.lower().split())

    assert policy
    assert len(policy) <= PROMPT_CHARACTER_BUDGET
    assert "runtime is authoritative for game state" in normalized
    assert "never invent mechanical outcomes" in normalized
    assert "retrieve rules before making uncertain mechanical assertions" in normalized
    assert "visibility scoping is mandatory" in normalized
    assert "semantic recall is not authoritative" in normalized
    assert "disputed rules must include source references" in normalized
    assert "record durable rulings" in normalized
    assert (
        "do not author mechanical numbers such as difficulty classes, modifiers, "
        "damage, or thresholds"
        in normalized
    )
    assert (
        "narration must reflect the plugin resolution rather than replace it"
        in normalized
    )


def test_prompt_policy_names_all_non_resolved_statuses() -> None:
    policy = PROMPT_PATH.read_text(encoding="utf-8")

    for status in ("RULING_REQUIRED", "UNRESOLVED", "UNSUPPORTED"):
        assert status in policy
