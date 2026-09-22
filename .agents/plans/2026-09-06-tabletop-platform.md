# Gamemaster: Omega-based Tabletop RPG Platform - Execution Plan

Status: ACTIVE.
Integration branch: `main`.
Development occurs on per-phase feature branches and merges through PRs.
Bootstrap date: 2026-09-06. Full plan and TODO tracker in one file.

## Objective

Create a new repository named `gamemaster` in the agent's current working directory by
cloning SingularityNET Omega as the base runtime, then build a first-draft,
platform-agnostic tabletop role-playing framework on top of it.

The result is a modular game-master platform, not a D&D-specific chatbot:

- Omega as the agent/runtime layer
- a separate tabletop orchestration layer
- a tabletop-specific plugin architecture
- game-system plugins (D&D 5e, GURPS, Dying Earth, future systems)
- content packs (settings, adventures, sourcebooks, campaign material)
- deterministic rule resolution where mechanics are known
- RAG over rules and source documents
- persistent campaign state
- event-sourced history
- NPC/private knowledge and agendas
- visibility-scoped facts
- human-readable campaign projections
- original source files available for direct inspection
- a first freeform system plugin
- a partial D&D 5e reference plugin only after the generic APIs exist

First goal: an informed architecture and a runnable first draft. Not a complete
game engine.

## Boundary (non-negotiable)

| Layer | Responsibility |
|---|---|
| Omega | cognition, tools, model interaction, channels |
| Tabletop Runtime | campaign orchestration, persistence, knowledge boundaries |
| Game System Plugin | mechanics and system-specific schemas |
| Content Pack | rules, settings, adventures, lore |
| Campaign | authoritative world state and history |
| LLM | intent interpretation, narration, ambiguous adjudication |

If implementation choices begin collapsing these layers, correct the architecture
before adding features.

## High-Level Constraints

1. Project lives at `./gamemaster`.
2. Omega is the upstream foundation. Do not rewrite its agent loop, provider
   layer, communication layer, memory layer, or plugin machinery unless necessary.
3. Isolate new tabletop functionality behind a single Omega-facing plugin.
4. Do not make the tabletop API D&D-shaped.
5. Game mechanics via capability-based plugins.
6. LLM may interpret intent, narrate outcomes, and adjudicate genuinely ambiguous
   situations. It must not silently replace deterministic mechanics.
7. Original PDFs/source documents stay separately accessible from their RAG
   representations.
8. Campaign truth must not depend on vector-memory recall.
9. Research repos cloned under `research/` and ignored by Git.
10. No copying code from research repos without license compatibility checks.
    Prefer architectural study and clean-room implementation.
11. First draft runs locally and in Docker/Portainer.
12. Prefer SQLite over external infrastructure.
13. Prefer Python for the tabletop framework unless Omega requires MeTTa.
14. Canon state, knowledge state, and visibility scope are three independent
    axes. Model-created facts default to non-canon. Promotion to canon and
    reveal to players are separate operations.
15. Extraction and import are separate trust boundaries. Model output is
    untrusted proposal data. The importer performs deterministic validation
    and holds no model credentials and no network access where practical.
16. Context compaction is refetch-based, not lossy summarization. A compacted
    entry retains the tool name and arguments needed to reconstruct it.

## Omega integration notes (validated 2026-09-06)

- Omega plugin API: a MeTTa or Python module with entry point `loadOmegaPlugin`.
- Plugins are listed in `config/plugins.yaml` with `name`, `loader` (`metta`|`python`),
  `location` (may use `{REPO}` placeholder).
- MeTTa APIs available to plugins:
  - `(add-skill $function $description $arguments)`
  - `(remove-skill $function)`
  - `(add-prompt-extension $handle $text)`
  - `(remove-prompt-extension $handle)`
  - `(add-heartbeat-listener $handle $callback)`
- MeTTa to Python bridge: `(py-call (module.func arg ...))`.
- Python modules are exposed via `!(import! &self (library Omega ./path/mod.py))`
  (pattern from `lib_omega.metta`).
- Original Omega bootstrap commit: `7b060f5738ee7b8cf064c8b6282ed9fe07cf407f`
  (`UPSTREAM.md`). Current `main` may include later `upstream/main` merges
  and tabletop PRs; do not require `HEAD` to equal the bootstrap commit.

Design decision for the tabletop plugin:
- `plugins/tabletop/tabletop.metta` is the thin Omega-facing glue (a MeTTa plugin,
  `loader: metta`).
- `tabletop/` is a pure-Python package (Tabletop Runtime) with zero MeTTa/Omega
  imports. It is standalone-testable via pytest.
- Game-system plugins (freeform, dnd5e) are Python packages in `systems/` loaded
  at runtime by the Python plugin registry. They never depend on Omega/MeTTa.

## Amendments from whitepaper review (2026-09-21)

Source: `whitepaper (1).md`, an external agent-platform whitepaper, not stored
in this repo. Reviewed for transferable architecture, not code reuse. Its
central stance matches this plan: the LLM is a proposer over authoritative
stores, never the store itself.

Three items are architecture-level upgrades rather than optional inspiration,
because they address failure modes this design otherwise hits once campaigns
grow large. They are recorded as constraints 14, 15, and 16 above:

1. Proposed canon vs confirmed canon vs player knowledge (Phases 11, 12, 14, 23).
2. Extractor and importer as separate trust boundaries (Phases 19, 31).
3. Refetchable context compaction instead of lossy summarization (Phase 30).

