"""Omega-facing Python bridge for the tabletop MeTTa plugin.

``plugins/tabletop/tabletop.metta`` is the configured Omega plugin. It imports
this module and forwards skill calls into the standalone ``tabletop`` runtime.
No game logic lives here.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME = None


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


def initialize_response() -> str:
    return _invoke("bootstrap_status")


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


def get_relationships(entity_id: Any) -> str:
    return _invoke("get_relationships", _text(entity_id))


def record_ruling(ruling: Any) -> str:
    return _invoke("record_ruling", _text(ruling))


def end_session() -> str:
    return _invoke("end_session")


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
