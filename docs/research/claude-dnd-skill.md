# claude-dnd-skill — Research Report

- Repository: https://github.com/neuralinitiative/claude-dnd-skill
- Cloned at: `research/claude-dnd-skill`
- License: AGPL-3.0 (Neural Initiative LLC)
- Reuse verdict: no code reused; clean-room

## Purpose

A Dungeon Master assistant for running persistent D&D 5e campaigns inside
Claude Code, packaged as a plugin skill (`skills/dnd/SKILL.md`). It handles
campaign creation/loading/import, character management, combat tracking, NPC
generation, dice rolling, XP awards, and session state persistence.
Companion pieces: a Flask SSE display companion (`skills/dnd/display/`) and
an optional physical-dice server (`dice-server/`). Campaigns declare a
ruleset (2014 vs 2024 SRD) on the `state.md` header line, resolved via
`scripts/paths.py` (`campaign_ruleset()`).

## Architecture

A Claude Code skill: a large procedural prompt (`SKILL.md` plus two reference
modules `SKILL-scripts.md` and `SKILL-commands.md`, loaded at session start)
layered over per-campaign markdown data trees and ~30 Python scripts. Every
calculation is pushed to a script ("Script-first rule"). A model-routing
table (`SKILL.md → Model Routing`) assigns Python to mechanics, Haiku to
formatting, Sonnet to play, Opus to world-gen. `scripts/paths.py` is the
canonical path resolver, separating a CODE root (skill dir) from a DATA root
(`DND_CAMPAIGN_ROOT`, default `~/.claude/dnd`), so user data survives plugin
updates. A notable meta-rule: after context compaction, the DM must re-read
the smallest covering section of the source files rather than trust
compacted memory (`SKILL.md → Active DM Mode`).

## Persistence

Markdown files are the source of truth under `<campaigns>/<name>/`:
`state.md`, `world.md`, `npcs.md`, `npcs-full.md`, `session-log.md`,
`session-log-archive.md`, `characters/<name>.md`, plus imported-campaign
`arc.md`, `source-index.md`, and `source/<chapter-id>.md`. Derived JSON state
lives alongside: `tracker.json` (conditions/concentration/effects/death
saves, `scripts/tracker.py`), recap snapshots under `.recap/`
(`scripts/session_recap.py`), and `graph.json` (relationship graph,
`scripts/campaign_graph.py`). Cross-session continuity is Claude messages
plus these state files, with an autosave hook (`scripts/autosave_checkpoint.py`)
flushing Live State Flags, graph edges, and the session tail at scene
boundaries so compaction never costs more than a turn or two.
[DISCREPANCY: prior-art.md lists "session flags" among the derived JSON
state; in the code they are a markdown section of `state.md` (`tutor_mode`,
`roll_mode`, `autosave`, …), not JSON — only tracker, recap snapshots, and
graph are JSON.]

## RAG & Retrieval

Not present in this codebase. There is no vector store or retrieval index;
context management is deterministic file targeting instead. The closest
mechanisms are: the lazy-corpus pointer pattern (`corpus_check.py` validates
`arc.md` / `source-index.md` / `source/<id>.md` consistency so whole chapters
are read on demand, never at load), keyword search across campaign files with
section-heading context (`scripts/campaign_search.py`), and the graph
scene-context query that extracts a subgraph instead of loading full NPC files.

## Document Ingestion

`scripts/import_campaign.py` extracts plaintext from PDF, markdown, txt, and
docx for `/dm:dnd import`. PDFs use PyMuPDF column-aware extraction
(`order_blocks()` classifies blocks as left/right/full-width, treats
full-width blocks as band dividers, and sorts each band left-then-right) with
a poppler `pdftotext` fallback — this de-columning is what keeps section
headers and keyed encounters in reading order for two-column published
modules. Large sources are chunked at 4000 words (`--chunk N`) for the model
to map onto campaign templates chapter by chapter. `tests/test_import_columns.py`
covers the column reordering.

## Deterministic Mechanics

Explicit state-tracking scripts, with a documented "Script-first rule"
forbidding LLM math. Highlights:

- `scripts/dice.py`: notation parser (NdS, kh/kl, adv/dis), optional routing
  to the physical dice server, `--silent` for hidden rolls.
- `scripts/combat.py`: JSON-in/JSON-out contract — `init` takes a JSON array
  of combatants and prints turn order; `tracker` renders a state blob;
  `attack` resolves one attack. State is piped between turns by the model,
  giving a clean model-tool serialization boundary.
- `scripts/xp.py`: codified XP tables, CR normalization, encounter
  multiplier, difficulty classification, level-up thresholds; writes
  character files and pushes display blocks.