The remaining nine items fold into existing phases. No new phases are added.

| Idea | Lands in |
|---|---|
| Provenance-linked reversible deletion | 11, 12, 19, 22 |
| Deterministic document-shape detection before LLM extraction | 19 |
| Resumable content-addressed ingestion jobs | 11, 19 |
| Workspace access by capability, not prompt prohibition | 24, 30 |
| Campaign overlays setting canon without rewriting it | 11, 20, 21 |
| Contradiction detection as an advisory service | 30 |
| Token-aware and content-aware context budgeting | 30 |
| Task lane separate from quality/cost tier | 36 (post first draft) |
| Typed advisory judges that cannot mutate canon | 36 (post first draft) |

Rejected for now: duplicating a full model-routing implementation. Omega already
abstracts providers. Only the lane/tier separation is kept, as a roadmap item, so
the interface does not foreclose it.

## Phases and TODOs

### Phase 1: Bootstrap the Repository
Status: DONE
- Clone `https://github.com/singnet/Omega.git` into `gamemaster`.
- Verified: `git status` clean, remote `origin`, HEAD `7b060f5`.
- Renamed `origin` -> `upstream`. No new origin added (no destination repo supplied).
- Created branch `tabletop-platform`.
- Created `UPSTREAM.md` (repo URL, commit SHA, branch, date, description).

TODO (commit): `chore: bootstrap gamemaster from omega` - verify git status --short
clean, commit UPSTREAM.md + .gitignore.

### Phase 2: Research Workspace
Status: COMPLETE
- Created `research/` and added `/research/` to `.gitignore`.
- Added ignores for campaign data, sourcebooks/library, vector stores, SQLite DBs,
  caches, secrets, `/tabletop/data/`.
- Cloned all six research repos into `research/`:
  - `AiChatTrpg` (Apache-2.0)
  - `dungeonmaster` (no license)
  - `claude-dnd-skill` (AGPL-3.0)
  - `open-tabletop-gm` (AGPL-3.0-or-later)
  - `mythweaver` (no license)
  - `dnd_experience` (no license)
- Deep architectural research done on all six repos via subagents (reports received,
  findings below).

Done: `docs/research/prior-art.md` written (commit `6959a07`).
Done: six per-repo research reports recorded in `docs/research/` (AiChatTrpg,
dungeonmaster, claude-dnd-skill, open-tabletop-gm, mythweaver, dnd_experience),
each verified against the cloned code (commit `42aab81`).

#### Research findings digest
- AiChatTrpg (Apache-2.0): adapter-protocol framework core with pure dataclass I/O;
  versioned deterministic dice IR + seeded replayable dice + persistent check log;
  event-sourced evidence-backed memory with idempotency and provenance; visibility-
  first design; marker-as-escape-hatch (no function calling) pattern; dual memory
  (structured + compressed narrative). Ideas to reject: in-memory lexical RAG
  placeholder, JSON-bucket state as source of truth, grep ReAct retrieval as a
  per-turn path.
- dungeonmaster (no license): "LLM proposes, code validates and applies" seam;
  pure deterministic core with injected RNG (seeded, testable); three-tier roll
  request (inline marker fallback + constrained-JSON router); spoiler discipline via
  data architecture (adventure excluded from vectors, gated scene exits); session
  artifact split (read-only sheet / code-owned mutable state / append-only journal)
  with atomic writes and rotation-on-leave; heading-aware RAG chunking preserving
  section titles; separate vec tables per corpus with embedding-model pinning; FTS5
  mirror. Ideas to reject: marker-only time (code-driven per-turn increments win),
  LLM-owned clocks/flags/attitude, adventure via plain vector RAG.
- claude-dnd-skill (AGPL-3.0): dual-level NPC storage (index + full entry) with
  read-before-act rule; deterministic state-diff session recap (LLM impartiality
  guard); typed-edge relationship graph as an index over canon; DM Notes + Secret
  fields as first-class schema; hidden rolls via --silent. Ideas to reject: LAN-only
  no-auth dice server, heavy YAML frontmatter on arcs.
- open-tabletop-gm (AGPL-3.0-or-later): two-layer system abstraction (system-agnostic
  core vs per-system module) with porting template; state.md header + Live State Flags
  as compaction-resistant anchor; typed + time-stamped relationship graph with
  since/until_session, close vs supersede; deterministic pattern extractor as local
  alternative to LLM extractor; roll_mode players/auto; name registry + safe rename;
  Pinned Facts as small stable canon set. Ideas to reject: chunk-and-embed RAG over
  campaign state, per-system UI CSS, auto-push host.
- mythweaver (no license): engine-owns-numbers + typed tool contract (NotImplemented
  for unbuilt phases); whitelist player projection with write-time player-safe
  composition (leakage-proof by construction); append-only facts ledger for evolving
  standing; degrade ladder for RAG (vector -> pgvector -> BM25 -> off) with hard
  dimension invariant; provenance-carrying retrieved chunks with human-citable
  sources; falsify-cheapest-first culture; $0 replay/restart paths. Ideas to reject:
  fully-fledged social relationship graph (falsified for well-known settings),
  fused hybrid retrieval (cascade simpler).
