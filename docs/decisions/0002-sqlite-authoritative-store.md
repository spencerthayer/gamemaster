# ADR 0002: SQLite as the authoritative store

## Status

Accepted

## Context

Campaign state must remain correct even when model context, semantic search,
or external services are unavailable. The first draft is a local and
single-writer-oriented system. Requiring a database server, vector service,
or message broker would add operating work before the project needs their
scale.

## Decision

Use SQLite as the authoritative store for campaign state, event history,
provenance, ingestion progress, and retrieval indexes. Run writes in
transactions and use the append-only event log where history is required.

Human-readable projections and retrieval indexes are derived views. They do
not become a second authority. Semantic or lexical retrieval can help locate
information, but a retrieval result never establishes campaign truth.

## Consequences

One database file provides transactional local persistence without external
infrastructure. Tests and deployments can use the same storage engine, and
campaign truth remains available when optional retrieval services fail.

SQLite constrains write concurrency and horizontal scaling. Database backup,
migration, locking, and corruption recovery remain application and operator
concerns. Larger deployments may eventually need a different storage
implementation while preserving the same authority and transaction
boundaries.

## Alternatives

- Use PostgreSQL or another database server from the start. Rejected because
  the first draft does not justify the added service and operating burden.
- Use files as the source of truth. Rejected because cross-record invariants
  and atomic updates would be harder to enforce.
- Use a vector database or model memory as authority. Rejected because recall
  is probabilistic and cannot safely define current campaign state.
