# ADR 0003: Capability-based game-system plugins

## Status

Accepted

## Context

Tabletop systems disagree about basic mechanics. Some use armor class and hit
points, others use active defenses or hit locations, and some use no dice or
combat model at all. A core API modeled on one system would force unrelated
games into its concepts and would grow a collection of optional,
system-specific methods.

## Decision

Define a system-neutral `GameSystemPlugin` contract. Each plugin advertises
the capabilities it currently implements and owns the meaning of its opaque
schemas, action parameters, outcomes, and system state.

The runtime negotiates capabilities before invoking optional behavior. It
orchestrates generic actions, resolution results, state changes, and events,
but does not interpret game mechanics. Game-system plugins depend on
`tabletop.api`, not on Omega, MeTTa, or runtime implementation modules.

## Consequences

Different game systems can implement only the mechanics they support without
adding their vocabulary to the core. The runtime can distinguish an
unsupported capability from a failed mechanic and can test plugins through a
stable boundary.

Plugins must provide adapters and opaque schemas for their own concepts.
Capability growth requires API-version discipline, discovery validation, and
clear failure behavior. Generic orchestration cannot make assumptions that
would simplify one specific system.

## Alternatives

- Design the API around Dungeons & Dragons concepts. Rejected because those
  concepts do not generalize to other systems.
- Create a separate runtime for every game system. Rejected because campaign,
  persistence, retrieval, and visibility behavior would be duplicated.
- Use unrestricted plugin duck typing. Rejected because missing features and
  contract mismatches would fail late and inconsistently.
