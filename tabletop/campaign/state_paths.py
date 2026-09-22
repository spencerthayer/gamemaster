"""Shared routing and JSON application for campaign state-change paths.

Store writes and event-log projections must resolve
``campaign`` / ``entities`` / ``scene`` roots the same way so the two
histories cannot drift.
"""

from __future__ import annotations

from typing import Any, assert_never

from tabletop.api.errors import InvalidResolutionError
from tabletop.api.resolution import StateChange, StateOperation

FORBIDDEN_PATH_COMPONENTS = frozenset(
    {
        "metadata",
        "owner_scope",
        "ownership",
        "setting_id",
        "campaign_id",
        "overrides_id",
        "provenance",
        "source_document_id",
        "source_chunk_id",
        "import_job_id",
        "extraction_method",
        "source_ownership",
        "canon",
        "canon_state",
        "knowledge",
        "knowledge_state",
        "visibility",
    }
)


def route_state_change_path(
    campaign_id: str,
    change: StateChange,
    scene_id: str | None,
) -> tuple[StateChange, tuple[str, str], tuple[str | int, ...]]:
    """Map a ``StateChange`` onto a store target and a relative JSON path."""

    if not isinstance(change, StateChange):
        raise InvalidResolutionError("changes must contain StateChange values")

    path = change.path
    root = path[0]
    if root == "campaign":
        if len(path) < 2 or path[1] != "system":
            raise InvalidResolutionError(
                "campaign state changes must start with ('campaign', 'system')"
            )
        relative_path = path[2:]
        target = ("campaign", campaign_id)
    elif root == "entities":
        if len(path) < 3 or not isinstance(path[1], str) or path[2] != "system":
            raise InvalidResolutionError(
                "entity state changes must start with "
                "('entities', '<entity-id>', 'system')"
            )
        relative_path = path[3:]
        target = ("entity", path[1])
    elif root == "scene":
        if len(path) < 2 or path[1] != "system":
            raise InvalidResolutionError(
                "scene state changes must start with ('scene', 'system')"
            )
        if scene_id is None:
            raise InvalidResolutionError("scene state changes require scene_id")
        relative_path = path[2:]
        target = ("scene", scene_id)
    else:
        raise InvalidResolutionError(
            "state change path root must be 'campaign', 'entities', or 'scene'"
        )

    forbidden = [
        component
        for component in relative_path
        if isinstance(component, str) and component in FORBIDDEN_PATH_COMPONENTS
    ]
    if forbidden:
        raise InvalidResolutionError(
            f"state change path may not write core-owned field {forbidden[0]!r}"
        )
    return change, target, relative_path


def apply_json_change(
    state: Any,
    path: tuple[str | int, ...],
    change: StateChange,
) -> Any:
    """Apply a ``StateChange`` to a JSON-compatible value at ``path``."""

    if not path:
        if change.operation is StateOperation.SET:
            return change.to_dict()["value"]
        if change.operation is StateOperation.DELETE:
            return {}
        assert_never(change.operation)

    current = state
    for component in path[:-1]:
        if isinstance(component, str):
            if not isinstance(current, dict):
                raise InvalidResolutionError(
                    f"path component {component!r} requires an existing object"
                )
            if component not in current:
                if change.operation is StateOperation.DELETE:
                    return state
                current[component] = {}
            current = current[component]
        else:
            current = _existing_list_item(current, component)

    final = path[-1]
    if isinstance(final, str):
        if not isinstance(current, dict):
            raise InvalidResolutionError(
                f"path component {final!r} requires an existing object"
            )
        if change.operation is StateOperation.SET:
            current[final] = change.to_dict()["value"]
        elif change.operation is StateOperation.DELETE:
            current.pop(final, None)
        else:
            assert_never(change.operation)
    else:
        if not isinstance(current, list):
            raise InvalidResolutionError(
                f"integer path component {final} requires an existing list"
            )
        if final >= len(current):
            raise InvalidResolutionError(
                f"list index {final} is out of range for state change path"
            )
        if change.operation is StateOperation.SET:
            current[final] = change.to_dict()["value"]
        elif change.operation is StateOperation.DELETE:
            del current[final]
        else:
            assert_never(change.operation)
    return state


def _existing_list_item(current: Any, index: int) -> Any:
    if not isinstance(current, list):
        raise InvalidResolutionError(
            f"integer path component {index} requires an existing list"
        )
    if index >= len(current):
        raise InvalidResolutionError(
            f"list index {index} is out of range for state change path"
        )
    return current[index]