- dnd_experience (no license): layered service separation; generic metadata/extra
  JSON columns on nearly every entity; pure deterministic engine separated from LLM;
  compact regex dice expression + keep/drop; cursor pagination; file attachments as
  disk + DB metadata with uuid stored names; @lru_cache retrieval. Ideas to reject:
  prompt-only GM screen as long-term visibility model, combat action log without
  resolution, global single RAG corpus for all campaigns.

Reuse verdict: no code reused from any research repo. AiChatTrpg (Apache-2.0) is the
only clearly permissive repo; everything else is AGPL or unlicensed. All architecture
is clean-room.

### Phase 3: Document the Target Architecture
Status: COMPLETE
Done: `docs/architecture.md` defines the full layer stack, ownership table,
and key rules (commit `42aab81`). TODO text below retained as the original
requirement record.
TODO: create `docs/architecture.md` defining layers:
Player/GM -> Omega channel/provider/runtime -> omega-tabletop adapter -> Tabletop
Runtime -> (Game System Plugins, Content Packs, Campaign Store, Rules/RAG, Event Log,
Visibility Engine, Relationship Graph, Source Document Store).
Key rule: Omega provides cognition. Tabletop Runtime provides orchestration and
continuity. Game plugins provide mechanics. Content packs provide rules and world
material. Campaign state provides truth. Omega-facing code is thin. Game-system
plugins must not depend on Omega or MeTTa.

### Phase 4: Create the Project Structure
Status: COMPLETE
Done: package skeleton created on `phase-3-4-structure` — all modules below
exist with contract docstrings, wired as importable packages, plus a
structure smoke test (`tests/tabletop/test_skeleton.py`: 46 module imports +
subprocess check that the runtime imports no Omega/MeTTa modules) and repo
root added to pytest `pythonpath`.
Target structure:
- `plugins/tabletop/tabletop.py` (Omega adapter)
- `tabletop/api/{plugin,capabilities,actions,resolution,events,entities,rules,visibility}.py`
- `tabletop/plugins/{discovery,manifest,registry}.py`
- `tabletop/campaign/{store,models,event_store,projections,relationships,visibility}.py`
- `tabletop/documents/{ingest,models,markdown,pdf,provenance}.py`
- `tabletop/retrieval/{interface,lexical,vector,hybrid,models}.py`
- `tabletop/orchestration/{turn,context,adjudication,session}.py`
- `tabletop/storage/{sqlite.py,migrations/}`
- `tabletop/dice/{parser,roller}.py`
- `systems/freeform/`, `systems/dnd5e/`
- `examples/campaigns/`
- `tests/`
- `library/raw/` mounting layout, `library/processed/`

### Phase 5: Build One Omega Adapter Plugin
Status: COMPLETE
Done: `plugins/tabletop/tabletop.metta` MeTTa plugin + `omega_tabletop_adapter.py`
bridge (runtime init, JSON-safe responses, exception containment) +
`tabletop/runtime.py` facade with shallow non-executing discovery and
`capability_unavailable` responses. Registered in `config/plugins.yaml`,
documented in `UPSTREAM.md`. 7 Phase 5 tests. Merged as PR #3 (`2e8833c`).
Adapter module named `omega_tabletop_adapter.py` (not `tabletop.py`) to
avoid colliding with the runtime package under Omega's loader.
TODO: one plugin `tabletop`, the only Omega-specific integration point.
Responsibilities: initialize Tabletop Runtime; register tabletop skills; add prompt
instructions; discover configured campaigns; discover game-system plugins; translate
Omega skill calls into Tabletop Runtime calls; translate results into safe
serializable responses.
Expected entry point: `loadOmegaPlugin()` (MeTTa glue + Python bridge).
Add `tabletop` to `config/plugins.yaml`. Document any modification to upstream
Omega files.

### Phase 6: Define the Game-System Plugin API
Status: COMPLETE
Done: `tabletop/api/plugin.py` (`TABLETOP_PLUGIN_API_VERSION = "tabletop/v1"`,
`GameSystemInfo`, `GameSystemPlugin` ABC with `supports()`/
`require_capability()`/lifecycle/schema hooks/`validate_state()` returning
`ValidationResult`, concrete `resolve(action: GameAction, context:
ResolutionContext) -> Resolution` wired in Phase 8),
`tabletop/api/capabilities.py` (13-member string-backed `Capability` enum),
`tabletop/api/errors.py` (5-class hierarchy),
`docs/plugin-api.md` incl. architecture falsification notes. `freeform` and
`dnd5e` conform with honestly empty capabilities. 16 new tests. No
discovery, no mechanics.
TODO: `GameSystemPlugin` class (id, api_version, capabilities(), resolve(), etc.)
per prompt. Capability negotiation. Create `docs/plugin-api.md` (manifest format,
API versioning, discovery, lifecycle, capability negotiation, errors, system-specific
state, extension points).

