from dataclasses import FrozenInstanceError

import pytest

from tabletop.api.errors import VisibilityScopeError
from tabletop.api.visibility import (
    Viewpoint,
    VisibilityKind,
    VisibilityScope,
    can_see,
    parse_scope,
)


@pytest.mark.parametrize(
    ("text", "kind", "target"),
    [
        ("PUBLIC", VisibilityKind.PUBLIC, None),
        ("PARTY", VisibilityKind.PARTY, None),
        ("GM", VisibilityKind.GM, None),
        ("CHARACTER:character-1", VisibilityKind.CHARACTER, "character-1"),
        ("NPC:npc-1", VisibilityKind.NPC, "npc-1"),
        ("FACTION:faction-1", VisibilityKind.FACTION, "faction-1"),
        ("GROUP:group-1", VisibilityKind.GROUP, "group-1"),
    ],
)
def test_parse_scope_accepts_documented_scopes(
    text: str,
    kind: VisibilityKind,
    target: str | None,
) -> None:
    scope = parse_scope(text)

    assert scope.kind is kind
    assert scope.target == target
    assert scope.to_string() == text


@pytest.mark.parametrize(
    "text",
    [
        "UNKNOWN:value",
        "CHARACTER:",
        "NPC: npc-1",
        "FACTION:faction-1 ",
        "public",
    ],
)
def test_parse_scope_rejects_invalid_scopes(text: str) -> None:
    with pytest.raises(VisibilityScopeError):
        parse_scope(text)


def test_visibility_scope_is_frozen() -> None:
    scope = parse_scope("PUBLIC")

    with pytest.raises(FrozenInstanceError):
        scope.target = "changed"


@pytest.mark.parametrize(
    "scope",
    [
        "PUBLIC",
        "PARTY",
        "GM",
        "CHARACTER:other",
        "NPC:other",
        "FACTION:other",
        "GROUP:other",
    ],
)
def test_gm_sees_every_scope(scope: str) -> None:
    viewer = Viewpoint(scope=parse_scope("GM"))

    assert can_see(viewer, parse_scope(scope))


@pytest.mark.parametrize(
    "viewer_scope",
    ["PUBLIC", "PARTY", "CHARACTER:a", "NPC:x", "FACTION:f", "GROUP:g"],
)
def test_every_viewpoint_sees_public(viewer_scope: str) -> None:
    viewer = Viewpoint(scope=parse_scope(viewer_scope))

    assert can_see(viewer, parse_scope("PUBLIC"))


def test_character_only_sees_its_own_character_scope() -> None:
    viewer = Viewpoint(scope=parse_scope("CHARACTER:a"))

    assert can_see(viewer, parse_scope("CHARACTER:a"))
    assert not can_see(viewer, parse_scope("CHARACTER:b"))
    assert not can_see(viewer, parse_scope("NPC:x"))


def test_npc_only_sees_its_own_npc_scope() -> None:
    viewer = Viewpoint(scope=parse_scope("NPC:x"))

    assert can_see(viewer, parse_scope("NPC:x"))
    assert not can_see(viewer, parse_scope("NPC:y"))
    assert not can_see(viewer, parse_scope("CHARACTER:x"))


def test_party_visibility_uses_explicit_membership() -> None:
    member = Viewpoint(scope=parse_scope("CHARACTER:a"), party_member=True)
    non_member = Viewpoint(scope=parse_scope("CHARACTER:b"))

    assert can_see(member, parse_scope("PARTY"))
    assert not can_see(non_member, parse_scope("PARTY"))


def test_faction_and_group_visibility_use_matching_memberships() -> None:
    viewer = Viewpoint(
        scope=parse_scope("CHARACTER:a"),
        faction_ids=frozenset({"faction-a"}),
        group_ids=frozenset({"group-a"}),
    )

    assert can_see(viewer, parse_scope("FACTION:faction-a"))
    assert not can_see(viewer, parse_scope("FACTION:faction-b"))
    assert can_see(viewer, parse_scope("GROUP:group-a"))
    assert not can_see(viewer, parse_scope("GROUP:group-b"))


def test_scoped_viewer_sees_its_own_faction_or_group_target() -> None:
    faction_viewer = Viewpoint(scope=parse_scope("FACTION:faction-a"))
    group_viewer = Viewpoint(scope=parse_scope("GROUP:group-a"))

    assert can_see(faction_viewer, parse_scope("FACTION:faction-a"))
    assert can_see(group_viewer, parse_scope("GROUP:group-a"))


def test_non_gm_does_not_see_gm_scope() -> None:
    viewer = Viewpoint(scope=parse_scope("CHARACTER:a"))

    assert not can_see(viewer, parse_scope("GM"))
