# ADR 0013: Trusted sender principal for participant-bound processes

## Status

Accepted

## Context

Channels authenticate a platform user id and then drop it, enqueueing only
display text. Omega history and `getContext` are process-wide. A shared
player process cannot safely choose a private prompt per inbound sender.

## Decision

One Omega process serves one fixed participant (`TABLETOP_PARTICIPANT`).
Authorization for a channel-originated human turn is:

1. Platform authentication.
2. Generic channel expected-sender gate (`OMEGA_EXPECTED_SENDER`): when set,
   accept only when `authenticated_sender == expected_sender`, before enqueue
   where the platform allows, otherwise before returning from `receive`. When
   unset, keep existing generic channel behavior. The channel never queries
   tabletop.
3. Tabletop active-binding check against SQLite immediately before an accepted
   payload becomes a `receive` result.
4. Skill-time ContextVar re-check as defense in depth.

The operator or start command resolves the expected sender from
`participant_principals` and places it in the process environment before the
channel starts. Every participant-bound tabletop process (GM or player) that
is reachable from a channel carries `TABLETOP_PARTICIPANT` and a matching
expected sender. A missing sender fails closed on a configured channel.
`current_sender()` being `None` never grants operator trust. Channel-origin
authorization is bypassed only through the explicit local invocation path
(operator CLI/shell and tests).

A shared player process for this phase is rejected. WebSocket treats one
authenticated connection as one principal. Multi-user Omega sessions are a
rejected alternative. If the ContextVar cannot survive
`receive -> src/loop.metta -> eval -> py-call`, reopen this ADR; do not edit
`src/loop.metta` as a workaround inside the same task.

## Consequences

Deployment costs one process per participant. Rebinding requires regenerating
the expected-sender environment and restarting the process for the new
principal to be accepted.

## Alternatives

- Parse display names from `HUMAN-MSG`. Rejected as forgeable.
- Shared player process with per-message viewpoint. Rejected (ADR 0012).
- New account system. Rejected for this phase.