### Phase 7: Plugin Discovery
Status: COMPLETE
Done: strict `plugin.yaml` manifest (`tabletop/plugins/manifest.py`, safe
YAML, unknown fields rejected, entrypoint `module:ClassName` validated);
`discovery.py` (immediate-children-only discovery, symlink-escape rejection,
deterministic id sorting, discover/load separation, discovery never executes
plugin code, API version checked before import, identity cross-check);
`registry.py` (duplicate ids fail closed, id-sorted listing,
shutdown_all collects failures). Runtime owns its registry instance,
replacing Phase 5 shallow system discovery; campaigns stay shallow until
Phase 11. `TABLETOP_PLUGIN_PATH` env (os.pathsep) + built-in systems root.
Built-in freeform/dnd5e and external plugins use the same pipeline via
`plugin.yaml` manifests. 30+ new tests incl. sentinel pre-import-execution
regressions. Docs: `docs/plugin-api.md` installation section.
TODO: `tabletop/plugins/{manifest,discovery,registry}.py`. Support discovery without
rebuilding Omega (`/tabletop/plugins/` mounted dir). Executable system plugins vs
non-executable content packs kept separate. No dynamic execution of untrusted
uploaded docs.

### Phase 8: Universal Action and Resolution Models
Status: COMPLETE
Merged as PR #6 (`60ae427`) on `main`. Commits: `efd46aa` (models),
`159ad02` (path/JSON/operation contract), `9f50c9c` (padded-string
rejection and str-enum JSON).

Done:
- `tabletop/api/entities.py`: frozen `EntityRef(id, entity_type=None)`.
  Opaque non-empty unpadded id. No UUID. Hashable. `to_dict()`.
- `tabletop/api/actions.py`: frozen `GameAction(actor, action_type,
  targets=(), parameters={})`. Arbitrary plugin-owned parameters. Zero,
  one, or many targets. Structural validation only.
- `tabletop/api/resolution.py`: `ResolutionContext` (campaign_id,
  system_id, optional scene_id, opaque `state`; data, not a service
  locator), `StateOperation` (`SET`/`DELETE`), `StateChange` (operation,
  `path: tuple[str | int, ...]`, optional value; DELETE forbids a
  value; no `previous_value`), `RollResult` (expression, finite total,
  opaque details), `Resolution` (plugin-owned `outcome`, optional rolls,
  state_changes, rule_references, events, ruling pair, mechanical
  `explanation`).
- `tabletop/api/rules.py`: transport `RuleReference` (Phase 22 fields;
  no retrieval).
- `tabletop/api/events.py`: proposed-event `GameEvent` (no sequence,
  campaign/session ids, timestamp, or store offset).
- `tabletop/api/_contract.py`: JSON-shape freeze (`None`/`str`/`bool`/
  `int`/finite `float`/string-keyed mappings/sequences),
  `MappingProxyType` defensive copies, `to_jsonable` (Enum before str).
- `InvalidActionError` and `InvalidResolutionError` in
  `tabletop/api/errors.py`.
- `GameSystemPlugin.resolve()` uses the concrete types. Freeform and
  dnd5e stubs keep empty capabilities and still raise `GameSystemError`.
- Docs: `docs/action-resolution.md`, `docs/plugin-api.md` resolution
  section.
- Tests: `tests/tabletop/test_action_resolution.py` plus skeleton import
  of `_contract`. Local suite at merge: 157 tabletop / 223 `tests/`
  (Python 3.11). CI: autotests pass, common pass.

Contract notes for later phases:
- Core does not require `success`/`failure`/`damage`/`margin` on
  `Resolution`. Dice are optional. Plugins describe `StateChange` values;
  they do not mutate campaign persistence through `ResolutionContext`.
- Phase 11 applies `SET`/`DELETE` along tuple paths (dots inside a
  component are literal keys). Prior state comes from the store, not
  the plugin.
- Phase 9 owns ruling policy enforcement. Phase 10 owns dice
  parse/roll. Phase 12 owns persisted event rows.

TODO: `GameAction`, `ResolutionContext`, `Resolution` dataclasses (per prompt).
Core runtime asks plugin to resolve actions; core must not know meaning of AC,
saving throw, hit location, spell slot, mana, refresh, sanity.

### Phase 9: Deterministic Mechanics Boundary
Status: PENDING
TODO: encode LLM vs plugin boundary in code and docs. When deterministic resolution
cannot decide: `requires_ruling = True` with rule refs and context. No LLM fabrication.

Clarified 2026-09-21. Phase 9 turns "LLM proposes, code validates, code resolves
where deterministic, LLM adjudicates only genuine gaps" into an enforceable
runtime contract. `requires_ruling` must not be merely advisory: where a
deterministic capability exists, the orchestration layer cannot bypass it and
manufacture a `Resolution`.

Resolution status is a closed set, not a boolean:
- `RESOLVED`: a deterministic result exists.
- `RULING_REQUIRED`: the rules leave a genuine adjudication choice.
- `UNRESOLVED`: the system knows the mechanic, but facts or rules in hand are
  insufficient.
- `UNSUPPORTED`: the active system or plugin cannot resolve this category at all.

Flow:

```
GameAction
    |
    v
active system implements ACTION_RESOLUTION?
    |
    +-- yes --> plugin.resolve()
    |               |
    |               +--> RESOLVED
    |               +--> RULING_REQUIRED
    |               +--> UNRESOLVED
    |
    +-- no ---> UNSUPPORTED
                    |
                    v
              adjudication path
```

Core rule: a missing mechanic is not permission to invent a mechanic. All four
states are explicit outcomes the orchestrator must handle. `UNSUPPORTED` and
`UNRESOLVED` route to adjudication with rule references and context; neither
licenses the LLM to produce mechanical numbers.

This taxonomy is what Phase 25 prompt policy and Phase 30 orchestration enforce.

