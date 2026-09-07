"""Append-only event store.

Immutable events with a per-campaign sequence; state projections derive from
events where practical. Events are never rewritten or deleted. Phase 12 of
the execution plan fills this in.
"""
