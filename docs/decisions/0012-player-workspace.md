# ADR 0012: Player workspace process per participant

## Status

Accepted

## Context

ADR 0009 requires one workspace per process and names only `setting` and
`campaign`. Player participants need a smaller skill surface that omits GM
operations. Omega history and `getContext` are process-wide: `getContext`
runs before `receive`, and `LAST_SKILL_USE_RESULTS` are not isolated per
human. A shared player process would attach the previous player's private
prompt to the next player's message.

## Decision

Add `TABLETOP_WORKSPACE=player` as a third process-scoped skill surface.
Choose the workspace at startup, register skills once, and do not switch to
`campaign` or `setting` while the process is running. A viewpoint argument on
the campaign workspace is not a substitute for a separate player process.

Each player participant gets a separate Omega process because prompt context
and Omega history are process-wide. One shared player process for every
participant is rejected for this phase. Forbidden operations are absent from
the player `add-skill` list, not hidden by a prompt.

## Consequences

Deployment costs one GM process (`campaign` or `setting`) plus one player
process per player participant. ADR 0009 remains in force: one workspace per
process. Per-session skill registries are rejected for this phase.

## Alternatives

- One shared player process with per-message viewpoint selection. Rejected
  because Omega history and `getContext` are process-wide.
- Register campaign skills and filter at call time. Rejected because forbidden
  operations would remain available to the model.
- Revoke ADR 0009. Rejected; this ADR extends the token list only.
