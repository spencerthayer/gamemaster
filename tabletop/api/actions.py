"""Universal action model.

``GameAction`` is the system-agnostic representation of an actor's declared
intent. The generic runtime transports ``action_type``, ``targets``, and
``parameters`` without interpreting game mechanics. Armor class, saving
throws, hit locations, spell slots, mana, refresh, and sanity are plugin
meanings, never core fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from tabletop.api._contract import freeze_mapping, freeze_tuple, require_non_empty_str, to_jsonable
from tabletop.api.entities import EntityRef
from tabletop.api.errors import InvalidActionError


@dataclass(frozen=True)
class GameAction:
    """An attempted action awaiting plugin resolution.

    ``parameters`` are owned by the game-system plugin. The generic core
    stores them and must not interpret their keys. Callers must treat the
    mapping as immutable; construction takes a defensive deep copy and
    wraps it in ``MappingProxyType``.
    """

    actor: EntityRef
    action_type: str
    targets: tuple[EntityRef, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.actor, EntityRef):
            raise InvalidActionError(
                f"actor must be an EntityRef, got {type(self.actor).__name__}"
            )
        require_non_empty_str(self.action_type, "action_type", InvalidActionError)
        object.__setattr__(
            self,
            "targets",
            freeze_tuple(self.targets, EntityRef, "targets", InvalidActionError),
        )
        object.__setattr__(
            self,
            "parameters",
            freeze_mapping(self.parameters, "parameters", InvalidActionError),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor": self.actor.to_dict(),
            "action_type": self.action_type,
            "targets": [target.to_dict() for target in self.targets],
            "parameters": to_jsonable(self.parameters),
        }


@dataclass(frozen=True)
class ActionProposal:
    """What a speaker seems to be trying to do, before anything is decided.

    This is model intent, not mechanics. It may name no action type, may
    target something that is not present, and may say plainly that it is
    unsure. None of that is an error: the deterministic planner decides what
    to do with the ambiguity, and only the planner may turn a resolved
    proposal into a :class:`GameAction`.

    Keeping this separate from ``GameAction`` is what stops a model's guess
    from becoming an authoritative mechanical action.
    """

    actor_id: str
    intent: str
    uncertainty: str | None = None
    proposed_action_type: str | None = None
    target_refs: tuple[str, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)
    needs_resolution: bool = True

    def __post_init__(self) -> None:
        require_non_empty_str(self.actor_id, "actor_id", InvalidActionError)
        require_non_empty_str(self.intent, "intent", InvalidActionError)
        if self.uncertainty is not None:
            require_non_empty_str(self.uncertainty, "uncertainty", InvalidActionError)
        if self.proposed_action_type is not None:
            require_non_empty_str(
                self.proposed_action_type, "proposed_action_type", InvalidActionError
            )
        object.__setattr__(
            self,
            "target_refs",
            freeze_tuple(self.target_refs, str, "target_refs", InvalidActionError),
        )
        for reference in self.target_refs:
            require_non_empty_str(reference, "target_refs", InvalidActionError)
        object.__setattr__(
            self,
            "parameters",
            freeze_mapping(self.parameters, "parameters", InvalidActionError),
        )
        if not isinstance(self.needs_resolution, bool):
            raise InvalidActionError("needs_resolution must be a bool")

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "intent": self.intent,
            "proposed_action_type": self.proposed_action_type,
            "target_refs": list(self.target_refs),
            "parameters": to_jsonable(self.parameters),
            "uncertainty": self.uncertainty,
            "needs_resolution": self.needs_resolution,
        }


_PROPOSAL_FIELDS = frozenset(
    {
        "actor_id",
        "intent",
        "uncertainty",
        "proposed_action_type",
        "target_refs",
        "parameters",
        "needs_resolution",
    }
)


def parse_action_proposal(payload: Mapping[str, Any]) -> ActionProposal:
    """Parse a mapping from a model into an :class:`ActionProposal`.

    Unknown keys are rejected rather than dropped. A model that invents a
    field such as ``difficulty_class`` is expressing a mechanical value
    through a side channel, and silently discarding it would hide exactly
    the mistake this contract exists to catch.

    Parsing is pure: it cannot create authoritative state.
    """

    if not isinstance(payload, Mapping):
        raise InvalidActionError("proposal payload must be a mapping")
    unexpected = sorted(set(payload) - _PROPOSAL_FIELDS)
    if unexpected:
        raise InvalidActionError(
            f"proposal has unexpected fields: {', '.join(unexpected)}"
        )
    missing = sorted({"actor_id", "intent"} - set(payload))
    if missing:
        raise InvalidActionError(
            f"proposal is missing required fields: {', '.join(missing)}"
        )

    raw_refs = payload.get("target_refs", ())
    if isinstance(raw_refs, str) or not isinstance(raw_refs, (list, tuple)):
        raise InvalidActionError("target_refs must be a list of strings")
    target_refs = tuple(raw_refs)

    raw_parameters = payload.get("parameters", {})
    if not isinstance(raw_parameters, Mapping):
        raise InvalidActionError("parameters must be a mapping")

    return ActionProposal(
        actor_id=payload["actor_id"],
        intent=payload["intent"],
        uncertainty=payload.get("uncertainty"),
        proposed_action_type=payload.get("proposed_action_type"),
        target_refs=target_refs,
        parameters=raw_parameters,
        needs_resolution=payload.get("needs_resolution", True),
    )


class ParameterSource(str, Enum):
    """Where a mechanical parameter value came from.

    The distinction that matters is authority, not convenience. A value the
    model proposed is not a rules fact, so it can never satisfy a
    rules-authoritative requirement. The set is closed so a typo cannot
    invent a trustworthy source.
    """

    MODEL_PROPOSAL = "model_proposal"
    CAMPAIGN_STATE = "campaign_state"
    ATTACHED_RULES = "attached_rules"
    RULING = "ruling"
    SYSTEM_DEFAULT = "system_default"

    @property
    def is_authoritative(self) -> bool:
        """True when a value from this source may satisfy a rules requirement."""

        return self is not ParameterSource.MODEL_PROPOSAL


@dataclass(frozen=True)
class MechanicalParameter:
    """One rules parameter and the provenance of its value.

    A bare value cannot say where it came from, which is exactly why a model's
    guess and a GM's ruling must not look alike once they reach a plugin.
    """

    name: str
    value: Any
    source: ParameterSource
    reference: str | None = None

    def __post_init__(self) -> None:
        require_non_empty_str(self.name, "name", InvalidActionError)
        object.__setattr__(self, "source", _coerce_parameter_source(self.source))
        if self.reference is not None:
            require_non_empty_str(self.reference, "reference", InvalidActionError)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": to_jsonable(self.value),
            "source": self.source.value,
            "reference": self.reference,
        }


def _coerce_parameter_source(value: Any) -> ParameterSource:
    try:
        return ParameterSource(value)
    except ValueError as exc:
        raise InvalidActionError(
            "source must be one of "
            f"{', '.join(member.value for member in ParameterSource)}, got {value!r}"
        ) from exc
