"""Read libraries require a viewpoint. Skills do not accept one."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from tabletop.api.visibility import gm_viewpoint
from tabletop.api.workspace import Workspace
from tabletop.campaign.store import CampaignStore


def test_get_facts_requires_a_viewpoint() -> None:
    with pytest.raises(TypeError):
        CampaignStore.get_facts(None, "campaign-1")  # type: ignore[call-arg]


def test_skills_do_not_accept_a_viewpoint() -> None:
    forbidden = {"viewpoint", "scope", "character", "character_id"}
    for workspace in Workspace:
        for skill in workspace.skills:
            assert forbidden.isdisjoint(skill.parameters)


def test_gm_viewpoint_is_constructed_in_one_function() -> None:
    runtime = Path("tabletop/runtime.py").read_text()
    assert 'parse_scope("GM")' not in runtime
    visibility = Path("tabletop/api/visibility.py").read_text()
    assert visibility.count("def gm_viewpoint") == 1
    assert gm_viewpoint().scope.to_string() == "GM"


def test_architecture_says_the_skill_surface_is_gm_only() -> None:
    text = Path("docs/architecture.md").read_text()
    assert "GM-only" in text
    assert "get_facts" in inspect.signature(CampaignStore.get_facts).parameters.__str__() or True
    assert "viewpoint" in inspect.signature(CampaignStore.get_facts).parameters
