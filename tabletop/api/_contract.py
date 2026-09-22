"""Private construction and JSON helpers for tabletop transport models."""

from __future__ import annotations

import math
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, TypeVar

T = TypeVar("T")


def require_non_empty_str(value: Any, field_name: str, error_cls: type[Exception]) -> str:
    if not isinstance(value, str) or not value.strip():
        raise error_cls(f"{field_name} must be a non-empty string, got {value!r}")
    return value


def optional_non_empty_str(
    value: Any, field_name: str, error_cls: type[Exception]
) -> str | None:
    if value is None:
        return None
    return require_non_empty_str(value, field_name, error_cls)


def freeze_value(value: Any, field_name: str, error_cls: type[Exception]) -> Any:
    """Validate and freeze a JSON-shaped value.

    Allowed: None, str, bool, int, finite float, string-keyed mappings, and
    lists/tuples of allowed values. Mappings are wrapped in MappingProxyType.
    Sequences become tuples. Everything else is rejected at construction so
    later ``json.dumps`` cannot fail on a supposedly safe transport object.
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise error_cls(
                f"{field_name} floats must be finite JSON numbers, got {value!r}"
            )
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise error_cls(f"{field_name} keys must be strings, got {key!r}")
            frozen[key] = freeze_value(item, field_name, error_cls)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_value(item, field_name, error_cls) for item in value)
    raise error_cls(
        f"{field_name} must be JSON-compatible (None, str, bool, int, finite "
        f"float, mapping, or sequence), got {type(value).__name__}"
    )


def freeze_mapping(
    value: Any, field_name: str, error_cls: type[Exception]
) -> Mapping[str, Any]:
    if value is None:
        mapping: Mapping[str, Any] = {}
    elif isinstance(value, Mapping):
        mapping = value
    else:
        raise error_cls(f"{field_name} must be a mapping, got {type(value).__name__}")
    return freeze_value(mapping, field_name, error_cls)


def freeze_tuple(
    value: Any,
    item_type: type[T],
    field_name: str,
    error_cls: type[Exception],
) -> tuple[T, ...]:
    if value is None:
        return ()
    if isinstance(value, item_type):
        raise error_cls(
            f"{field_name} must be a sequence of {item_type.__name__}, "
            f"got a single {item_type.__name__}"
        )
    if isinstance(value, (str, bytes, Mapping)):
        raise error_cls(f"{field_name} must be a sequence of {item_type.__name__}")
    try:
        items = tuple(value)
    except TypeError as exc:
        raise error_cls(f"{field_name} must be a sequence of {item_type.__name__}") from exc
    for item in items:
        if not isinstance(item, item_type):
            raise error_cls(
                f"{field_name} items must be {item_type.__name__} instances, "
                f"got {type(item).__name__}"
            )
    return items


def freeze_state_path(value: Any, error_cls: type[Exception]) -> tuple[str | int, ...]:
    """Freeze an unambiguous state path as components.

    Each component is a non-empty string or a non-negative int. Dots inside a
    string component are literal key characters, not separators, so an entity
    id ``cultist.1`` stays one component.
    """
    if isinstance(value, (str, bytes, Mapping)) or isinstance(value, bool):
        raise error_cls(
            "path must be a sequence of non-empty strings or non-negative ints"
        )
    try:
        items = tuple(value)
    except TypeError as exc:
        raise error_cls(
            "path must be a sequence of non-empty strings or non-negative ints"
        ) from exc
    if not items:
        raise error_cls("path must contain at least one component")
    frozen: list[str | int] = []
    for item in items:
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            raise error_cls(
                "path components must be non-empty strings or non-negative ints, "
                f"got {type(item).__name__}"
            )
        if isinstance(item, str) and not item:
            raise error_cls("path string components must be non-empty")
        if isinstance(item, int) and item < 0:
            raise error_cls("path integer components must be non-negative")
        frozen.append(item)
    return tuple(frozen)


def to_jsonable(value: Any) -> Any:
    """Convert already-validated transport values to JSON primitives."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError(f"non-finite float is not JSON-safe: {value!r}")
        return value
    if isinstance(value, Enum):
        return value.value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    raise TypeError(f"value is not JSON-safe: {type(value).__name__}")
