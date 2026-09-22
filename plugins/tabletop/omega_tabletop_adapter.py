"""Omega-facing Python bridge for the tabletop MeTTa plugin.

``plugins/tabletop/tabletop.metta`` is the configured Omega plugin. It imports
this module and forwards skill calls into the standalone ``tabletop`` runtime.
No game logic lives here. Skills are registered once from the active
workspace payload; the adapter never re-registers.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME = None
_SKILLS_REGISTERED = False


def load_prompt_policy(path: str | Path) -> str:
    """Load the non-empty policy text registered during Omega startup."""
    policy = Path(path).read_text(encoding="utf-8").strip()
    if not policy:
        raise ValueError("Tabletop prompt policy file is empty")
    return policy


def ensure_runtime_importable():
    """Make the standalone ``tabletop`` runtime importable under Omega."""
    root = str(_REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    import tabletop

    return tabletop


def initialize():
    """Initialize and cache the pure-Python Tabletop Runtime."""
    global _RUNTIME
    ensure_runtime_importable()
    if _RUNTIME is None:
        from tabletop.runtime import TabletopRuntime

        _RUNTIME = TabletopRuntime.from_environment(_REPO_ROOT)
    return _RUNTIME


def reset_runtime_for_tests():
    """Clear the cached runtime. Intended only for isolated tests."""
    global _RUNTIME
    _RUNTIME = None


def reset_skill_registration_for_tests():
    """Clear the one-shot skill registration guard for isolated tests."""
    global _SKILLS_REGISTERED
    _SKILLS_REGISTERED = False


def initialize_response() -> str:
    return _invoke("bootstrap_status")


# Sentinel returned to MeTTa when registration must be a no-op. Never JSON:
# ``register-workspace-skills`` matches bare tokens ``setting`` / ``campaign``.
_ALREADY_REGISTERED = "already-registered"
_WORKSPACE_UNAVAILABLE = "workspace-unavailable"


def active_workspace() -> str:
    """Return the fixed workspace token for MeTTa registration branching.

    Success is always the bare enum value ``setting`` or ``campaign`` so
    MeTTa can match ``(register-workspace-skills setting)``. Failures return
    a bare sentinel token, never a JSON object string.
    """
    try:
        runtime = initialize()
        return runtime.workspace.value
    except Exception:
        return _WORKSPACE_UNAVAILABLE


def skill_registration_payload() -> str:
    """Return the workspace-scoped skill list used for Omega ``add-skill``."""
    try:
        runtime = initialize()
        return _encode(runtime.skill_registration_payload())
    except Exception as exc:
        return _encode(
            {
                "ok": False,
                "operation": "skill-registration-payload",
                "error": {
                    "code": "adapter_error",
                    "message": "Tabletop adapter call failed.",
                    "exception_type": type(exc).__name__,
                },
                "data": {},
            }
        )


def begin_skill_registration() -> str:
    """Return the registration payload exactly once per process.

    Omega ``add-skill`` is process-global. Re-registration would widen or
    swap the tool surface across conversations sharing the process. The
    MeTTa path must also call ``claim_skill_registration`` so a second
    ``loadOmegaPlugin`` does not run ``add-skill`` again.
    """
    global _SKILLS_REGISTERED
    try:
        runtime = initialize()
        if _SKILLS_REGISTERED:
            return _encode(
                {
                    "ok": False,
                    "operation": "begin-skill-registration",
                    "error": {
                        "code": "skills_already_registered",
                        "message": (
                            "Tabletop skills are registered once per process "
                            "from TABLETOP_WORKSPACE."
                        ),
                    },
                    "data": {"workspace": runtime.workspace.value},
                }
            )
        _SKILLS_REGISTERED = True
        return _encode(runtime.skill_registration_payload())
    except Exception as exc:
        return _encode(
            {
                "ok": False,
                "operation": "begin-skill-registration",
                "error": {
                    "code": "adapter_error",
                    "message": "Tabletop adapter call failed.",
                    "exception_type": type(exc).__name__,
                },
                "data": {},
            }
        )


def claim_skill_registration() -> str:
    """One-shot guard for the MeTTa ``add-skill`` path.

    First successful claim returns the bare workspace token (``setting`` or
    ``campaign``). Later calls return ``already-registered`` so
    ``register-workspace-skills`` is a no-op and does not widen the surface.
    """
    global _SKILLS_REGISTERED
    try:
        runtime = initialize()
        if _SKILLS_REGISTERED:
            return _ALREADY_REGISTERED
        _SKILLS_REGISTERED = True
        return runtime.workspace.value
    except Exception:
        return _WORKSPACE_UNAVAILABLE


def skills_already_registered() -> str:
    """Return ``true`` or ``false`` for MeTTa one-shot gating checks."""
    return "true" if _SKILLS_REGISTERED else "false"


def current_campaign() -> str:
    return _invoke("current_campaign")


def current_scene() -> str:
    return _invoke("current_scene")


def query_rules(query: Any) -> str:
    return _invoke("query_rules", _text(query))


def query_campaign(query: Any) -> str:
    return _invoke("query_campaign", _text(query))


def resolve_action(action: Any) -> str:
    return _invoke("resolve_action", _text(action))


def roll(expression: Any) -> str:
    return _invoke("roll", _text(expression))


def get_entity(entity_id: Any) -> str:
    return _invoke("get_entity", _text(entity_id))


def get_fact(fact_id: Any) -> str:
    return _invoke("get_fact", _text(fact_id))


def get_relationships(entity_id: Any) -> str:
    return _invoke("get_relationships", _text(entity_id))


def record_ruling(ruling: Any) -> str:
    return _invoke("record_ruling", _text(ruling))


def start_session(session: Any) -> str:
    return _invoke("start_session", _text(session))


def end_session() -> str:
    return _invoke("end_session")


def query_setting(query: Any) -> str:
    return _invoke("query_setting", _text(query))


def edit_setting(edit: Any) -> str:
    return _invoke("edit_setting", _text(edit))


def get_world_entity(entity_id: Any) -> str:
    return _invoke("get_world_entity", _text(entity_id))


def upsert_world_entity(entity: Any) -> str:
    return _invoke("upsert_world_entity", _text(entity))


def query_world_history(query: Any) -> str:
    return _invoke("query_world_history", _text(query))


def record_world_history(entry: Any) -> str:
    return _invoke("record_world_history", _text(entry))


def get_chunk(chunk_id: Any) -> str:
    return _invoke("get_chunk", _text(chunk_id))


def read_session(session_id: Any) -> str:
    return _invoke("read_session", _text(session_id))


def get_party_state() -> str:
    return _invoke("get_party_state")


def get_open_threads() -> str:
    return _invoke("get_open_threads")


def mutate_quest(quest: Any) -> str:
    return _invoke("mutate_quest", _text(quest))


def read_campaign_secret(secret_id: Any) -> str:
    return _invoke("read_campaign_secret", _text(secret_id))


def loadOmegaPlugin():
    """Remain directly loadable by Omega's Python loader for regression tests.

    Production registration is performed by ``tabletop.metta``. Returning the
    runtime package preserves the Phase 4 loader-boundary regression contract.
    """
    runtime_module = ensure_runtime_importable()
    initialize()
    return runtime_module


def _invoke(method_name: str, *args: Any) -> str:
    try:
        runtime = initialize()
        method = getattr(runtime, method_name)
        result = method(*args)
        return _encode(result)
    except Exception as exc:  # adapter boundary must not leak arbitrary exceptions
        return _encode(
            {
                "ok": False,
                "operation": method_name.replace("_", "-"),
                "error": {
                    "code": "adapter_error",
                    "message": "Tabletop adapter call failed.",
                    "exception_type": type(exc).__name__,
                },
                "data": {},
            }
        )


def _text(value: Any) -> str:
    """Convert Omega atom/string inputs into a bounded plain string value."""
    if value is None:
        return ""
    return str(value)


def _encode(value: Any) -> str:
    """Serialize only JSON-safe values returned across the Omega boundary."""
    normalized = _normalize(value)
    return json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _normalize(value: Any, *, depth: int = 0) -> Any:
    if depth > 20:
        raise ValueError("serialization nesting limit exceeded")
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float is not serializable")
        return value
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("response mappings require string keys")
            normalized[key] = _normalize(item, depth=depth + 1)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize(item, depth=depth + 1) for item in value]
    raise TypeError(f"unsupported response type: {type(value).__name__}")
