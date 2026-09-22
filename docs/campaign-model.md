# Authoritative campaign model

SQLite is the authoritative store for current campaign state. It holds the
structural records, fact state, provenance references, resumable ingestion
progress, and plugin-owned JSON state described below. Vector recall and
narrative text are not authoritative campaign state.

This document describes the schema applied by `tabletop/storage/migrations/`,
from `0001_core.sql` through `0014_setting_events.sql`. SQLite is the
authoritative current state. Campaign history is the append-only `events`
table. Setting history is the append-only `setting_events` table. Replay
rebuilds projections from those logs. Retrieval and vector rows are lookup,
not truth.

## Core tables

### `settings`

| Column | Purpose |
|---|---|
| `setting_id` | Stable primary key for a shared world. |
| `name` | Human-readable setting name. |
| `created_at` | Setting creation time. |

### `campaigns`

| Column | Purpose |
|---|---|
| `campaign_id` | Stable primary key for one campaign. |
| `name` | Human-readable campaign name. |
| `system_id` | Selects the game-system plugin used by the campaign. |
| `setting_id` | Optional reference to the shared setting that the campaign overlays. Deleting that setting is restricted while the campaign refers to it. |
| `created_at` | Campaign creation time. |
| `system_state` | JSON object for campaign-level state owned by the selected game-system plugin. |

### `sessions`

| Column | Purpose |
|---|---|
| `session_id` | Stable primary key for one play session. |
| `campaign_id` | Owning campaign. Deleting the campaign deletes its sessions. |
| `started_at` | Session start time. |
| `ended_at` | Optional session end time. |
| `participants` | JSON list of session participants. |
| `summary` | Optional session summary. |

### `scenes`

| Column | Purpose |
|---|---|
| `scene_id` | Stable primary key for one scene. |
| `campaign_id` | Owning campaign. Deleting the campaign deletes its scenes. |
| `session_id` | Optional containing session. Deleting that session clears this reference without deleting the scene. |
| `name` | Human-readable scene name. |
| `opened_at` | Scene opening time. |
| `closed_at` | Optional scene closing time. |
| `system_state` | JSON object for scene-level state owned by the selected game-system plugin. Migration `0004_scene_state.sql` adds this column. |

### `entities`

| Column | Purpose |
|---|---|
| `entity_id` | Opaque entity identifier. It is unique per owner rather than globally unique. |
| `owner_scope` | Checked ownership discriminator, either `setting` or `campaign`. |
| `setting_id` | Setting owner or, on a campaign row, optional setting context for an overlay. |
| `campaign_id` | Campaign owner. |
| `overrides_id` | Optional identifier of the setting entity specialized by a campaign entity. |
| `entity_type` | Optional generic classification used for filtering. |
| `name` | Human-readable entity name. |
| `system_state` | JSON object whose contents belong to the game-system plugin. |
| `metadata` | JSON object for generic, core-owned metadata. |

Two partial unique indexes enforce one entity identifier per setting and one
per campaign. A setting-owned row requires a setting reference and forbids a
campaign reference. A campaign-owned row requires a campaign reference and may
also retain setting context.

## Facts

The `facts` table stores atomic statements and keeps ownership, canon,
knowledge, visibility, and temporal validity explicit.

| Column | Purpose |
|---|---|
| `fact_id` | Globally unique fact primary key. |
| `fact_scope` | Checked ownership discriminator, either `setting` or `campaign`. |
| `setting_id` | Setting owner or optional setting context for a campaign overlay. |
| `campaign_id` | Campaign owner. |
| `subject_id` | Optional entity or subject described by the fact. |
| `predicate` | Name of the asserted relation or property. |
| `value` | Stored fact value. |
| `canon_state` | `proposed` or `confirmed`. |
| `knowledge_state` | `unrevealed` or `known`. |
| `visibility` | Audience scope string. Its default is `GM`. |
| `valid_from` | Optional beginning of temporal validity. |
| `valid_until` | Optional end of temporal validity. |
| `source_document_id` | Optional identity of the source document. |
| `source_chunk_id` | Optional identity of the source chunk or extraction unit. |
| `import_job_id` | Optional identity of the ingest job that produced the fact. |
| `extraction_method` | Optional parser or extraction method recorded for the fact. |
| `source_ownership` | `attached` while the source owns purge lifecycle, or `detached` after explicit transfer. |
| `created_at` | Fact creation time. |

