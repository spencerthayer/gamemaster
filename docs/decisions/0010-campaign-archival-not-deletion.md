# ADR 0010: Campaign archival instead of deletion

## Status

Accepted, not yet implemented

## Context

Campaign events are intended to be immutable history. Existing campaign-owned
tables also use cascading foreign-key deletion. Deleting a campaign would
therefore erase events and other history, while retaining those rows would
leave records whose owning campaign no longer exists. Immutable history and
cascade deletion cannot both define campaign removal.

## Decision

Do not support campaign deletion. The eventual lifecycle operation will
archive a campaign while retaining its authoritative records and immutable
events. Archived campaigns should be excluded from normal active-campaign
flows without erasing their history.

Archival is not implemented yet. Until it is implemented, campaigns simply
stay in the database. No current command or API should claim to delete or
archive them.

## Consequences

Campaign history will remain available for audit, replay, provenance, and
future inspection. The design avoids a deletion path that contradicts the
event log's immutability.

Archival still needs schema fields, queries, runtime behavior, user-facing
commands, and tests. Because it is unimplemented, unwanted or obsolete
campaigns simply stay in the database and continue consuming storage.
Deletion remains unsupported, including as a substitute for missing archival.

## Alternatives

- Cascade-delete campaigns and their events. Rejected because it destroys
  immutable history.
- Delete the campaign row but retain events. Rejected because it breaks
  ownership and referential integrity.
- Permit hard deletion only through an administrative escape hatch. Rejected
  because it creates a second lifecycle contract and weakens the immutability
  guarantee.
