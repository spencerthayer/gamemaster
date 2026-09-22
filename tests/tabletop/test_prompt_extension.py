import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
METTA_PATH = REPO_ROOT / "plugins" / "tabletop" / "tabletop.metta"
PROMPT_PATH = REPO_ROOT / "plugins" / "tabletop" / "prompt.md"
ADAPTER_PATH = REPO_ROOT / "plugins" / "tabletop" / "omega_tabletop_adapter.py"
PROMPT_CHARACTER_BUDGET = 4_000


def _load_adapter():
    spec = importlib.util.spec_from_file_location("prompt_policy_adapter", ADAPTER_PATH)
    assert spec is not None and spec.loader is not None
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    return adapter


def test_prompt_policy_is_not_registered_as_a_static_extension() -> None:
    metta = METTA_PATH.read_text(encoding="utf-8")
    adapter = ADAPTER_PATH.read_text(encoding="utf-8")

    assert "add-prompt-extension" not in metta
    assert "tabletop-runtime-policy" not in metta
    assert "(py-call (omega_tabletop_adapter.allocated_context_text))" in metta
    assert "load_prompt_policy" in adapter
    assert '"plugins" / "tabletop" / "prompt.md"' in adapter


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


def test_allocated_context_is_one_py_call() -> None:
    metta = METTA_PATH.read_text(encoding="utf-8")
    adapter = ADAPTER_PATH.read_text(encoding="utf-8")
    policy = PROMPT_PATH.read_text(encoding="utf-8")
    extension = metta.split("(= (prompt-extension tabletop-allocated-context)", 1)[1]
    extension = extension.split("\n\n", 1)[0]

    assert extension.count("py-call") == 1
    assert "(py-call (omega_tabletop_adapter.allocated_context_text))" in extension
    assert "record_allocated_context_receipt" not in metta
    assert "record_prompt_context_receipt" not in metta
    assert "_connection" not in adapter
    assert "Tabletop Runtime is authoritative" in policy
    assert "HUMAN-MSG" not in policy
    for path in (REPO_ROOT / "tabletop").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "import omega" not in text
        assert "import metta" not in text


def test_allocated_context_builds_one_snapshot_and_one_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tabletop.campaign.store import CampaignStore
    from tabletop.orchestration.prompt_context import PROMPT_CONTEXT_HEADER
    from tabletop.runtime import TabletopRuntime
    from tabletop.storage.sqlite import connect, migrate

    database = tmp_path / "prompt.sqlite3"
    connection = connect(database)
    migrate(connection)
    CampaignStore(connection).create_campaign("campaign-a", "Active", "freeform")
    connection.close()
    monkeypatch.setenv("TABLETOP_WORKSPACE", "campaign")
    monkeypatch.setenv("TABLETOP_CAMPAIGN", "campaign-a")
    monkeypatch.setenv("TABLETOP_DATABASE_PATH", str(database))
    adapter = _load_adapter()
    adapter.reset_runtime_for_tests()
    adapter.reset_skill_registration_for_tests()
    calls = {"snapshot": 0}
    original = TabletopRuntime.prompt_context_snapshot

    def _counting(self):
        calls["snapshot"] += 1
        return original(self)

    monkeypatch.setattr(TabletopRuntime, "prompt_context_snapshot", _counting)
    text = adapter.allocated_context_text()
    assert calls["snapshot"] == 1
    assert text.count("Tabletop Runtime is authoritative") == 1
    assert text.index("Tabletop Runtime is authoritative") < text.index(PROMPT_CONTEXT_HEADER)
    assert "HUMAN-MSG" not in text
    counted = connect(database)
    assert counted.execute(
        "SELECT COUNT(*) FROM prompt_context_receipts"
    ).fetchone()[0] == 1
    again = adapter.allocated_context_text()
    assert again == text
    assert calls["snapshot"] == 2
    assert counted.execute(
        "SELECT COUNT(*) FROM prompt_context_receipts"
    ).fetchone()[0] == 1

    def _boom(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("disk full")

    monkeypatch.setattr(
        "tabletop.runtime.record_prompt_context_receipt",
        _boom,
    )
    unchanged = adapter.allocated_context_text()
    assert unchanged == text
    assert counted.execute(
        "SELECT COUNT(*) FROM prompt_context_receipts"
    ).fetchone()[0] == 1
    counted.close()
