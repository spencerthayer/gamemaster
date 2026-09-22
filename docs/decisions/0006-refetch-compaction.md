# ADR 0006: Refetch-based context compaction

## Status

Accepted

## Context

Agent context has a finite token budget. Full tool results eventually need to
leave the active context, but lossy summaries can omit names, rules, state, or
provenance needed later. Treating a generated summary as durable memory would
make future behavior depend on information that cannot be checked against its
source.

## Decision

Compact context entries into refetchable stubs instead of summaries. Each
compactable entry carries a tool name and serialized arguments that can reload
the original authoritative or source-backed record. Compaction preserves that
recipe while removing the large result body.

Callers must refetch the record before relying on details no longer present in
the compacted stub.

## Consequences

Compaction reduces active token use without turning a lossy summary into a new
source of truth. Data can be reloaded through the same visibility and
authority boundaries that governed the original request.

Every compactable result must store refetch arguments, which adds metadata and
storage cost. Tool names and argument contracts must remain stable or provide
a migration path. Refetching also adds latency and can fail if the underlying
record has been removed or access has changed.

## Alternatives

- Summarize old results. Rejected because omitted or altered details cannot be
  reconstructed reliably.
- Keep every full result in context. Rejected because context growth is
  unbounded.
- Persist an unrestricted transcript as memory. Rejected because transcript
  text does not preserve typed authority, visibility, or current-state
  semantics.