### Phase 10: Dice Engine
Status: PENDING
TODO: `tabletop/dice/{parser,roller}.py`. Support d20, 2d6, 3d6+2, 2d20kh1, 2d20kl1,
4d6dl1, 1d100. No D&D semantics. `roll(expression, rng=None) -> RollResult`.
Seeded deterministic testing.

### Phase 11: Campaign Persistence Model
Status: PENDING
TODO: SQLite authoritative store. Tables: campaigns, sessions, scenes, entities,
facts, relationships, events, rulings, documents, document_chunks (+ clocks,
resources, visibility_grants, system_state as needed). Create `docs/campaign-model.md`.

Amended 2026-09-21:
- Setting-level canon and campaign-level overlay are separate scopes. A campaign
  fact overrides a setting fact for that campaign only. Divergence never mutates
  shared setting canon.
- Facts carry canon state (`proposed` | `confirmed`) and knowledge state
  (`unrevealed` | `known`) as independent columns, with visibility scope and
  temporal validity as further independent axes.
- Imported records carry reversible provenance: `source_document_id`,
  `source_chunk_id`, `import_job_id`, `extraction_method`. Enough to trace a row
  back to document, chunk, and ingest job, and to delete exactly the derived set.
- Ingestion job persistence: `ingest_jobs` (job_id, document_hash, parser_version,
  slice_strategy_version, status, total_slices, completed_slices, failed_slices,
  estimated_cost, actual_cost, started_at, updated_at) and `ingest_slices`.

Clarified 2026-09-21:
- Facts carry an explicit `fact_scope` (`setting` | `campaign`) with matching
  ownership identifiers `setting_id` and `campaign_id`. A campaign fact never
  overwrites a setting fact at storage level. The overlay happens at query and
  context-construction time, which is what makes the Phase 20 and 21 precedence
  rules implementable.
- Promotion and detachment are different operations on different fields.
  Promotion changes canon state. Detachment changes provenance ownership.
  Consequences:

```
imported + confirmed   still owned by the source document,
                       removed when that source is purged
imported + detached    independently owned campaign canon,
                       survives source purge
```

  Promoting an imported proposal does not strip its provenance. Only explicit
  detachment transfers ownership.

### Phase 12: Event-Sourced History
Status: PENDING
TODO: append-only immutable events with sequence, event_type, campaign_id, session_id,
scene_id, actor_id, target_id, payload. Projections derive current state from events
where practical. Inspectable.

Amended 2026-09-21:
- Canon promotion and player reveal are distinct event types. Promoting a fact
  emits no reveal, and revealing emits no promotion.
- Removal of imported records is provenance-aware: deleting a document removes
  the records derived from it and leaves hand-authored or independently promoted
  material intact.
- Contradiction findings are advisory events. They record a candidate conflict
  and never rewrite canon on their own.

Clarified 2026-09-21: promotion and detachment are distinct event types, in
addition to reveal. A provenance-aware document purge removes imported records
that are still attached, including promoted ones, and leaves detached and
hand-authored records intact.

### Phase 13: Human-Readable Campaign Projections
Status: PENDING
TODO: campaign directory layout (campaign.yaml, state/, world/, rulings/, sessions/,
gm/). These are projections/operator artifacts. SQLite + event history remain
authoritative.

### Phase 14: Visibility and Knowledge Scoping
Status: PENDING
TODO: visibility scopes PUBLIC, PARTY, GM, CHARACTER:<id>, NPC:<id> (+ FACTION,
GROUP). Facts carry explicit visibility. Context construction filters facts per
active viewpoint. Tests early.

Amended 2026-09-21: replace the single confirmed/visible distinction with four
independent axes:
- canon state: `proposed` | `confirmed`
- knowledge state: `unrevealed` | `known`
- visibility scope: PUBLIC | PARTY | GM | CHARACTER:<id> | NPC:<id> | FACTION | GROUP
- temporal scope (world year, valid_from / valid_until)

Lifecycle:

```
LLM extraction / inference
        |
        v
    PROPOSED
        |   GM promotes
        v
   CONFIRMED
        |   GM reveals / players discover
        v
      KNOWN
```

Separating promotion from reveal separates what is true from who knows it. A
confirmed fact must never become player-known as a side effect of confirmation.

Clarified 2026-09-21: independent representation does not mean every Cartesian
combination is semantically valid. Storage keeps the axes independent; a separate
invariant layer rejects combinations that are nonsense.

Default invariants for the canon/knowledge pair:

| canon state | knowledge state | valid |
|---|---|---|
| CONFIRMED | UNREVEALED | yes |
| CONFIRMED | KNOWN | yes |
| PROPOSED | UNREVEALED | yes |
| PROPOSED | KNOWN | no, by default |

The last row is the invariant behind the Phase 33 regression test that revealing
an unconfirmed proposed fact is rejected.

### Phase 15: Relationship Graph
Status: PENDING
TODO: lightweight graph over SQLite. Relationships: source, relationship_type,
target, metadata, visibility, valid_from, valid_until. No Neo4j.

### Phase 16: NPC Model
Status: PENDING
TODO: structured NPC state (id, identity, public, private, knowledge, agenda,
relationships, clocks, system_state). System-agnostic; no requirement of HP/classes.

### Phase 17: Content-Pack Architecture
Status: PENDING
TODO: non-executable content packs (rules, setting, adventure, campaign-seed,
supplement) with manifests. gm_only material (secrets, npc-agendas). No arbitrary code.

