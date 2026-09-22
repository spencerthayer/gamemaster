"""Private construction and JSON helpers for tabletop transport models."""

from __future__ import annotations

from copy import deepcopy
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
    """Deep-copy a JSON-shaped value and wrap mappings as MappingProxyType."""
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise error_cls(f"{field_name} keys must be strings, got {key!r}")
            frozen[key] = freeze_value(item, field_name, error_cls)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_value(item, field_name, error_cls) for item in value)
    return deepcopy(value)


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


def to_jsonable(value: Any) -> Any:
    """Convert transport values to JSON-compatible Python primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
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
    return value
