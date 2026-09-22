# Roadmap

Gamemaster is a working first draft of a tabletop runtime, not a complete
game-master product. This roadmap records what has landed and keeps later work
separate from the first-draft claims.

## Milestones

### Milestone 0: Research and architecture

**Landed.** The repository contains prior-art research and a documented layer
model. Omega owns the agent loop, providers, tools, memory, and channels. The
Omega-facing adapter stays thin, the pure-Python tabletop runtime owns
orchestration and campaign truth, and game-system plugins own mechanics.
Content packs remain data only.

### Milestone 1: Runtime skeleton

**Landed.** The pure-Python runtime, versioned game-system plugin API,
capability negotiation, manifest-based plugin discovery, generic dice engine,
content-pack boundary, and Omega adapter are present. The runtime does not
import Omega or MeTTa.

### Milestone 2: Persistence

**Landed.** SQLite stores campaigns, entities, facts, relationships, sessions,
rulings, retrieval records, and append-only events. Tested turn writes apply
state changes and event records transactionally. Visibility scopes and
provenance are persisted. One process supports one startup-selected workspace,
not multiple workspaces in one process.

### Milestone 3: Documents and retrieval

**Landed with a stated extraction limit.** Markdown and text-layer PDFs can be
ingested into provenance-bearing chunks. Retrieval provides isolated
namespaces, SQLite FTS5 search, optional vector search for small corpora, and
an observable fallback to lexical search. Original sources remain separately
addressable, and retrieval never becomes campaign truth. Scanned and
image-only PDFs require manual review because OCR has not landed.

### Milestone 4: Play loop

**Landed as a tested runtime flow.** The orchestration path builds context,
delegates known mechanics to the active system plugin, returns unresolved work
for adjudication instead of inventing numbers, applies approved state changes,
records history, and provides results for narration. Allocated context also
enters Omega as a second prompt extension, recomputed at `getContext`. Omega
appends the current human message after that extension. The freeform reference
system and automated campaign demonstration cover the mechanical flow. This is
not a player-facing UI.

### Milestone 5: 5e reference

**Partially landed by design.** The `dnd5e` plugin and automated demonstration
exercise selected 2014 D&D 5e checks, attacks, damage, basic conditions,
initiative, movement, and rests. This API validation reference does not provide
full 5e support. Spells, classes, feats, monster stat blocks, multiclassing,
and many other rules are not implemented.

### Milestone 6: GURPS validation

**Landed as a minimal plugin plus the earlier audit.** The documented audit
checks that the generic plugin API can represent roll-under checks, active
defenses, maneuvers, skill defaults, hit locations, damage resistance,
separate HP and FP, advantages and disadvantages, contests, and encumbrance
without importing D&D assumptions into the core. `systems/gurps` implements
roll-under, contests, active defense, damage resistance, hit location, and
fatigue. Broader GURPS coverage has not landed.

### Milestone 7: UI and channel improvements

**Not done.** No Gamemaster UI or tabletop-specific channel improvements have
landed. Current integrations use Omega's existing channel layer. UI work,
streaming presentation, reconnect behavior, and channel-specific tabletop
flows remain future work.

## Post first-draft work

### Routing inputs without a second provider layer

Add a task lane that describes the kind of work independently from a quality
tier that describes the required model capability. Keep latency preference
and cost preference as explicit routing inputs. Collapsing task lane and
quality tier into one axis causes misrouting: two tasks can need the same
quality while requiring different model strengths, tools, or context
handling, and one task lane can accept several quality levels.

The interface should preserve the lane and tier distinction so future routing
can use it. A duplicate full model-routing implementation was rejected because
Omega already abstracts model providers. Gamemaster should pass tabletop
intent to that boundary rather than replace it.

Track model availability and observed error rates so routing and fallback
decisions can use current operational evidence instead of static assumptions.

### Advisory judges

Add typed advisory judges with structured output for:

- contradiction triage
- visibility-leak candidates
- canon-promotion priority
- narration-policy violations

Judges report evidence and recommendations only. They have no authority to
mutate campaign state, promote canon, change visibility, or rewrite a
resolution. Existing authoritative write and approval paths remain in control.

### Turn receipts

Record per-turn cost, token, and tool receipts. Receipts should identify the
model calls and tool activity attributable to a turn without treating provider
telemetry as campaign truth.

### Disconnect-safe generation

Make long-running generation survive a client or channel disconnect. Work
needs durable identity, resumable status, idempotent completion, and later
result delivery so reconnecting does not duplicate or lose a generation.
