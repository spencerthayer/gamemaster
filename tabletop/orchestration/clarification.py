"""Resolve missing intent or mechanics in the required order.

Two jobs live here, and neither writes a fact or a ruling as a side effect:

* ``ClarificationStore`` holds a pending question for exactly one turn and
  resolves it when the player answers.
* ``resolve_lookup`` tries attached rules, then a current unsuperseded
  ruling, then campaign state, and only then escalates to the GM.

The order is deliberate. A value that the rules or an existing ruling
already establish must be reused rather than re-decided, so a campaign
replays consistently and a GM is not asked a question that already has an
answer.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from tabletop.api.actions import MechanicalParameter, ParameterSource
from tabletop.campaign.rulings import RulingStore
from tabletop.orchestration.planner import Disposition, ResolutionPlan

# A pending clarification is held in memory by the store that owns it, for the
# life of that store. Durable turn storage exists; an unanswered question is not
# a campaign fact, so it is not persisted and it does not survive a restart.
#
# It was once a module-level dict keyed by ``id(conn)``, which is a bug: CPython
# reuses the address of a freed object, so a newly opened connection could
# inherit a question belonging to one that no longer existed. Instance state
# has no such hazard and no cross-campaign leakage.


@dataclass(frozen=True)
class PendingClarification:
    """One open question bound to one turn."""

    turn_id: str
    question: str
    candidates: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "question": self.question,
            "candidates": list(self.candidates),
        }


class ClarificationStore:
    """Hold and resolve one pending clarification per turn.

    Scoped per connection id so two campaigns in one process cannot answer
    each other's questions.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._pending: dict[str, PendingClarification] = {}

    def open(
        self, turn_id: str, *, question: str, candidates: Sequence[str] = ()
    ) -> PendingClarification:
        pending = PendingClarification(
            turn_id=turn_id, question=question, candidates=tuple(candidates)
        )
        self._pending[turn_id] = pending
        return pending

    def pending_for_turn(self, turn_id: str) -> PendingClarification | None:
        return self._pending.get(turn_id)

    def answer(self, turn_id: str, choice: str) -> str:
        """Resolve one pending question.

        An answer must be one the question offered. Accepting an arbitrary
        string would let a player resolve a question about which of three
        hostiles they mean by naming something that was never on the list.
        """
        pending = self._pending_for_turn(turn_id)
        if pending.candidates and choice not in pending.candidates:
            raise ValueError(
                f"{choice!r} is not one of the offered choices: "
                f"{', '.join(pending.candidates)}"
            )
        del self._pending[turn_id]
        return choice

    def cancel(self, turn_id: str) -> None:
        self._pending.pop(turn_id, None)

    def _pending_for_turn(self, turn_id: str) -> PendingClarification:
        pending = self._pending.get(turn_id)
        if pending is None:
            raise LookupError(f"turn {turn_id!r} has no pending clarification")
        return pending


class RuleSource(Protocol):
    """Supplies rule-authoritative values for named parameters."""

    def lookup(
        self, names: tuple[str, ...], campaign_id: str
    ) -> Mapping[str, Any]:
        """Return the values this source can establish, by parameter name."""


@dataclass(frozen=True)
class RuleLookup:
    """Adapter over attached rules and content packs."""

    source: RuleSource
    tier: str = "authoritative rules document"

    def resolve(
        self, names: Sequence[str], campaign_id: str
    ) -> dict[str, MechanicalParameter]:
        if not names:
            return {}
        found = self.source.lookup(tuple(names), campaign_id)
        return {
            name: MechanicalParameter(
                name=name,
                value=value,
                source=ParameterSource.ATTACHED_RULES,
                reference=f"{self.tier}:{name}",
            )
            for name, value in found.items()
        }


@dataclass(frozen=True)
class StateLookup:
    """Adapter over current campaign state."""

    values: Mapping[str, Any]

    def resolve(
        self, names: Sequence[str], campaign_id: str
    ) -> dict[str, MechanicalParameter]:
        return {
            name: MechanicalParameter(
                name=name,
                value=self.values[name],
                source=ParameterSource.CAMPAIGN_STATE,
                reference="campaign state",
            )
            for name in names
            if name in self.values
        }


@dataclass(frozen=True)
class LookupOrder:
    """What the lookup found, and what it could not."""

    disposition: Disposition
    parameters: Mapping[str, MechanicalParameter] = field(default_factory=dict)
    missing_parameters: tuple[str, ...] = ()
    reason: str = ""

    def to_plan(self) -> ResolutionPlan:
        return ResolutionPlan(
            disposition=self.disposition,
            reason=self.reason,
            missing_parameters=self.missing_parameters,
            parameters=dict(self.parameters),
        )


def resolve_lookup(
    *,
    missing: Sequence[str],
    rules: RuleLookup,
    rulings: RulingStore,
    state: StateLookup,
    campaign_id: str,
    query: str = "",
    model_proposed: Sequence[MechanicalParameter] = (),
) -> LookupOrder:
    """Try each source in order and stop at the first that answers.

    A model-proposed parameter is never used to fill a gap. It is accepted
    only as a starting point for a rules search, never as the value itself.
    """
    found: dict[str, MechanicalParameter] = {}

    from_rules = rules.resolve(tuple(missing), campaign_id)
    found.update(from_rules)

    outstanding = tuple(name for name in missing if name not in found)
    if outstanding:
        found.update(_from_rulings(rulings, campaign_id, query, outstanding))

    outstanding = tuple(name for name in missing if name not in found)
    if outstanding:
        found.update(state.resolve(outstanding, campaign_id))

    still_missing = tuple(name for name in missing if name not in found)
    if still_missing:
        return LookupOrder(
            disposition=Disposition.GM_RULING,
            missing_parameters=still_missing,
            reason=(
                "no attached rule, current ruling, or campaign state supplies "
                f"{', '.join(still_missing)}; the GM must decide"
            ),
        )
    return LookupOrder(
        disposition=Disposition.RESOLVE,
        parameters=found,
        reason="every missing parameter was established from an authoritative source",
    )


def _from_rulings(
    rulings: RulingStore,
    campaign_id: str,
    query: str,
    names: Sequence[str],
) -> dict[str, MechanicalParameter]:
    """Read precedent from confirmed, unsuperseded rulings.

    A ruling's decision is free text, so it is read as a number only when it
    says exactly one. Guessing a value out of a sentence is how a wrong
    number reaches a plugin.
    """
    if not query:
        return {}
    found: dict[str, MechanicalParameter] = {}
    for ruling in rulings.search(query, campaign_id):
        number = _single_number(ruling.decision)
        if number is None:
            continue
        # A ruling names the thing it decides in its question or scope, not
        # necessarily the parameter name. "Gate DC?" answers `dc` because the
        # question mentions it.
        haystack = f"{ruling.question} {ruling.scope}".casefold()
        name = next(
            (candidate for candidate in names if candidate.casefold() in haystack),
            None,
        )
        if name is None:
            continue
        found.setdefault(
            name,
            MechanicalParameter(
                name=name,
                value=number,
                source=ParameterSource.RULING,
                reference=f"ruling:{ruling.ruling_id}",
            ),
        )
    return found


def _single_number(text: str) -> int | float | None:
    stripped = text.strip()
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        return None
