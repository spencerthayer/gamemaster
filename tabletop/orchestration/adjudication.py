"""GM adjudication path.

Handles actions the active system plugin did not deterministically resolve.
Phase 9 establishes when adjudication is legally reachable and what crosses
the boundary; it does not perform adjudication. No LLM call, no prompt, no
ruling persistence lives here. Phase 23 records accepted rulings, Phase 25
and Phase 30 drive the asking.

The three non-resolved statuses all arrive here, but they are different
problems and are presented differently: the rules leaving a judgment call is
not the same as a missing target number, which is not the same as an
unimplemented mechanic.
"""

from __future__ import annotations

from dataclasses import dataclass

from tabletop.api.actions import GameAction
from tabletop.api.errors import InvalidResolutionError
from tabletop.api.resolution import Resolution, ResolutionContext, ResolutionStatus
from tabletop.api.rules import RuleReference

_HEADLINES = {
    ResolutionStatus.RULING_REQUIRED: "The rules leave this to GM judgment.",
    ResolutionStatus.UNRESOLVED: "Required information is missing.",
    ResolutionStatus.UNSUPPORTED: "This system plugin does not implement this.",
}


def requires_adjudication(resolution: Resolution) -> bool:
    """True when a resolution cannot stand on its own."""
    return resolution.status is not ResolutionStatus.RESOLVED


@dataclass(frozen=True)
class AdjudicationRequest:
    """What the runtime hands to the GM when mechanics did not decide.

    Carries the originating action and context so a ruling can be recorded
    against real ids later, plus whatever the plugin could say: the question
    for ``RULING_REQUIRED``, the explanation for the other two, and any rule
    references worth reading first.
    """

    status: ResolutionStatus
    action: GameAction
    context: ResolutionContext
    detail: str
    rule_references: tuple[RuleReference, ...] = ()

    @property
    def headline(self) -> str:
        """Short, status-specific framing. Never the same for two statuses."""
        return _HEADLINES[self.status]


@dataclass(frozen=True)
class AdjudicationResult:
    """A GM ruling on one request.

    ``resolution`` is the mechanical result the ruling implies, when the
    ruling produces one. It must be ``RESOLVED``: adjudication ends the
    question rather than deferring it again.
    """

    request: AdjudicationRequest
    decision: str
    resolution: Resolution | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.decision, str) or not self.decision.strip():
            raise InvalidResolutionError("decision must be a non-empty string")
        if (
            self.resolution is not None
            and self.resolution.status is not ResolutionStatus.RESOLVED
        ):
            raise InvalidResolutionError(
                "an adjudication result's resolution must be resolved, got "
                f"{self.resolution.status.value}"
            )


def adjudication_request(
    resolution: Resolution,
    action: GameAction,
    context: ResolutionContext,
) -> AdjudicationRequest:
    """Build the request for a non-resolved resolution.

    Raises when the resolution is ``RESOLVED``: asking the GM to adjudicate a
    settled mechanical result is exactly the bypass Phase 9 exists to stop.
    """
    if not requires_adjudication(resolution):
        raise InvalidResolutionError(
            "a resolved resolution does not require adjudication"
        )
    detail = resolution.ruling_question or resolution.explanation
    if not detail:
        raise InvalidResolutionError(
            f"{resolution.status.value} resolutions must explain themselves"
        )
    return AdjudicationRequest(
        status=resolution.status,
        action=action,
        context=context,
        detail=detail,
        rule_references=resolution.rule_references,
    )
