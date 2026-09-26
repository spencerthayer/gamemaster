"""Player workspace skill registration."""

from __future__ import annotations

from pathlib import Path

from tabletop.api.workspace import Workspace, parse_workspace

_REPO = Path(__file__).resolve().parents[2]
_METTA = _REPO / "plugins" / "tabletop" / "tabletop.metta"

_ALLOWED = frozenset(
    {
        "query-campaign",
        "query-rules",
        "query-setting",
        "query-world-history",
        "get-chunk",
        "get-world-entity",
        "get-entity",
        "get-fact",
        "get-relationships",
        "get-ruling",
        "submit-action",
        "roll",
        "current-campaign",
        "read-session",
    }
)

_FORBIDDEN = frozenset(
    {
        "read-campaign-secret",
        "mutate-quest",
        "record-ruling",
        "promote-ruling",
        "promote-fact",
        "promote-world-fact",
        "reveal-fact",
        "reveal-world-fact",
        "end-session",
        "start-session",
        "edit-setting",
        "upsert-world-entity",
        "record-world-history",
        "current-scene",
        "get-party-state",
        "get-open-threads",
    }
)


def test_parse_workspace_player() -> None:
    assert parse_workspace("player") is Workspace.PLAYER


def test_player_skills_match_allowlist() -> None:
    names = {skill.name for skill in Workspace.PLAYER.skills}
    assert names == _ALLOWED
    assert names.isdisjoint(_FORBIDDEN)


def test_metta_player_rule_matches_allowlist() -> None:
    text = _METTA.read_text(encoding="utf-8")
    assert '(= (register-workspace-skills "player")' in text
    assert "(= (register-workspace-skills player)" in text
    player_block = text.split("(= (register-workspace-skills player)")[1].split(
        "(= (query-setting"
    )[0]
    names = {
        line.split("(add-skill ", 1)[1].split()[0]
        for line in player_block.splitlines()
        if "(add-skill " in line
    }
    assert names == _ALLOWED
    assert names.isdisjoint(_FORBIDDEN)
