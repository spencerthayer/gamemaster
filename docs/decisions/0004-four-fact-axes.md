# ADR 0004: Four independent fact axes

## Status

Accepted

## Context

A tabletop fact can be true without being known to players, known without
being visible to every audience, or true only during part of the world's
history. Treating these properties as one status causes unsafe side effects,
such as confirming a fact and accidentally revealing it.

## Decision

Represent four concerns independently:

- canon state records whether the GM has accepted the fact;
- knowledge state records whether the fact has become known in play;
- visibility records which audience may receive it;
- temporal validity records when the fact applies in world time.

Promotion changes canon only. Reveal changes knowledge only. Neither operation
changes visibility or temporal validity. A Python invariant layer rejects
invalid combinations, including a proposed fact marked as known.

## Consequences

The runtime can preserve secrets, audience rules, and historical truth without
overloading canon state. Queries can reason about each concern directly, and
state transitions have limited, explicit effects.

The model costs extra database columns, query predicates, indexes, and an
invariant layer at every write boundary. Callers must choose each axis
correctly, and tests must cover combinations that a single status field would
not permit.

## Alternatives

- Use one lifecycle status for proposed, secret, revealed, and historical
  facts. Rejected because the concerns are independent and combinations grow
  ambiguous.
- Derive player knowledge from visibility. Rejected because permission to see
  information is different from whether it has been learned in play.
- Encode temporal state by overwriting current facts. Rejected because it
  destroys history.