### Phase 18: Document Storage
Status: PENDING
TODO: `library/raw/` vs `library/processed/`. Raw read-only mounts in Docker.
Ignores for copyrighted material.

### Phase 19: Document Ingestion
Status: PENDING
TODO: `DocumentIngestor` interface (supports()/ingest()). Markdown, txt, PDF first.
Preserve source file, page, section, heading, doc id, content pack, system id,
edition/version, visibility.

Amended 2026-09-21. File format and document shape are different things. A PDF
may hold structured rules, novel prose, adventure prose, tables, or reference
entries. Pipeline:

```
raw document
    |
text extraction
    |
deterministic document-shape detection
    |
    +-- structured parser
    +-- prose extractor
    +-- reference/table parser
    +-- unsupported / manual review
    |
model extraction where required
    |
ProposedExtraction records
    |
deterministic validator / importer
    |
pending canon (proposed)
```

Requirements:
- Shape detection is deterministic (headings, dialogue quotes, speech verbs,
  sentence structure). The LLM does not choose the ingestion route.
- The model extractor never writes authoritative state. It emits
  `ProposedExtraction` records bounded by closed entity/fact schemas.
- The importer performs deterministic validation, holds no model credentials,
  and requires no network access.
- Malformed names, fragments, and rejected subjects are filtered deterministically
  before import.
- Long reads are durable resumable jobs, not request-scoped operations.
- Resume identity is file content hash plus parser version plus slice strategy
  version, so a file replaced under the same name cannot resume an incompatible
  job.
- Completed slices survive partial failure. Failed slices are reported explicitly.
- Cost and slice estimates are available before expensive extraction where
  practical.

Clarified 2026-09-21:
- Promotion of an imported proposal changes canon state only. Provenance
  survives promotion. Detachment is a separate, explicit operation that transfers
  ownership away from the source document.
- Extracted provenance resolves to exact source-version identity, either stored
  directly on the record or reachable through `import_job_id`:

```
fact
 -> extraction
 -> slice
 -> ingest job
 -> exact document hash
 -> exact parser / extractor / slice-strategy version
```

  The system must be able to state not just "this came from book.pdf" but "this
  came from SHA X, slice 47, extracted by version Y during job Z". That identity
  is what makes reversible import and extraction debugging tractable months later.

### Phase 20: RAG Architecture
Status: PENDING
TODO: `Retriever.search(query, filters, limit)`. SQLite FTS5 + vector + metadata
filtering. Namespaces for system/setting/adventure/campaign/rulings/character/npc.
Create `docs/retrieval.md`.

Amended 2026-09-21:
- Setting and campaign are distinct retrieval namespaces. Campaign retrieval
  overlays setting retrieval rather than merging into it.
- Retrieval results preserve refetchable source references (tool plus arguments
  sufficient to fetch the full record again).
- Retrieval remains a lookup path, never the source of campaign truth.

### Phase 21: Retrieval Precedence
Status: PENDING
TODO: precedence: campaign rulings -> campaign house rules -> active system rules ->
enabled supplements -> adventure-specific rules -> GM adjudication. Separate lore vs
mechanics namespaces. Original source reference returnable.

Amended 2026-09-21. Extended precedence order:

```
campaign rulings
campaign house rules
campaign-specific canon
shared setting canon
active system rules
enabled supplements
adventure-specific rules
GM adjudication
```

Where campaign and setting facts conflict: the campaign fact wins for that
campaign, the conflict is surfaced to the GM, and the shared setting is not
automatically rewritten.

### Phase 22: Rule References
Status: PENDING
TODO: retrieval, persistence integration, and precedence around the
Phase 8 `RuleReference` transport type (`source_id`, `title`, `section`,
`page`, `document_path`, `chunk_id`). Deterministic resolutions return
rule refs where available.

Amended 2026-09-21: provenance fields sufficient for reversible deletion and
audit: originating document, page/section, chunk/slice, ingest job, and
parser/extractor version where relevant.

Clarified 2026-09-21: rule-reference provenance resolves to the same
source-version identity as Phase 19 (document hash, slice, ingest job, parser and
extractor version), directly or through `import_job_id`.

### Phase 23: Campaign Rulings
Status: PENDING
TODO: first-class rulings (id, campaign_id, system_id, question, decision, scope,
source refs, session, timestamp, supersedes). Searched before generic sourcebooks.
Not modifications to base rules.

Amended 2026-09-21: rulings participate in the canon lifecycle of Phase 14. A
ruling may be recorded as proposed and promoted by the GM, and promotion is
independent of whether the ruling is disclosed to players.

### Phase 24: Omega Skills
Status: PENDING
TODO: tabletop skills: current-campaign, current-scene, query-rules, query-campaign,
resolve-action, roll, get-entity, get-relationships, record-ruling, end-session.
Clear descriptions. No low-level DB ops exposed to LLM.

Amended 2026-09-21: group skills by workspace capability instead of exposing
every skill everywhere. Forbidden operations are absent from the tool surface,
not merely prohibited by prompt text.

```
SETTING workspace
  can:    query setting, edit setting, manage world entities, manage world history
  cannot: read sessions, mutate quests, inspect party state, read campaign secrets

CAMPAIGN workspace
  inherits setting read access
  plus session, party, thread, and campaign tools
```

