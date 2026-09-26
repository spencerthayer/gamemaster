"""Run a scripted play transcript against the real runtime.

The runner drives ``TabletopRuntime.submit_action`` with a scripted proposal,
then asserts structure at each phase: the planner's disposition, the events
and state the turn produced, the visibility boundary, and semantic predicates
over the final response.

It never compares final prose exactly. Narration is the one part of a turn
allowed to vary, so pinning it would make the suite fail on wording rather
than on behavior.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tabletop.api.visibility import Viewpoint, VisibilityKind, VisibilityScope
from tabletop.api.workspace import Workspace
from tabletop.campaign.event_store import EventStore
from tabletop.campaign.models import PresenceType
from tabletop.campaign.scene_snapshot import build_scene_snapshot
from tabletop.campaign.scenes import SceneStore
from tabletop.campaign.store import CampaignStore
from tabletop.runtime import TabletopRuntime
from tabletop.storage.sqlite import connect, migrate

from tests.play_transcripts.schema import TranscriptCase, TurnExpectation

REPO_ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN = "transcript-demo"
SCENE_ID = "scene-1"


class TranscriptFailure(AssertionError):
    """A transcript assertion failed, naming the exact phase that broke."""


@dataclass
class TurnResult:
    """What one scripted turn actually produced."""

    index: int
    disposition: str
    response: str
    events: tuple[str, ...] = ()
    state_changed: tuple[str, ...] = ()
    clarification: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "disposition": self.disposition,
            "events": list(self.events),
            "state_changed": list(self.state_changed),
        }


@dataclass
class TranscriptRun:
    """Every turn of one case, plus the player-visible surface at the end."""

    case_id: str
    turns: list[TurnResult] = field(default_factory=list)
    player_visible_text: str = ""


def run_case(case: TranscriptCase, tmp_path: Path) -> TranscriptRun:
    """Build a campaign, run every scripted turn, and return what happened."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    conn = connect(tmp_path / f"{case.case_id}.db")
    migrate(conn)
    try:
        _seed(conn, case)
        run = TranscriptRun(case_id=case.case_id)
        for index, (proposal, _) in enumerate(case.turns):
            run.turns.append(_run_turn(conn, case, index, proposal.to_dict()))
        run.player_visible_text = _player_visible_text(conn)
        return run
    finally:
        conn.close()


def assert_case(case: TranscriptCase, run: TranscriptRun) -> None:
    """Check a completed run against the case's expectations.

    Every failure names the turn and the phase, so a red transcript says
    which step broke rather than only that something did.
    """
    if len(run.turns) != len(case.turns):
        raise TranscriptFailure(
            f"{case.case_id}: expected {len(case.turns)} turns, ran {len(run.turns)}"
        )
    for index, ((_, expect), actual) in enumerate(zip(case.turns, run.turns)):
        _assert_turn(case.case_id, index, expect, actual)

    sentinels = {s for _, expect in case.turns for s in expect.secret_sentinels}
    for sentinel in sorted(sentinels):
        if sentinel in run.player_visible_text:
            raise TranscriptFailure(
                f"{case.case_id}: visibility phase leaked secret {sentinel!r} "
                "to a player surface"
            )


def _assert_turn(
    case_id: str,
    index: int,
    expect: TurnExpectation,
    actual: TurnResult,
) -> None:
    if actual.disposition != expect.disposition:
        raise TranscriptFailure(
            f"{case_id} turn {index}: planner phase expected disposition "
            f"{expect.disposition!r}, got {actual.disposition!r}"
        )
    if tuple(actual.events) != tuple(expect.expect_events):
        raise TranscriptFailure(
            f"{case_id} turn {index}: authoritative phase expected events "
            f"{list(expect.expect_events)}, got {list(actual.events)}"
        )
    for marker in expect.response_contains_any:
        if marker not in actual.response:
            raise TranscriptFailure(
                f"{case_id} turn {index}: response phase expected {marker!r}, "
                f"got {actual.response!r}"
            )
    for marker in expect.response_excludes:
        if marker in actual.response:
            raise TranscriptFailure(
                f"{case_id} turn {index}: visibility phase expected {marker!r} "
                "to be absent from the response"
            )