- `scripts/session_recap.py`: deterministic state-diff recap — parses
  character-sheet markdown (tolerant regex) plus `tracker.json` into a
  snapshot dict (HP, level, hit dice, death saves, conditions,
  concentration, exhaustion, inspiration, slots), then diffs two snapshots
  into a plain-English summary. Zero LLM; its docstring states recaps are
  "the #1 thing an LLM hallucinates". Snapshots persist under
  `<campaign>/.recap/`.
- `scripts/oracle.py`: Mythic-style chaos factor, yes/no oracle, event focus,
  scene meaning — seeded `random`, chaos factor persisted in `state.md`
  Session Flags.
- `scripts/tracker.py`: conditions, concentration, timed effects
  (rounds/minutes/hours/indef), death saves.

The documented continuity failure after context compaction is solved
structurally — typed relationship graph, Live State Flags, and read-the-source
rules — not with better prompts alone.

## Campaign State

Precise markdown template schemas (`skills/dnd/templates/`):

- `state.md` — header (created/last-session/session-count/ruleset), Current
  Situation, Pinned Facts (soft canon kept hot), World State (date, season,
  weather, threat arc stage, faction states), Active Quests, Open Threads &
  Rumours, Faction Moves, Recent Events, Active Combat, Live State Flags
  (structured cover/faction-stance/NPC-disposition key-values designed to
  survive compaction), Campaign Arc (sandbox/structured/dynamic YAML variants
  with beats, `what_changes`, `world_pressure`, `outstanding_beats`,
  `steering_notes`, `revision_log`), Arc History, Session Flags, DM Notes.
- `world.md` — adventure nodes, places, faction escalation arcs, clue
  trails; `session-log.md`, `arc.md`, `character-sheet.md` as below.

Per-campaign tuning dials live in Session Flags: `difficulty`, `spotlight`,
`pacing`, `roll_mode`, `autosave`.

## NPC Model

Dual-level storage. `npcs.md` holds a one-line-per-NPC index table (name,
role, faction, location, attitude, notes); `npcs-full.md` holds the full
entries with role, CR/level, HP/AC/attack, demeanor, motivation, secret,
speech quirk, attitude toward party on a hostile→allied scale, faction,
current goal, schedule, four personality axes (Trustworthy↔Deceptive,
Ambitious↔Content, Loyal↔Opportunistic, Brave↔Cowardly), and at least two
relationships. The load-bearing behavior rule (`SKILL.md` and
`SKILL-commands.md`): **before writing substantive dialogue or decisions for
any named NPC, read their full entry in `npcs-full.md`** — the index carries
surface traits only, and personality/relationships/hidden goals will drift
without the full entry. `scripts/name_registry.py` enforces name uniqueness;
`scripts/npc_rename.py` renames a character across every campaign file,
graph nodes and edges included, with a backup first.

## Visibility & Private Information

Content-level conventions, not role-based access control. `templates/state.md`
ends with a `## DM Notes (hidden from players)` section; the NPC template has
a `Secret` field; `templates/world.md` gives places, factions, and quest
nodes each a `Secret` field alongside `Discoverable`. Hidden rolls
(Perception, Insight, Stealth) go through `dice.py --silent` — silent in the
terminal, visible only on the DM-controlled display; NPC/monster dice-server
rolls omit `--player` so they auto-roll server-side and never surface on
player phones (`dice-server/README.md`). No read-enforcement mechanism exists
beyond these conventions.

## Session History

Appended, non-destructive per-session sections in `session-log.md`
(`templates/session-log.md`): Recap, Key Events, Decisions Made, XP Awarded,
Loot & Changes, Open Threads Heading into Next Session, DM Calibration (what
worked / adjust next time). The log is archived to `session-log-archive.md`
after a threshold; load-time rules forbid reading `session-log.md` at load
since Recent Events live in `state.md`. Supplementary mechanisms: the
deterministic state-diff recap (above) and graph sweeps at `/dm:dnd save`
that extract relationship shifts from the session logs.

## Relationship Graph

Yes — a typed-edge graph stored as `<campaign>/graph.json`
(`scripts/campaign_graph.py`). Node types (open vocabulary): npc, faction,
place, item, thread; edge types: loyal_to, opposes, allied_with, member_of,
lives_in, controls, knows_about, owes, related_by_blood, advances_thread,
blocks_thread, and more. Edges carry
`since_session`/`until_session` timestamps; an edge is active at session N
iff `since_session <= N AND (until_session is null OR until_session > N)`,
and `close-edge` sets `until_session` when a relationship ends. The graph is
explicitly an index over canonical markdown (`npcs-full.md`,
`session-log.md` remain authoritative) used to pull scene-relevant subgraphs
(`scene-context --place --present --threads --hops --at-session`) at lower
token cost. Extraction is phased: `campaign_graph.py extract` is a Haiku pass
proposing edges with verbatim source anchors for DM review via
`extract-apply`; `scripts/graph_extract_deterministic.py` is the LLM-free
variant — a YAML verb table (`data/graph/verb_table_seed.yaml`) pattern-matches
session-log sentences (SVO and SVO-with-prep only), claiming ~95% precision
at ~50% recall for zero cost, emitting proposals in the same format so
`extract-apply` consumes either. Corrupt graph JSON is backed aside before
any write-back can flatten it.

