# ADR 0001: Omega as the agent runtime foundation

## Status

Accepted

## Context

Gamemaster needs an agent loop, model and provider integration, tool execution,
memory, and communication channels. SingularityNET Omega already supplies
those runtime concerns. Reimplementing them inside the tabletop layer would
duplicate infrastructure and tie game rules to agent-runtime details.

## Decision

Use Omega as the agent runtime foundation. Keep tabletop behavior in a
separate Python runtime and expose it through one thin Omega-facing adapter.
Omega owns cognition, model interaction, channels, and tool execution. The
tabletop runtime owns campaign orchestration and persistence. Game-system
plugins own mechanics.

Omega internals should remain unchanged unless an integration requirement
cannot be met through its existing extension points. Any necessary upstream
changes must stay small and documented in `UPSTREAM.md`.

## Consequences

The project inherits Omega's runtime capabilities and can focus on tabletop
behavior. The core tabletop package and game-system plugins remain testable
without Omega.

The deployment now depends on Omega's plugin model, lifecycle, and supported
versions. Integration behavior must be tested at the adapter boundary, and
upstream changes can require compatibility work. The repository also carries
Omega code and configuration that would not be needed by a standalone
tabletop engine.

## Alternatives

- Build a new agent runtime. Rejected because it duplicates model, tool,
  memory, and channel infrastructure.
- Put tabletop mechanics directly into Omega. Rejected because it couples
  domain rules to one agent implementation and makes isolated testing harder.
- Integrate each game system as its own Omega plugin. Rejected because it
  repeats runtime integration and exposes Omega concepts to mechanics code.
