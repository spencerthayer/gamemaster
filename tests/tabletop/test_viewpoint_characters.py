"""Viewpoint.character_ids visibility."""

from __future__ import annotations

from tabletop.api.visibility import Viewpoint, can_see, gm_viewpoint, parse_scope


def test_character_ids_grant_character_scopes() -> None:
    viewer = Viewpoint(
        scope=parse_scope("PUBLIC"),
        character_ids=frozenset({"ada", "bo"}),
    )
    assert can_see(viewer, parse_scope("CHARACTER:ada"))
    assert can_see(viewer, parse_scope("CHARACTER:bo"))
    assert not can_see(viewer, parse_scope("CHARACTER:cy"))


def test_primary_character_scope_still_works_alone() -> None:
    viewer = Viewpoint(scope=parse_scope("CHARACTER:ada"))
    assert can_see(viewer, parse_scope("CHARACTER:ada"))
    assert not can_see(viewer, parse_scope("CHARACTER:bo"))


def test_group_scope_does_not_unlock_characters() -> None:
    viewer = Viewpoint(scope=parse_scope("GROUP:ada"))
    assert not can_see(viewer, parse_scope("CHARACTER:ada"))


def test_gm_viewpoint_still_sees_all() -> None:
    assert can_see(gm_viewpoint(), parse_scope("CHARACTER:anyone"))