## Rule-Source Citation

A bundled SRD dataset (`skills/dnd/data/dnd5e_srd.json`, 1.2 MB,
`_meta.record_counts`: 319 spells, 334 monsters, 362 magic items, etc. plus
`dnd5e_supplemental.json`) queried by `scripts/lookup.py` with fuzzy
(difflib) matching across categories, `--campaign` resolving the ruleset from
the campaign's `state.md` or `--ruleset` overriding directly. The dataset is
rebuilt from upstream repos by `scripts/build_srd.py` (5e-bits/5e-database
and foundryvtt/dnd5e) with staleness checks against recorded SHAs
(`scripts/sync_srd.py`); provenance and license attribution (CC-BY-4.0 for
SRD 5.1/5.2 content) are embedded in `_meta` and generated attribution lines
in `build_srd.py`. Citation is dataset-level, not per-claim: lookups return
records, and nothing pins an in-fiction ruling back to a source paragraph.
[DISCREPANCY: prior-art.md says sourcing is "CC-BY-4.0/Fair Use"; the code
contains only CC-BY-4.0 attribution — no "fair use" language was found.]

## Useful Ideas

- Dual-level NPC storage (index row + full entry) with the read-before-act
  rule — cheap always-loaded context plus a mandatory deep-read before
  portrayal.
- Deterministic state-diff session recap as an LLM impartiality guard:
  recaps are pure data, never computed in context.
- Typed, time-stamped relationship edges (`since_session`/`until_session`)
  as an index over canon — historical relational state stays recoverable.
- Lazy corpus pointers: one file per chapter, read on demand, validated by a
  linter (`corpus_check.py`); the whole book is never loaded.
- JSON-in/JSON-out combat serialization as a clean model-tool contract; the
  model pipes state blobs between turns.
- DM Notes and Secret fields as first-class schema elements on every world
  template.
- Phased graph extraction: deterministic verb-table matcher first,
  LLM fallback only for unmatched patterns; identical proposal format for
  both.
- Live State Flags: a compaction-resistant key-value section designed to be
  the first stop for any recap claim.

## Ideas Rejected

- The LAN-only, no-auth physical dice server (`dice-server/server.py`):
  fine for a trusted home table, unacceptable as a network deployment
  pattern.
- LLM-based graph extraction as the primary phase: the deterministic matcher's
  95% precision at zero cost makes the LLM pass the fallback, not the
  default.
- Committed YAML frontmatter arcs where a plain tree suffices: this repo's
  dynamic-arc YAML blocks are large inline sections of `state.md`; our design
  keeps structure in the tree and stores only what needs to travel.

## License & Reuse

AGPL-3.0 (`LICENSE`, Neural Initiative LLC, 2026). The network clause
(Sec. 13) would attach to any derived network service, so no code is reused;
the approach is clean-room.

## Key Files

| Path | Role |
|---|---|
| `skills/dnd/SKILL.md` | Core DM prompt: standards, directory layout, model routing, display sync, dice/roll-mode conventions |
| `skills/dnd/SKILL-commands.md` | All `/dm:dnd` command procedures (load, new, import, npc, graph, save/end) |
| `skills/dnd/templates/state.md` | Campaign state schema incl. Live State Flags, arc YAML variants, Session Flags, DM Notes |
| `skills/dnd/templates/npcs.md` | NPC index table + full-entry schema (secret, personality axes, ≥2 relationships) |
| `skills/dnd/scripts/paths.py` | Canonical DATA-root vs CODE-root resolution, ruleset lookup |
| `skills/dnd/scripts/session_recap.py` | Deterministic snapshot/diff recap (zero LLM) |
| `skills/dnd/scripts/import_campaign.py` | Column-aware PDF/markdown/txt/docx extraction, 4000-word chunks |
| `skills/dnd/scripts/campaign_graph.py` | Typed-edge graph store, scene-context queries, Haiku extraction |
| `skills/dnd/scripts/graph_extract_deterministic.py` | Verb-table deterministic edge extractor |
| `skills/dnd/scripts/combat.py` | JSON-in/out combat init/tracker/attack resolution |
| `skills/dnd/scripts/lookup.py` | Fuzzy SRD lookup with per-campaign ruleset resolution |
| `skills/dnd/data/dnd5e_srd.json` | Bundled SRD dataset with `_meta` provenance |
| `skills/dnd/data/graph/verb_table_seed.yaml` | Verb patterns for deterministic graph extraction |
