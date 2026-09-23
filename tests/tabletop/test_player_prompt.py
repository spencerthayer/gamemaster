"""Player prompt snapshot is separate from the GM builder."""

from __future__ import annotations

import inspect

import pytest

from tabletop.api.visibility import Viewpoint, gm_viewpoint, parse_scope
from tabletop.api.workspace import Workspace
from tabletop.orchestration.prompt_context import (
    build_player_prompt_context_snapshot,
    build_prompt_context_snapshot,
)


def test_gm_builder_has_no_viewpoint_parameter() -> None:
    signature = inspect.signature(build_prompt_context_snapshot)
    assert "viewpoint" not in signature.parameters
    assert "scope" not in signature.parameters
    assert "character" not in signature.parameters


def test_player_builder_refuses_gm_viewpoint() -> None:
    with pytest.raises(ValueError, match="gm_viewpoint"):
        build_player_prompt_context_snapshot(
            None,
            workspace=Workspace.PLAYER,
            campaign_id="night",
            viewpoint=gm_viewpoint(),
        )


def test_player_builder_accepts_public_viewpoint() -> None:
    snapshot = build_player_prompt_context_snapshot(
        None,
        workspace=Workspace.PLAYER,
        campaign_id=None,
        viewpoint=Viewpoint(scope=parse_scope("PUBLIC")),
    )
    assert snapshot.workspace == "player"
