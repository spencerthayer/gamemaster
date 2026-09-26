"""Case format for golden play transcripts.

A case describes what a player said, what a model proposed, and what the
system must do about it. It asserts structure at each phase rather than
prose: narration is allowed to vary, but a disposition, an authoritative
effect, and a visibility boundary are not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ProposalScript:
    """One scripted model proposal for a turn."""

    actor_id: str
    intent: str
    proposed_action_type: str | None = None
    target_refs: tuple[str, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)
    uncertainty: str | None = None
    needs_resolution: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "intent": self.intent,
            "proposed_action_type": self.proposed_action_type,
            "target_refs": list(self.target_refs),
            "parameters": dict(self.parameters),
            "uncertainty": self.uncertainty,
            "needs_resolution": self.needs_resolution,
        }


@dataclass(frozen=True)
class TurnExpectation:
    """What must be true after one turn."""

    disposition: str
    #: Substrings any one of which must appear in the final response.
    response_contains_any: tuple[str, ...] = ()
    #: Substrings that must not appear, typically GM-only facts.
    response_excludes: tuple[str, ...] = ()
    #: Event types appended during this turn, in order.
    expect_events: tuple[str, ...] = ()
    #: Entity state paths that must change, as campaign/entity key and path.
    expect_state_changes: tuple[tuple[str, str], ...] = ()
    #: Secrets that must never reach a player-facing surface.
    secret_sentinels: tuple[str, ...] = ()
    #: True when this turn is evaluated from a player viewpoint.
    player_viewpoint: bool = False
    #: Authoritative scene state written just before this turn runs, modeling
    #: something the GM established mid-case such as a ruling or a difficulty.
    state_writes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TranscriptCase:
    """One scripted play scenario."""

    case_id: str
    description: str
    #: Entities created before the turns run.
    entities: tuple[tuple[str, str], ...] = ()
    #: Entities present in the opening scene.
    present: tuple[str, ...] = ()
    #: Campaign system state written before the turns run.
    starting_state: Mapping[str, Any] = field(default_factory=dict)
    #: Values a ruling source or state lookup can supply.
    state_values: Mapping[str, Any] = field(default_factory=dict)
    turns: tuple[tuple[ProposalScript, TurnExpectation], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "description": self.description,
            "entities": [list(item) for item in self.entities],
            "present": list(self.present),
            "starting_state": dict(self.starting_state),
            "state_values": dict(self.state_values),
            "turns": [
                {"proposal": proposal.to_dict(), "expect": _expect_to_dict(expect)}
                for proposal, expect in self.turns
            ],
        }


def _expect_to_dict(expect: TurnExpectation) -> dict[str, Any]:
    return {
        "disposition": expect.disposition,
        "response_contains_any": list(expect.response_contains_any),
        "response_excludes": list(expect.response_excludes),
        "expect_events": list(expect.expect_events),
        "expect_state_changes": [list(item) for item in expect.expect_state_changes],
        "secret_sentinels": list(expect.secret_sentinels),
        "player_viewpoint": expect.player_viewpoint,
    }



def parse_case(payload: Mapping[str, Any]) -> TranscriptCase:
    """Read one case from a YAML mapping, rejecting unknown fields."""

    allowed = {
        "case_id",
        "description",
        "entities",
        "present",
        "starting_state",
        "state_values",
        "turns",
    }
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise ValueError(f"case has unexpected fields: {', '.join(unexpected)}")
    missing = sorted({"case_id", "description", "turns"} - set(payload))
    if missing:
        raise ValueError(f"case is missing required fields: {', '.join(missing)}")

    turns: list[tuple[ProposalScript, TurnExpectation]] = []
    for entry in payload["turns"]:
        proposal = ProposalScript(
            actor_id=str(entry["proposal"].get("actor_id", "")),
            intent=str(entry["proposal"].get("intent", "")),
            proposed_action_type=entry["proposal"].get("proposed_action_type"),
            target_refs=tuple(entry["proposal"].get("target_refs", ()) or ()),
            parameters=dict(entry["proposal"].get("parameters", {}) or {}),
            uncertainty=entry["proposal"].get("uncertainty"),
            needs_resolution=bool(
                entry["proposal"].get("needs_resolution", True)
            ),
        )
        raw = dict(entry.get("expect", {}))
        turns.append(
            (
                proposal,
                TurnExpectation(
                    disposition=str(raw.get("disposition", "")),
                    response_contains_any=tuple(
                        raw.get("response_contains_any", ()) or ()
                    ),
                    response_excludes=tuple(raw.get("response_excludes", ()) or ()),
                    expect_events=tuple(raw.get("expect_events", ()) or ()),
                    expect_state_changes=tuple(
                        tuple(item) for item in raw.get("expect_state_changes", ()) or ()
                    ),
                    secret_sentinels=tuple(raw.get("secret_sentinels", ()) or ()),
                    player_viewpoint=bool(raw.get("player_viewpoint", False)),
                    state_writes=dict(raw.get("state_writes", {}) or {}),
                ),
            )
        )

    return TranscriptCase(
        case_id=str(payload["case_id"]),
        description=str(payload["description"]),
        entities=tuple(
            (str(item[0]), str(item[1])) for item in payload.get("entities", ()) or ()
        ),
        present=tuple(payload.get("present", ()) or ()),
        starting_state=dict(payload.get("starting_state", {}) or {}),
        state_values=dict(payload.get("state_values", {}) or {}),
        turns=tuple(turns),
    )