A setting fact requires a setting reference and forbids a campaign reference.
A campaign fact requires a campaign reference and may also retain setting
context.

## Ingestion tables

### `ingest_jobs`

| Column | Purpose |
|---|---|
| `job_id` | Stable primary key for one ingest attempt and its slices. |
| `document_hash` | Content hash identifying the exact source bytes. |
| `parser_version` | Version of the parser or extractor used for the job. |
| `slice_strategy_version` | Version of the strategy that divided the document. |
| `status` | Checked job state: `pending`, `running`, `completed`, or `failed`. |
| `total_slices` | Number of slices expected for the job. |
| `completed_slices` | Number of completed slices. |
| `failed_slices` | Number of failed slices. |
| `estimated_cost` | Optional pre-run cost estimate. |
| `actual_cost` | Optional measured cost. |
| `started_at` | Job start time. |
| `updated_at` | Time of the latest job update. |

The combination of the document hash, parser version, and slice strategy
version is unique. It is the resume identity, so changed source bytes or changed
processing code cannot accidentally resume an incompatible job.

### `ingest_slices`

| Column | Purpose |
|---|---|
| `job_id` | Owning ingest job. Deleting the job deletes its slices. |
| `slice_index` | Stable position of the slice within the job. |
| `status` | Checked slice state: `pending`, `completed`, or `failed`. |
| `error` | Optional failure detail. |
| `updated_at` | Time of the latest slice update. |

The job identity and slice index form the composite primary key. Completed
slices therefore survive a partial failure and can be skipped during resume.

## Ownership and overlays

Setting ownership exists independently of campaigns because deities, regions,
factions, cultures, and world characters can outlive any one campaign. A
setting workspace does not need a fake campaign to own shared material.

Entities and facts implement the same ownership pattern with different
discriminator names. Entities use `owner_scope`; facts use `fact_scope`. Both
pair that discriminator with `setting_id` and `campaign_id`. Setting rows have
only a setting owner. Campaign rows always have a campaign owner and may also
record the setting they overlay.

A campaign overlay is resolved when code queries facts and entities or builds a
runtime context. It does not update or delete the setting row. A campaign can
therefore diverge from shared setting canon without changing other campaigns.
For entities, `overrides_id` records the setting entity that a campaign entity
specializes.

`relationships` (`0006_relationships.sql`) stores directed edges with the
same setting-or-campaign ownership split. Each edge has `relationship_id`,
`source_id`, `relationship_type`, `target_id`, `metadata`, `visibility`,
`valid_from`, and `valid_until`. A closed `valid_until` hides the edge from
later reads. Superseding an edge closes the old row and inserts a new one.

## Canonical state tree

Plugins receive a storage-independent state snapshot:

```text
ResolutionContext.state
{
  "campaign": {
    "system": { ... }
  },
  "entities": {
    "<entity-id>": {
      "system": { ... },
      "metadata": { ... }
    }
  },
  "scene": {
    "system": { ... }
  }
}
```

The persistence adapter maps `StateChange.path` values as follows:

| Path prefix | Existing storage target |
|---|---|
| `("campaign", "system", ...)` | The selected campaign's `system_state` JSON. |
| `("entities", "<entity-id>", "system", ...)` | The existing campaign entity's `system_state` JSON. |
| `("scene", "system", ...)` | The explicitly selected scene's `system_state` JSON. |

The remaining tuple components address keys or list indexes inside that JSON.
String components are literal, so a dot in a key does not split the key.
Plugins may mutate only the `system` branches. The `metadata` branch and all
ownership, provenance, canon, knowledge, and visibility fields remain
core-owned. Plugins never receive table or column names.

## Four independent fact axes

The fact model separates four questions:

1. `canon_state`: Has the GM accepted the statement as true?
2. `knowledge_state`: Has the information become known in play?
3. `visibility`: Which audience may receive it?
4. `valid_from` and `valid_until`: During what world-time interval does it
   apply?

Separate columns prevent confirmation from revealing a secret, prevent
audience policy from changing truth, and permit historical facts without
rewriting them. Independent storage does not make every combination meaningful.
The Python invariant layer rejects one canon and knowledge pair at the write
boundary:

