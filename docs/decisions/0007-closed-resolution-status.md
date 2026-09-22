# ADR 0007: Closed resolution status set

## Status

Accepted

## Context

A `requires_ruling` boolean can distinguish a GM judgment call from one
successful path, but it cannot explain why a mechanic produced no result.
Missing input, an unimplemented capability, and genuine rules ambiguity need
different handling. A boolean also permits contradictory combinations with
outcomes, state changes, and events.

## Decision

Use the closed `ResolutionStatus` set:

- `RESOLVED`
- `RULING_REQUIRED`
- `UNRESOLVED`
- `UNSUPPORTED`

Derive `requires_ruling` from the status instead of storing it independently.
Enforce status-specific invariants when constructing a `Resolution`.
`RULING_REQUIRED` carries the question for the GM and cannot settle state.
`UNRESOLVED` and `UNSUPPORTED` carry an explanation and no mechanical result.

## Consequences

Callers can distinguish a completed mechanic, a judgment call, missing
information, and absent plugin support. Invalid combinations fail at the data
boundary, and the orchestration layer can present each case accurately.

Every construction site had to migrate to and follow the closed set. New
terminal states require a deliberate API change and exhaustive updates to
callers, serializers, tests, and user-facing handling. The stricter model
rejects shortcuts that a boolean would have accepted.

## Alternatives

- Keep only `requires_ruling`. Rejected because false conflates resolved,
  unresolved, and unsupported outcomes.
- Use free-form status strings. Rejected because typos and unhandled states
  would fail at runtime.
- Represent failures only with exceptions. Rejected because unresolved and
  unsupported mechanics are expected game outcomes, not integration faults.
