"""Parsing and comparison for game-agnostic visibility scopes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from tabletop.api.errors import VisibilityScopeError


class VisibilityKind(str, Enum):
    """The supported kinds of visibility scope."""

    PUBLIC = "PUBLIC"
    PARTY = "PARTY"
    GM = "GM"
    CHARACTER = "CHARACTER"
    NPC = "NPC"
    FACTION = "FACTION"
    GROUP = "GROUP"


_BARE_KINDS = frozenset(
    {
        VisibilityKind.PUBLIC,
        VisibilityKind.PARTY,
        VisibilityKind.GM,
    }
)
_TARGETED_KINDS = frozenset(
    {
        VisibilityKind.CHARACTER,
        VisibilityKind.NPC,
        VisibilityKind.FACTION,
        VisibilityKind.GROUP,
    }
)


@dataclass(frozen=True)
class VisibilityScope:
    """A parsed visibility kind with an optional opaque target id."""

    kind: VisibilityKind
    target: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, VisibilityKind):
            raise VisibilityScopeError("visibility kind must be a VisibilityKind")
        if self.kind in _BARE_KINDS:
            if self.target is not None:
                raise VisibilityScopeError(
                    f"{self.kind.value} visibility cannot have a target"
                )
            return
        if (
            self.kind not in _TARGETED_KINDS
            or not isinstance(self.target, str)
            or not self.target
            or self.target != self.target.strip()
        ):
            raise VisibilityScopeError(
                f"{self.kind.value} visibility requires an unpadded target id"
            )

    def to_string(self) -> str:
        """Return the canonical scope string."""

        if self.target is None:
            return self.kind.value
        return f"{self.kind.value}:{self.target}"


@dataclass(frozen=True)
class Viewpoint:
    """A viewer identity and its explicitly supplied memberships."""

    scope: VisibilityScope
    party_member: bool = False
    faction_ids: frozenset[str] = field(default_factory=frozenset)
    group_ids: frozenset[str] = field(default_factory=frozenset)
    character_ids: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.scope, VisibilityScope):
            raise VisibilityScopeError("viewpoint scope must be a VisibilityScope")
        if not isinstance(self.party_member, bool):
            raise VisibilityScopeError("party membership must be a boolean")
        object.__setattr__(self, "faction_ids", frozenset(self.faction_ids))
        object.__setattr__(self, "group_ids", frozenset(self.group_ids))
        object.__setattr__(self, "character_ids", frozenset(self.character_ids))


def parse_scope(text: str) -> VisibilityScope:
    """Parse one canonical visibility scope string."""

    if not isinstance(text, str) or not text or text != text.strip():
        raise VisibilityScopeError("visibility scope must be an unpadded string")

    prefix, separator, target = text.partition(":")
    try:
        kind = VisibilityKind(prefix)
    except ValueError as error:
        raise VisibilityScopeError(f"unknown visibility scope {text!r}") from error

    if kind in _BARE_KINDS:
        if separator:
            raise VisibilityScopeError(f"{kind.value} visibility cannot have a target")
        return VisibilityScope(kind=kind)

    if not separator or not target or target != target.strip():
        raise VisibilityScopeError(
            f"{kind.value} visibility requires an unpadded target id"
        )
    return VisibilityScope(kind=kind, target=target)


def gm_viewpoint() -> Viewpoint:
    """The single GM viewpoint constructor used by the registered skill surface."""

    return Viewpoint(scope=parse_scope("GM"))


def can_see(viewer: Viewpoint, scope: VisibilityScope) -> bool:
    """Return whether ``viewer`` can see data with ``scope``."""

    if viewer.scope.kind is VisibilityKind.GM:
        return True
    if scope.kind is VisibilityKind.PUBLIC:
        return True
    if scope.kind is VisibilityKind.PARTY:
        return viewer.party_member
    if scope.kind is VisibilityKind.GM:
        return False
    if scope.kind is VisibilityKind.CHARACTER:
        return (
            viewer.scope.kind is VisibilityKind.CHARACTER
            and viewer.scope.target == scope.target
        ) or (scope.target is not None and scope.target in viewer.character_ids)
    if scope.kind is VisibilityKind.NPC:
        return viewer.scope.kind is scope.kind and viewer.scope.target == scope.target
    if scope.kind is VisibilityKind.FACTION:
        return (
            viewer.scope.kind is VisibilityKind.FACTION
            and viewer.scope.target == scope.target
        ) or scope.target in viewer.faction_ids
    if scope.kind is VisibilityKind.GROUP:
        return (
            viewer.scope.kind is VisibilityKind.GROUP
            and viewer.scope.target == scope.target
        ) or scope.target in viewer.group_ids

    raise AssertionError(f"unhandled visibility kind: {scope.kind}")