def _seed(conn: sqlite3.Connection, case: TranscriptCase) -> None:
    store = CampaignStore(conn)
    store.create_campaign(CAMPAIGN, case.description, "freeform")
    for entity_id, name in case.entities:
        store.upsert_entity(
            CAMPAIGN,
            entity_id,
            name,
            system_state={"resources": {"lanterns": 5}},
        )
    scenes = SceneStore(conn)
    scenes.open_scene(CAMPAIGN, SCENE_ID, "Crossroads")
    for entity_id in case.present:
        scenes.enter(CAMPAIGN, SCENE_ID, entity_id, PresenceType.NPC)
    if case.starting_state:
        conn.execute(
            "UPDATE scenes SET system_state = ? WHERE scene_id = ?",
            (json.dumps(dict(case.starting_state)), SCENE_ID),
        )


def _run_turn(
    conn: sqlite3.Connection,
    case: TranscriptCase,
    index: int,
    proposal: dict[str, Any],
) -> TurnResult:
    runtime = TabletopRuntime(
        REPO_ROOT,
        campaign_roots=[REPO_ROOT / "examples" / "campaigns"],
        plugin_roots=[REPO_ROOT / "systems"],
        connection=conn,
        workspace=Workspace.CAMPAIGN,
        active_campaign=CAMPAIGN,
    )
    before_events = len(EventStore(conn).read(CAMPAIGN))
    before_state = _state_fingerprint(conn)

    result = runtime.submit_action(json.dumps(proposal))
    if not result.get("ok"):
        raise TranscriptFailure(
            f"{case.case_id} turn {index}: submit_action failed: {result.get('error')}"
        )
    data = result["data"]
    events = [event.event_type for event in EventStore(conn).read(CAMPAIGN)[before_events:]]
    after_state = _state_fingerprint(conn)
    changed = tuple(
        key
        for key in sorted(set(before_state) | set(after_state))
        if before_state.get(key) != after_state.get(key)
    )
    return TurnResult(
        index=index,
        disposition=str(data.get("disposition", "")),
        response=_response_text(data),
        events=tuple(events),
        state_changed=changed,
        clarification=data.get("clarification"),
    )


def _response_text(data: dict[str, Any]) -> str:
    """Collect every human-readable string the runtime returned."""
    parts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    for key in ("clarification", "narration", "explanation", "reason"):
        walk(data.get(key))
    return " ".join(parts)


def _state_fingerprint(conn: sqlite3.Connection) -> dict[str, str]:
    fingerprint: dict[str, str] = {}
    for row in conn.execute(
        "SELECT entity_id, system_state FROM entities "
        "WHERE owner_scope = 'campaign' AND campaign_id = ?",
        (CAMPAIGN,),
    ).fetchall():
        fingerprint[f"entity:{row['entity_id']}"] = row["system_state"]
    row = conn.execute(
        "SELECT system_state FROM scenes WHERE scene_id = ?", (SCENE_ID,)
    ).fetchone()
    if row is not None:
        fingerprint[f"scene:{SCENE_ID}"] = row["system_state"]
    return fingerprint


def _player_visible_text(conn: sqlite3.Connection) -> str:
    """Everything a player viewpoint may legitimately see.

    Only the player snapshot. Including the GM view here would make every
    sentinel fail, which would prove nothing about visibility.
    """

    player = Viewpoint(
        scope=VisibilityScope(kind=VisibilityKind.CHARACTER, target="pc-ada"),
        party_member=True,
    )
    return str(build_scene_snapshot(conn, CAMPAIGN, viewpoint=player))
