"""Tests for human-readable campaign directory projections."""

from __future__ import annotations

from pathlib import Path

import pytest

from tabletop.campaign import projections as projections_module
from tabletop.campaign.event_store import EventType, PersistedEvent
from tabletop.campaign.models import CanonState, KnowledgeState
from tabletop.campaign.projections import (
    CampaignProjection,
    ProjectedFact,
    project_campaign,
    write_projection,
)


def _projection() -> CampaignProjection:
    return CampaignProjection(
        campaign_id="campaign-1",
        sequence=42,
        campaign_system={"rules": {"initiative": "side-based"}},
        entities={"hero": {"hp": 9}},
        scenes={"crossroads": {"round": 2}},
        facts={
            "known-fact": ProjectedFact(
                fact_id="known-fact",
                canon_state=CanonState.CONFIRMED,
                knowledge_state=KnowledgeState.KNOWN,
                visibility="PLAYER",
                predicate="guards",
                value="the northern pass",
            ),
            "secret-fact": ProjectedFact(
                fact_id="secret-fact",
                canon_state=CanonState.CONFIRMED,
                knowledge_state=KnowledgeState.UNREVEALED,
                visibility="PLAYER",
                predicate="betrayed",
                value="the hidden prince",
            ),
            "gm-fact": ProjectedFact(
                fact_id="gm-fact",
                canon_state=CanonState.CONFIRMED,
                knowledge_state=KnowledgeState.KNOWN,
                visibility="GM",
                predicate="serves",
                value="the glass lich",
            ),
        },
        open_threads=({"title": "The duke's secret bargain"},),
    )


def _files(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def test_replay_records_campaign_identity_and_source_sequence() -> None:
    event = PersistedEvent(
        campaign_id="campaign-1",
        sequence=42,
        event_type=EventType.SCENE_OPENED.value,
        session_id=None,
        scene_id="crossroads",
        actor_id=None,
        target_id=None,
        payload={
            "scene_id": "crossroads",
            "name": "Crossroads",
            "started_at": "2026-09-22T00:00:00Z",
        },
        occurred_at="2026-09-22T00:00:00Z",
    )

    projection = project_campaign((event,))

    assert projection.campaign_id == "campaign-1"
    assert projection.sequence == 42


def test_write_projection_creates_documented_deterministic_layout(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "campaign"

    write_projection(_projection(), directory)
    first = _files(directory)
    write_projection(_projection(), directory)

    assert _files(directory) == first
    assert set(first) == {
        "campaign.yaml",
        "gm/facts.yaml",
        "gm/threads.yaml",
        "rulings/rulings.yaml",
        "sessions/sessions.yaml",
        "state/campaign.yaml",
        "state/entities.yaml",
        "state/scenes.yaml",
        "world/facts.yaml",
    }
    for content in first.values():
        text = content.decode()
        assert text.startswith(
            "# GENERATED ARTIFACT: campaign campaign-1, source sequence 42."
        )

    non_gm_text = "\n".join(
        content.decode()
        for path, content in first.items()
        if not path.startswith("gm/")
    )
    assert "the northern pass" in non_gm_text
    assert "the hidden prince" not in non_gm_text
    assert "the glass lich" not in non_gm_text
    assert "The duke's secret bargain" not in non_gm_text
    assert "the hidden prince" in first["gm/facts.yaml"].decode()
    assert "the glass lich" in first["gm/facts.yaml"].decode()


def test_write_projection_replaces_files_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "campaign"
    directory.mkdir()
    destination = directory / "campaign.yaml"
    destination.write_text("previous complete projection\n")

    def interrupted_replace(source: Path, target: Path) -> None:
        raise OSError("interrupted")

    monkeypatch.setattr(projections_module.os, "replace", interrupted_replace)

    with pytest.raises(OSError, match="interrupted"):
        write_projection(_projection(), directory)

    assert destination.read_text() == "previous complete projection\n"
    assert list(directory.glob(".campaign.yaml.*.tmp")) == []


def test_write_projection_rejects_symlink_escape(tmp_path: Path) -> None:
    directory = tmp_path / "campaign"
    outside = tmp_path / "outside"
    directory.mkdir()
    outside.mkdir()
    (directory / "world").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="outside projection directory"):
        write_projection(_projection(), directory)

    assert list(outside.iterdir()) == []
