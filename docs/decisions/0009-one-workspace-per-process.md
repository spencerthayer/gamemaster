# ADR 0009: One workspace per runtime instance

## Status

Accepted

## Context

Setting and campaign workspaces expose different tool surfaces. Campaign tools
can read or mutate information that must not appear in a setting-only
workspace. Omega's `add-skill` operation mutates a process-global registry, so
changing registered skills for one conversation changes them for every
conversation in that process.

## Decision

Select exactly one workspace at runtime startup with
`TABLETOP_WORKSPACE=setting`, `TABLETOP_WORKSPACE=campaign`, or
`TABLETOP_WORKSPACE=player` (see ADR 0012). Register that
workspace's skills once and do not switch workspaces while the process is
running.

Forbidden operations must be absent from the registered tool surface, not
merely discouraged by prompt text. Serving more than one workspace requires a
separate runtime instance for each workspace until Omega supports isolated
per-session tool surfaces.

## Consequences

Conversations sharing a process cannot change each other's tool permissions by
switching workspace. Startup configuration is explicit and fails closed when
the workspace is missing or unknown.

Deployment costs one process per workspace. Each process has its own runtime
overhead, configuration, health checks, and lifecycle. A user who needs both
surfaces cannot switch between them inside one running instance.

## Alternatives

- Re-register skills for each conversation. Rejected because the registry is
  process-global and would leak tools between concurrent conversations.
- Register every skill and rely on prompts or runtime checks. Rejected because
  forbidden operations would remain available to the model.
- Change Omega to support session-local registries now. Rejected because the
  tabletop layer should not rewrite the agent runtime for this first draft.
