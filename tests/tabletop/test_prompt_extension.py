from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
METTA_PATH = REPO_ROOT / "plugins" / "tabletop" / "tabletop.metta"
PROMPT_PATH = REPO_ROOT / "plugins" / "tabletop" / "prompt.md"
PROMPT_CHARACTER_BUDGET = 4_000
PROMPT_HANDLE = "tabletop-runtime-policy"


def test_prompt_extension_loads_reviewable_policy_with_stable_handle() -> None:
    metta = METTA_PATH.read_text(encoding="utf-8")

    assert '(joinPath ((projectRootDirectory) "plugins" "tabletop" "prompt.md"))' in metta
    assert "(read-file $prompt-path)" in metta
    assert f"(add-prompt-extension {PROMPT_HANDLE} $prompt)" in metta


def test_prompt_extension_fails_startup_when_policy_is_missing_or_empty() -> None:
    metta = METTA_PATH.read_text(encoding="utf-8")

    assert "(not (exists-file $prompt-path))" in metta
    assert "(== $prompt \"\")" in metta
    assert metta.count("(Error (_loadOmegaPlugin)") >= 2


def test_prompt_policy_states_runtime_authority_limits() -> None:
    policy = PROMPT_PATH.read_text(encoding="utf-8").strip()
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
        "narration must reflect the plugin resolution rather than replace it"
        in normalized
    )


def test_prompt_policy_names_all_non_resolved_statuses() -> None:
    policy = PROMPT_PATH.read_text(encoding="utf-8")

    for status in ("RULING_REQUIRED", "UNRESOLVED", "UNSUPPORTED"):
        assert status in policy
