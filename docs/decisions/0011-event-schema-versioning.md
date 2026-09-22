# ADR 0011: Persisted event schema versions

## Status

Accepted

## Context

Campaign events are immutable. Later writers need richer payloads for
`ruling.recorded`, `session.started`, and `fact.proposed`. Replay cannot
discover which contract an old row used by inspecting which keys happen to
be present, and it cannot rewrite those rows to the new shape.

## Decision

`events.event_schema_version` is part of the persistence envelope.
`PersistedEvent` exposes it. `GameEvent` does not: that type is the
plugin-facing proposal, not a stored row.

Generation 0 is the historical shape written before this column existed.
The column default is 0 so existing rows stay generation 0 without an
update. New appends write generation 1, the payload contracts defined by
the post-first-draft hardening plan.

Replay dispatches on `(event_type, event_schema_version)`. It does not
infer a generation from payload keys. Historical events are never rewritten.
A fidelity report may say that generation 0 history cannot be fully
reconstructed.

Setting events are created with `event_schema_version INTEGER NOT NULL`
and are written at generation 1. They have no generation 0 rows.

## Consequences

A payload change that is not a new generation is invisible to replay.
Adding a generation requires a handler for that pair. Operators can count
generation 0 rows and treat full reconstruction as not guaranteed while
any remain.

## Alternatives

- Infer the contract from payload keys. Rejected because a partial new
  payload can look like an old one, and extra keys on an old row would
  silently change meaning.
- Rewrite stored events in place. Rejected because the event log is
  append-only.