| Canon state | Knowledge state | Valid |
|---|---|---|
| `confirmed` | `unrevealed` | Yes |
| `confirmed` | `known` | Yes |
| `proposed` | `unrevealed` | Yes |
| `proposed` | `known` | No |

Promotion changes only `canon_state`. Reveal changes only `knowledge_state` and
is rejected while the fact is proposed. Visibility and temporal validity do
not change as side effects of either operation.

## Provenance

Imported facts retain both historical provenance and lifecycle ownership. The
intended trace is:

```text
fact
  -> extraction
  -> slice
  -> ingest job
  -> exact document hash
  -> exact parser and slice-strategy versions
```

The current schema anchors that trace as follows. A fact records
`source_document_id`, `source_chunk_id`, `import_job_id`, and
`extraction_method`. The `import_job_id` value conventionally matches an ingest
job's `job_id`, but it is unconstrained text rather than a foreign key, so the
database does not enforce that association. When the referenced job exists, it
records `document_hash`, `parser_version`, and `slice_strategy_version`. Slices
are durable rows identified within a job by `job_id` and `slice_index`.

The current migrations do not add an extraction table, a document table, or a
foreign key from a fact's import job reference to an ingest job. They also do
not add a foreign key that maps a fact's chunk reference to a slice index.
Therefore the schema preserves the fact, source, extraction method, and a
conventional exact-job reference, while the complete enforced relational
traversal from extraction to slice remains work for the document-ingestion
schema. These limitations must not be hidden by treating either reference as a
database join that does not exist.

## Promotion and detachment

Promotion and detachment change different axes:

* Promotion changes `canon_state` from `proposed` to `confirmed`. It preserves
  every provenance value and does not change source lifecycle ownership.
* Detachment changes `source_ownership` from `attached` to `detached`. It keeps
  historical provenance but transfers purge lifecycle away from the source.

| Imported fact state | Result of source purge |
|---|---|
| Confirmed and `attached` | Purged with its source. Confirmation does not detach it. |
| `detached` | Survives source purge, including when it was imported. |

This distinction allows audit and debugging after detachment without letting a
later source purge remove independently owned campaign canon.

## Migrations

| File | Adds |
|---|---|
| `0001_core.sql` | `settings`, `campaigns`, `sessions`, `scenes`, `entities` |
| `0002_facts.sql` | `facts` with canon, knowledge, visibility, validity, and provenance |
| `0003_ingest_jobs.sql` | `ingest_jobs`, `ingest_slices` |
| `0004_scene_state.sql` | `scenes.system_state` |
| `0005_events.sql` | append-only `events`, update and delete triggers |
| `0006_relationships.sql` | `relationships` |
| `0007_documents.sql` | `documents`, `document_chunks` |
| `0008_retrieval.sql` | one FTS5 table per retrieval namespace |
| `0009_rulings.sql` | `rulings` |
| `0010_session_lifecycle.sql` | session transcript, event range, summary lists |
| `0011_session_checklist_progress.sql` | `sessions.checklist_step` |
| `0012_event_schema_version.sql` | `events.event_schema_version`, default 0 |
| `0013_one_open_session.sql` | one open session per campaign |
| `0014_setting_events.sql` | append-only `setting_events` |
| `0015_turn_receipts.sql` | diagnostic `turn_receipts`, not campaign canon |

## Event history

`events` is append-only. The primary key is `(campaign_id, sequence)`.
Update and delete triggers abort. `event_schema_version` 0 is the historical
payload shape. New appends write generation 1. Replay dispatches on event
type and generation. Rows are not rewritten.

`setting_events` is the same idea for setting-owned edits, world entities,
and world-history facts. `setting_id` references `settings` with
`ON DELETE RESTRICT`. Every setting event is written at generation 1.

`rulings` store GM decisions. `record` appends `ruling.recorded`.
`promote` appends `ruling.promoted` and changes canon without changing
knowledge.

Sessions open through `start-session` (`session.started`) and close through
`end-session` (`session.ended`). At most one session per campaign has a
null `ended_at`.

Current rows remain authoritative. Projections are derived and are not a
second write path.