### Phase 25: Omega Prompt Extension
Status: PENDING
TODO: prompt extension via Omega plugin API. Policies: runtime authoritative for
game state; no invented mechanical outcomes; rules retrieval before asserting
uncertain mechanics; visibility scoping; semantic recall not authoritative; source
refs for disputed rules; record durable rulings; narration reflects plugin
resolution; no silent sourcebook modification. Keep concise.

### Phase 26: Freeform Reference System
Status: PENDING
TODO: `systems/freeform/`. Basic dice, generic actions, generic opposed checks,
generic resources. No classes/HP/AC/initiative. Test harness. Platform runs with only
freeform installed.

### Phase 27: D&D 5e Reference Plugin
Status: PENDING (after generic API works)
TODO: one rules revision (2014 or 2024, not mixed). Enough mechanics to validate
architecture: ability checks, saving throws, attack rolls, adv/disadv, AC, damage, HP,
basic conditions, initiative, simple movement, basic rests/resources. No full
spellcasting/classes/monsters/feats/multiclassing.

### Phase 28: Plan GURPS as Second Validation System
Status: PENDING
TODO: `docs/gurps-validation.md`. Check API for hidden D&D assumptions: 3d6 roll-under,
active defenses, maneuvers, skill defaults, hit locations, damage types, DR, HP/FP,
advantages/disadvantages, contests, encumbrance. Fix API before declaring stable.

### Phase 29: Session Model
Status: PENDING
TODO: session id, campaign id, start/end time, participants, transcript, event range,
summary, important facts, open threads. End-of-session checklist (close event range,
summary, projections, retrieval updates, preserve NPC agendas, clocks, rulings,
events untouched). LLM summaries are not the sole historical source.

### Phase 30: Context Builder
Status: PENDING
TODO: context service assembling current scene, active entities, viewer-visible facts,
recent events, relevant history, NPC agenda (GM side), relationships, system info,
retrieved rules, campaign rulings. No full-file dumps into prompts.

Amended 2026-09-21:
- `ContextEntry` carries `content`, `token_cost`, `refetch_tool`, `refetch_args`,
  `priority`, `compacted`, so compaction is deterministic.
- Compaction is refetch-based, not summarization. An aged tool result becomes a
  one-line stub naming the tool that can fetch it again, for example
  `[Compacted entity record: Ithrazel. Refetch with get-entity("ithrazel").]`
- Budget is computed, not hardcoded:
  `model context size - prompt reserve - response reserve - session reserve =
  retrieval/tool budget`, then allocated by priority. A single fixed tool-result
  cap fails as soon as a large chapter and campaign history compete for it.
- Newest and highest-priority material is retained first.
- Setting/campaign precedence (Phase 21) is applied during context construction.
- Contradiction detection runs before presenting claims about already-established
  entities: retrieve adjacent canon, compare, then emit either no conflict or a
  `ConflictCandidate` (existing fact, source, session, reason). Advisory only; it
  never rewrites canon.
- The workspace tool surface (Phase 24) bounds what the context builder may read.

### Phase 31: Security
Status: PENDING
TODO: `docs/security.md` (prompt injection from docs, sourcebooks as data, plugin
trust boundaries, read-only mounts, no docker socket, constrained discovery, path
traversal rejection, secrets outside content). Tests for path normalization and
manifest validation.

Amended 2026-09-21:
- Extraction and authoritative import are separate trust boundaries.
- Model-produced extraction is always untrusted proposal data.
- The importer runs without model credentials and without network access where
  practical.
- Invalid or version-incompatible extraction cannot mutate canon directly.
- Configured executable plugin roots stay distinct from document and content roots.

### Phase 32: Docker and Portainer
Status: PENDING
TODO: `docker-compose.yml` for Portainer Git Stack. Persist Omega memory and tabletop
state, mount plugins ro, library ro, campaigns rw. No docker socket. No hardcoded
dev paths. Provide `.env.example`.

### Phase 33: Testing
Status: PENDING
TODO: tests for plugin discovery, visibility, events, dice, retrieval, resolution,
documents. Contracts first, not D&D behavior.

Amended 2026-09-21. Regression tests required for the new invariants:
- Confirming a fact does not reveal it.
- Revealing an unconfirmed proposed fact is rejected or handled explicitly.
- Deleting an imported document removes only records derived from that document.
- Campaign canon overrides setting canon without mutating the setting.
- Deterministic shape detection routes prose away from structural parsing.
- Malformed extraction cannot reach authoritative state.
- An interrupted ingest resumes only when file hash and slicing strategy match.
- Context compaction stubs can actually refetch their original data.
- Setting-only workspaces do not expose campaign operations in their tool surface.

Clarified 2026-09-21. Additional regression tests:
- Promoting an imported fact preserves its provenance.
- Purging a source document removes attached imported records including promoted
  ones, and leaves detached records intact.
- A proposed fact cannot be stored in the KNOWN knowledge state.
- Orchestration cannot emit a `Resolution` for an action the active system
  declares resolvable without calling the plugin.
- `UNSUPPORTED` and `UNRESOLVED` both route to adjudication and neither yields
  LLM-authored mechanical numbers.

### Phase 34: First Demonstration
Status: PENDING
TODO: freeform example campaign (17-step flow). D&D 5e demonstration (8-step combat).

