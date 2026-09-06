# Gamemaster: Omega-based Tabletop RPG Platform - Execution Plan

Status: ACTIVE. Working branch: `tabletop-platform`.
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
- Upstream commit: `7b060f5738ee7b8cf064c8b6282ed9fe07cf407f`, branch `main`.

Design decision for the tabletop plugin:
- `plugins/tabletop/tabletop.metta` is the thin Omega-facing glue (a MeTTa plugin,
  `loader: metta`).
- `tabletop/` is a pure-Python package (Tabletop Runtime) with zero MeTTa/Omega
  imports. It is standalone-testable via pytest.
- Game-system plugins (freeform, dnd5e) are Python packages in `systems/` loaded
  at runtime by the Python plugin registry. They never depend on Omega/MeTTa.

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
Status: PENDING
TODO: create `docs/architecture.md` defining layers:
Player/GM -> Omega channel/provider/runtime -> omega-tabletop adapter -> Tabletop
Runtime -> (Game System Plugins, Content Packs, Campaign Store, Rules/RAG, Event Log,
Visibility Engine, Relationship Graph, Source Document Store).
Key rule: Omega provides cognition. Tabletop Runtime provides orchestration and
continuity. Game plugins provide mechanics. Content packs provide rules and world
material. Campaign state provides truth. Omega-facing code is thin. Game-system
plugins must not depend on Omega or MeTTa.

### Phase 4: Create the Project Structure
Status: PARTIAL (directories created; code TODOs below)
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
Status: PENDING
TODO: one plugin `tabletop`, the only Omega-specific integration point.
Responsibilities: initialize Tabletop Runtime; register tabletop skills; add prompt
instructions; discover configured campaigns; discover game-system plugins; translate
Omega skill calls into Tabletop Runtime calls; translate results into safe
serializable responses.
Expected entry point: `loadOmegaPlugin()` (MeTTa glue + Python bridge).
Add `tabletop` to `config/plugins.yaml`. Document any modification to upstream
Omega files.

### Phase 6: Define the Game-System Plugin API
Status: PENDING
TODO: `GameSystemPlugin` class (id, api_version, capabilities(), resolve(), etc.)
per prompt. Capability negotiation. Create `docs/plugin-api.md` (manifest format,
API versioning, discovery, lifecycle, capability negotiation, errors, system-specific
state, extension points).

### Phase 7: Plugin Discovery
Status: PENDING
TODO: `tabletop/plugins/{manifest,discovery,registry}.py`. Support discovery without
rebuilding Omega (`/tabletop/plugins/` mounted dir). Executable system plugins vs
non-executable content packs kept separate. No dynamic execution of untrusted
uploaded docs.

### Phase 8: Universal Action and Resolution Models
Status: PENDING
TODO: `GameAction`, `ResolutionContext`, `Resolution` dataclasses (per prompt).
Core runtime asks plugin to resolve actions; core must not know meaning of AC,
saving throw, hit location, spell slot, mana, refresh, sanity.

### Phase 9: Deterministic Mechanics Boundary
Status: PENDING
TODO: encode LLM vs plugin boundary in code and docs. When deterministic resolution
cannot decide: `requires_ruling = True` with rule refs and context. No LLM fabrication.

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

### Phase 12: Event-Sourced History
Status: PENDING
TODO: append-only immutable events with sequence, event_type, campaign_id, session_id,
scene_id, actor_id, target_id, payload. Projections derive current state from events
where practical. Inspectable.

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

### Phase 20: RAG Architecture
Status: PENDING
TODO: `Retriever.search(query, filters, limit)`. SQLite FTS5 + vector + metadata
filtering. Namespaces for system/setting/adventure/campaign/rulings/character/npc.
Create `docs/retrieval.md`.

### Phase 21: Retrieval Precedence
Status: PENDING
TODO: precedence: campaign rulings -> campaign house rules -> active system rules ->
enabled supplements -> adventure-specific rules -> GM adjudication. Separate lore vs
mechanics namespaces. Original source reference returnable.

### Phase 22: Rule References
Status: PENDING
TODO: `RuleReference` dataclass (source_id, title, section, page, document_path,
chunk_id). Deterministic resolutions return rule refs where available.

### Phase 23: Campaign Rulings
Status: PENDING
TODO: first-class rulings (id, campaign_id, system_id, question, decision, scope,
source refs, session, timestamp, supersedes). Searched before generic sourcebooks.
Not modifications to base rules.

### Phase 24: Omega Skills
Status: PENDING
TODO: tabletop skills: current-campaign, current-scene, query-rules, query-campaign,
resolve-action, roll, get-entity, get-relationships, record-ruling, end-session.
Clear descriptions. No low-level DB ops exposed to LLM.

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

### Phase 31: Security
Status: PENDING
TODO: `docs/security.md` (prompt injection from docs, sourcebooks as data, plugin
trust boundaries, read-only mounts, no docker socket, constrained discovery, path
traversal rejection, secrets outside content). Tests for path normalization and
manifest validation.

### Phase 32: Docker and Portainer
Status: PENDING
TODO: `docker-compose.yml` for Portainer Git Stack. Persist Omega memory and tabletop
state, mount plugins ro, library ro, campaigns rw. No docker socket. No hardcoded
dev paths. Provide `.env.example`.

### Phase 33: Testing
Status: PENDING
TODO: tests for plugin discovery, visibility, events, dice, retrieval, resolution,
documents. Contracts first, not D&D behavior.

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

### Phase 40: Final Agent Report
Status: PENDING
TODO: repo state (upstream commit, branch, files added, Omega core files modified),
research findings, architecture implemented, runnable functionality, tests
(run/passed/failed/untested), known limitations, architectural risks, ranked next steps.
No placeholders presented as implemented.

## Current TODO state (tracker)

- [x] Phase 1: Bootstrap repo from Omega
- [x] Phase 2: Research workspace and prior art
- [ ] Phase 3-4: Architecture docs and project structure
- [ ] Phase 5-7: Omega adapter plugin and plugin system
- [ ] Phase 8-10: Action/resolution models and dice engine
- [ ] Phase 11-16: Campaign persistence, events, visibility, relationships, NPC
- [ ] Phase 17-21: Content packs, document storage, ingestion, RAG
- [ ] Phase 22-28: Rule refs, rulings, skills, prompt ext, freeform, dnd5e
- [ ] Phase 29-36: Session model, context, security, docker, tests, demos, README
- [ ] Phase 37-40: Decisions, git hygiene, first draft verification, final report

## Verification commands

Upstream state:
- `git remote -v` (expect `upstream`)
- `git log -1 --oneline` (expect `7b060f5`)
- `git branch --show-current` (expect `tabletop-platform`)

Tests:
- `python -m pytest tests/ -v`

Omega still starts normally (must not break):
- `sh run.sh run.metta` (or the current Omega launcher)

Git hygiene before each commit:
- `git status --short` - confirm no research/, no PDFs, no keys, no .env,
  no .sqlite, no vector data, no model cache staged.