### Phase 35: README
Status: PENDING
TODO: rewrite project README (What Gamemaster is, Why Omega, Architecture, plugins,
content packs, persistence, RAG, maturity, quick start, Docker/Portainer, creating
plugins/packs, security warning, roadmap, upstream attribution). No over-claiming.

### Phase 36: Roadmap
Status: PENDING
TODO: `docs/roadmap.md` milestones 0-7 (research/architecture, runtime skeleton,
persistence, documents/RAG, play loop, D&D 5e reference, GURPS validation,
UI/channel improvements).

Amended 2026-09-21. Post first-draft items, deliberately outside Phases 8-35:
- Task lane (conversation, rules lookup, document extraction, scene drafting,
  summarization, judgment) as an axis independent of quality tier, latency
  preference, and cost preference. Collapsing the two axes causes misrouting.
- Model availability and error-rate tracking.
- Typed advisory judges with structured output and no authority to mutate state:
  contradiction triage, visibility-leak candidates, canon promotion priority,
  narration-policy violations.
- Per-turn cost, token, and tool receipts.
- Disconnect-safe long-running generation.

### Phase 37: Decision Log
Status: PENDING
TODO: ADRs 0001-0008 in `docs/decisions/` (Status, Context, Decision, Consequences,
Alternatives).

### Phase 38: Git Hygiene
Status: PENDING
TODO: verify nothing staged except intended files (no research/, no PDFs, no keys,
no .env, no .sqlite, no vector data, no model cache). Logical commits per suggested
boundaries.

### Phase 39: First Draft Definition of Done
Status: PENDING
Checklist: Omega starts normally; tabletop plugin loads; separate plugin API exists;
system plugins discovered without editing Omega core; freeform works; campaign
creation; SQLite persistence; state survives restart; visibility-scoped facts;
relationships stored/queried; PDF and Markdown ingested; raw files accessible;
provenance preserved; rules retrieval filtered by namespace; generic dice resolved
deterministically; unresolved action requests GM adjudication; Omega narrates
returned resolution; session history persisted; small D&D 5e plugin proves APIs;
tests cover major boundaries; Docker/Portainer documented; roadmap present.

Added 2026-09-21: proposed facts require explicit promotion before becoming canon;
promotion and reveal are separately auditable; deleting an ingested document
removes exactly its derived records; an interrupted ingest resumes from a slice
boundary; a compacted context entry can be refetched from its recorded tool call.

### Phase 40: Final Agent Report
Status: PENDING
TODO: repo state (upstream commit, branch, files added, Omega core files modified),
research findings, architecture implemented, runnable functionality, tests
(run/passed/failed/untested), known limitations, architectural risks, ranked next steps.
No placeholders presented as implemented.

## Current TODO state (tracker)

- [x] Phase 1: Bootstrap repo from Omega
- [x] Phase 2: Research workspace and prior art
- [x] Phase 3-4: Architecture docs and project structure
- [x] Phase 5: Omega adapter plugin
- [x] Phase 6: Game-system plugin API
- [x] Phase 7: Plugin discovery
- [x] Phase 8: Universal action and resolution models
- [ ] Phase 9: Deterministic mechanics boundary
- [ ] Phase 10: Dice engine
- [ ] Phase 11-16: Campaign persistence, events, visibility, relationships, NPC
- [ ] Phase 17-21: Content packs, document storage, ingestion, RAG
- [ ] Phase 22-28: Rule refs, rulings, skills, prompt ext, freeform, dnd5e
- [ ] Phase 29-36: Session model, context, security, docker, tests, demos, README
- [ ] Phase 37-40: Decisions, git hygiene, first draft verification, final report

## Current code on `main` (after Phase 8)

HEAD after PR #6: `60ae427`.

Implemented:
- `tabletop/api/plugin.py`, `capabilities.py`, `errors.py`
- `tabletop/api/{entities,actions,resolution,events,rules,_contract}.py`
- `tabletop/plugins/{discovery,manifest,registry}.py`
- `tabletop/runtime.py`, `plugins/tabletop/`, `systems/freeform/`, `systems/dnd5e/`
- Docs: `docs/architecture.md`, `docs/plugin-api.md`, `docs/action-resolution.md`,
  `docs/research/`

Still placeholders: `tabletop/api/visibility.py`, `campaign/`, `dice/`,
`storage/sqlite.py`, `documents/`, `retrieval/`, `orchestration/`.

Owed docs: `campaign-model.md`, `retrieval.md`, `security.md`,
`gurps-validation.md`, `roadmap.md`, `docs/decisions/` ADRs 0001-0008.

Local verification at Phase 8 merge (Python 3.11): 157 tabletop tests, 223
in `tests/`. CI on that HEAD: autotests pass, common pass.

## Verification commands

Upstream state:
- `git remote -v` (expect `upstream` plus the project `origin`)
- Original Omega bootstrap commit remains
  `7b060f5738ee7b8cf064c8b6282ed9fe07cf407f` in `UPSTREAM.md`. Current HEAD
  is expected to have moved; do not require `git log -1` to equal `7b060f5`.
- Implementation work uses a per-phase feature branch. Integration target
  is `main`.

Tests:
- `python -m pytest tests/ -v`

Omega still starts normally (must not break):
- `sh run.sh run.metta` (or the current Omega launcher)

Git hygiene before each commit:
- `git status --short` - confirm no research/, no PDFs, no keys, no .env,
  no .sqlite, no vector data, no model cache staged.