# MythWeaver — Research Report

- Repository: https://github.com/xavibonell/mythweaver
- Cloned at: `research/mythweaver`
- License: none (all rights reserved)
- Reuse verdict: no code reused; clean-room

## Purpose

Self-hosted, LLM-driven Dungeon Master for tabletop RPGs (v1 targets D&D 5e). Two ideas carry the
whole system, stated in `README.md`: (1) a deterministic engine owns every number — the LLM narrates
and calls tools, it never invents a mechanic; (2) a fully dynamic, LLM-directed scene UI — the model
directs in semantic terms and a deterministic layer plus a curated tile library resolve that into a
walkable top-down map ("the model never paints a pixel"). The repo ships a full spec
(`docs/MythWeaver-Dev-Spec.md`), a phased build plan (`docs/BUILD-PLAN.md`), and design docs for the
player interface (`docs/PLAYER-INTERFACE.md`), spatial truth (`docs/SPATIAL-TRUTH.md`), and the
interaction layer (`docs/INTERACTION-LAYER.md`).

## Architecture

TypeScript monorepo: `packages/engine`, `packages/llm`, `packages/rag`, `packages/scene`,
`packages/shared`, plus `apps/server` (Fastify turn loop, projections, orchestration) and `apps/web`
(Next.js/React with a Phaser renderer). `packages/llm` puts Anthropic/OpenAI/Gemini behind a
`LlmProvider` seam (`packages/llm/src/provider.ts`) with a factory and router
(`packages/llm/src/factory.ts`, `packages/llm/src/router.ts`) and a `fake-provider.ts` used by the
whole test suite.

The load-bearing seam is `EngineTools` in `packages/shared/src/engine-contract.ts`: a typed interface
listing every mutation the orchestrator may expose to the LLM (getState, rollDice, requestRoll,
submitRoll, resolveCheck/Save/Attack, applyDamage, startCombat, item/economy tools, rest tools,
POI tools, spell resources). The orchestrator (`apps/server/src/orchestrator.ts`) converts tool-use
blocks into engine calls and returns verdict facts; it is the only path from LLM output into game
state. The interaction resolver (`apps/server/src/interactions.ts`) states the principle directly in
its header: "the ENGINE — not the narrator — decides who perceives the act (the witness oracle), how
each NPC answers it (persona), and walks them on the REAL map via engine.travel; the DM narrates ONLY
the returned verdict facts."

## Persistence

PostgreSQL, schema in `db/init/001_init.sql`, accessed via `apps/server/src/db.ts`:

- `sessions` — one row per game, with the authoritative `GameState` snapshotted as a JSONB `state`
  blob (`saveState` rewrites it each turn).
- `messages` — the canonical transcript as a separate table (id, session_id, kind, speaker, text),
  explicitly not inside the state blob; `recentMessages` serves the context window.
- `rag_chunks` — embeddings for retrieval, with a `namespace` column and an HNSW index over
  `vector(1024)`.

## RAG & Retrieval

A fallback ladder, not a fused hybrid. `buildRetriever` in `apps/server/src/corpus.ts` picks the best
available tier, degrading gracefully:

1. in-memory vector (semantic) — cached vectors file plus an embeddings key, no DB
   (`InMemoryVectorRetriever`, `packages/rag/src/retriever.ts`);
2. pgvector — `MYTHWEAVER_RAG=pgvector` plus a Voyage key (`DbRetriever`,
   `apps/server/src/retriever.ts`, `db.searchChunks`);
3. in-memory keyword (BM25) — offline, no key or DB (`InMemoryRetriever`, with a small suffix
   stemmer and stopword list);
4. none — the DM runs without a `lookupRule` tool and falls back to its own knowledge.

The retrieval seam is deliberately narrow (`packages/rag/src/retriever.ts` header): RAG supplies
rules TEXT for narration and lookup, it never resolves a mechanic. Every chunk carries a prose
`source` field for citation. Vector dimension is treated as a hard invariant:
`packages/rag/src/embedding.ts` documents that `dimension` must equal the pgvector column dimension
(1024 for both voyage-3-large and text-embedding-3-large via output-dimension reduction), and
changing vendor or dimension requires a full re-embed plus schema migration.

The lore namespace was designed (`docs/LORE-GROUNDING.md` describes a three-namespace split: RULES,
STYLE/VOICE exemplars, and a future LORE namespace) and then falsified by a cheap knowledge probe:
the base model already produced deeply accurate core-D&D canon, so a lore-RAG vertical would add
little; the probe exposed variety/consistency as the real gap, which shipped as a lightweight
composer variety nudge instead of any corpus. The doc is kept in-repo with its "falsify
cheapest-first" ladder as a record.

## Document Ingestion

`scripts/extract-corpus.py` uses PyMuPDF (`fitz`) to turn a folder of licensed PDFs into a chunked
JSONL corpus (default `content/corpus/dnd.jsonl`), ~1100-char chunks with 150-char overlap, output
gitignored and explicitly restricted to material the operator is licensed to use. `packages/rag/src/
chunk.ts` mirrors the same chunker for non-PDF text. `scripts/embed-corpus.mjs` embeds the JSONL once
and caches vectors to `<file>.vectors.jsonl` for the in-memory vector tier; `scripts/ingest-corpus.mjs`
embeds into pgvector with a namespace argument (schema default `srd`, ingest default `dnd`) only for
the pgvector path. A parallel exemplar pipeline (`scripts/ingest-exemplars.mjs`,
`apps/server/src/exemplar-ingest.ts`, `apps/server/src/exemplar-corpus.ts`) distills real-DM
transcripts into a per-turn STYLE/VOICE retriever — exemplars feed voice only, guarded against
content copying, and retrieval failure never breaks a turn.

## Deterministic Mechanics

The engine (`packages/engine/src/engine.ts`, implementing `EngineTools`) owns every number: derived
modifiers, AC, saves, DCs, HP math, XP and level-up, item economy in integer copper. The LLM affects
state only by calling engine tools. Unbuilt mechanics throw a phase error: `NotImplemented` in
`packages/shared/src/engine-contract.ts` is raised by methods whose ramp tier (P0–P4, documented on
each interface method) is not yet implemented — `engine.ts` e.g. throws `NotImplemented('resolveAttack',
'P1')` — "so a premature LLM-side resolution surfaces loudly instead of silently bending the rules".

Physical dice use a suspend loop with range validation (`packages/engine/src/dice.ts`): `requestRoll`
creates a `RollRequest`; `GameState.pendingTurn` persists the in-flight turn (message history,
resolved tool results, DM-recorded facts) and the session pauses; `submitRoll` validates the
declared total against the legal range derived from the `NdX±M` expression — `in_range` accepted,
`ambiguous` accepted but recorded unvalidated (within `unmodeledBonusMax` slack), `out_of_range`
flagged and not applied, after which the DM re-requests. The engine "trusts plausible declarations
and only intervenes on the impossible — it never fabricates a roll to 'correct' the player". The
pending map is in-memory, so `Engine.registerRoll` re-registers requests across a save/resume
boundary. [DISCREPANCY: prior-art.md summarizes this as rolls "validated against engine rolls ...
until a declared roll matches"; the code validates the declared total against the derived legal
range, not against a parallel engine-generated roll.]

## Campaign State

`GameState` (`packages/shared/src/domain.ts`) is a full read-model: combatants with derived sheets,
combat/initiative, flags, the canon `LedgerState` (entity cards, append-only facts, plants), a
pending turn, the world graph of frozen `SceneMap`s with POIs, budget fields, turn count, and the
journal. The whole blob is snapshotted into the `sessions.state` JSONB column each turn
(`apps/server/src/db.ts`).

Cheap restart paths: `apps/server/src/content.ts` loads frozen `content/pregens/*.json` (complete
generated-arc bundles that start a campaign with zero model calls) and `content/dev-sessions/*.json`
(full captured GameStates with a scene already rendered, hydrated by `dm-lab.ts` into a fresh Engine
for instant `$0` play; a stale `pendingTurn` carried by a freeze is dropped on load). The orchestrator
also reuses frozen maps verbatim on location re-entry and reports that honestly in
`sceneProvenance`.

## NPC Model

Two-tier persona (`packages/shared/src/persona.ts`), designed to be thin — the header records that
the design review rejected simulated minds. Tier-1, `profileOf(subject)`, derives archetype
(authority/keeper/cleric/commoner/beast/monster) and temper from role/tag/id word signals plus a
stable id-hash for crowd variety — total, deterministic, and retroactive over frozen scenes at zero
storage. Tier-2, `PersonaSeed` on the `EntityCard`, is an optional authored override for named cast
(allegiance, one-line stake, overriding archetype/temper). `personaOf(subject, card?)` is the single
entry point; persona is data the reaction resolver reads and never moves anyone itself.

Standing is not stored on the NPC. It lives as a mutating fact in the append-only ledger:
`STANDING_ATTR = 'standing:party'` (`apps/server/src/interactions.ts`), clamped to −3..+3, seeded
from authored allegiance, docked by witnessed crimes (theft −1, assault on a non-hostile −2, the
victim docked steeper). `Engine.recordFact` (`packages/engine/src/engine.ts`) appends a `FactRow`
and marks any older same-subject/same-attribute fact with `supersededBy` — append-only for audit,
latest wins for reads. A party-perceived dossier (`apps/server/src/profiler.ts`) is composed only
from the previous sheet plus the words spoken aloud this turn — it never sees the ledger's
want/fear/notes, the arc, or any DC, so it cannot leak internals by construction.

## Visibility & Private Information

The strongest design here, described as "THE LAW" in `apps/server/src/player-view.ts`:
a datum reaches players only when play surfaced it. Player-safe text is composed at write time, not
redacted at read time — the header records the failure it fixes: the table had been rendering a
DM-grade payload where "the secrets sat in the browser heap the whole time — client-side hiding is
not hiding."

Mechanics, all in `player-view.ts`:

- A whitelist projection: player objects are built field by field rather than deleted from the DM
  object, so a new DM-side field is player-invisible by default instead of leaking until someone
  remembers it.
- Secret engine state keys (`rx:`, `cmd-refused:`) are excluded by a regex
  (`SECRET_STATE_KEY`); the DM's `updateScene` path cannot write them (only the trusted interaction
  writer can).
- One masking rule for unidentified items (`maskItem`, tri-state `identified`) shared by the sheet
  and loot prose.
- The player scene map strips hidden tokens, actors concealed indoors, engine bookkeeping, and exit
  destinations ("a door labelled `loc:smugglers-cave` spoils what is behind it"). Scene deltas are
  filtered by resolved visibility, not by op shape; the player turn view strips tool traces (they
  carry DCs and POI contents), state diffs, and provenance.
- The Book (`playerBook`) is a pure fold over the journal, which is player-safe at write time.

The engine-side counterpart is the witness oracle: `packages/engine/src/witness.ts` computes, for
every journal event, the deterministic set of PCs who perceived it (geometric — present in the
location, or within 60 ft earshot of an origin), and `apps/server/src/interactions.ts` grades each
onlooker saw/heard/alerted/oblivious from zones, earshot, and line-of-sight — "the engine, not the
narrator" (`apps/server/src/scene-graph.ts` supplies the zone/earshot/LOS graph).

## Session History

The canonical transcript lives in the `messages` table (`apps/server/src/db.ts`), not in the state
blob. Composers receive only a recent window: `orchestrator.ts` slices `recentTranscript` to the
last 12 lines (dropping bare roll-declaration lines) for planning. Anything older that must persist
lives in structured state: the canon ledger surfaces durable facts at `$0` every turn ("this is how
a returning NPC keeps its voice and the silver key stays remembered past the 12-line window"). The
engine-owned journal (`packages/shared/src/journal.ts`) is a separate append-only stream recording
what the table witnessed — written at verdict moments rather than mined from the engine log
afterwards, with a deliberately small event vocabulary (prologue, chapter, beat, met, verdict,
disposition, finding, loot, clue, insight, decision) that excludes rolls, damage, and inventory
already owned by other surfaces.

## Relationship Graph

Not present as a graph structure. Social state is flattened into the append-only facts ledger (a
scalar `standing:party` per entity plus discrete facts). The "scene graph"
(`apps/server/src/scene-graph.ts`) is geometrical — buildings, zones, a 60 ft earshot constant,
line-of-sight — used to decide perception, not relationships. The NPC dossier ("who we've met") is
folded from witnessed journal events rather than stored as edges.

## Rule-Source Citation

Retrieved chunks carry a prose `source` field (`RetrievedChunk.source` in
`packages/rag/src/retriever.ts`) intended for citation, e.g. "Player's Handbook p.123"; the SRD
material shipped under `content/srd/*.md` is license-appropriate. `rag_chunks.source` persists the
same provenance through pgvector. The rules namespace is kept separate from the style/voice exemplar
namespace and lore was designed as a third isolated namespace (never visible to `lookupRule`),
per `docs/LORE-GROUNDING.md`.

## Useful Ideas

- Engine-owns-numbers with an explicit `NotImplemented` phase error for unbuilt mechanics — the game
  cannot silently invent them.
- Whitelist player projection with write-time player-safe composition: leakage-proof by
  construction, and new fields default to invisible.
- The journal composed player-safe at write time, from what the table witnessed, with geometric
  witness sets recorded even before any per-player UI exists.
- Append-only facts ledger with supersede marking for evolving standing and canon.
- RAG degrade ladder with a hard vector-dimension invariant; retrieval as a narrow text-for-narration
  seam, never a mechanic resolver.
- Provenance-carrying retrieved chunks.
- Falsify-cheapest-first culture: a `$3–4` knowledge probe killed a whole lore-RAG vertical before a
  corpus was built (`docs/LORE-GROUNDING.md`).
- Zero-cost replay/restart: frozen pregen arcs and captured dev-session states; frozen maps reused
  verbatim on re-entry with honest provenance.
- Physical-dice trust: range validation that trusts plausible declarations and only rejects the
  impossible, with the turn persisting across a suspend/resume boundary.

## Ideas Rejected

- A fully-fledged social relationship graph: falsified for well-known settings; a scalar standing
  plus the facts ledger suffices (the relationship "graph" that remains is geometrical, not social).
- Fused hybrid retrieval: a cascade ladder is simpler, degrades gracefully, and caches better
  (in-memory vectors need no DB for small corpora).
- Simulated NPC minds: the persona layer is deliberately thin (a design review rejected "25
  simulated minds"); reaction goals are dumb one-way waypoints with a TTL that can never
  re-evaluate the world.
- Lore-RAG over core D&D: falsified by the knowledge probe; replaced by a composer variety nudge.

## License & Reuse

No license file in the clone; prior-art.md records "none (all rights reserved)". The DawnLike tile
assets are CC-BY and third-party (noted in `README.md`), which does not license the code. Verdict
unchanged: no code reused; clean-room.

## Key Files

| Path | Role |
| --- | --- |
| `packages/shared/src/engine-contract.ts` | The typed tool seam: `EngineTools` interface, `NotImplemented`, roll request/result types |
| `packages/engine/src/engine.ts` | The deterministic engine implementing the contract; facts ledger, travel gate, opportunity attacks |
| `packages/engine/src/dice.ts` | Dice + declared-roll range validation (`in_range`/`ambiguous`/`out_of_range`) |
| `packages/engine/src/witness.ts` | Geometric witness set for journal events |
| `packages/shared/src/domain.ts` | `GameState`, `LedgerState`, `pendingTurn`, POIs, budget |
| `packages/shared/src/persona.ts` | Two-tier persona (derived profile + authored seed) |
| `packages/shared/src/journal.ts` | The append-only, write-time player-safe journal event stream |
| `packages/rag/src/retriever.ts` | `Retriever` seam, BM25 and in-memory vector tiers, provenance field |
| `packages/rag/src/embedding.ts` | Embedding provider seam with the dimension invariant |
| `apps/server/src/orchestrator.ts` | The turn loop: tool dispatch, suspend/resume, context assembly, scene freeze/reuse |
| `apps/server/src/player-view.ts` | The whitelist player projection and Book fold |
| `apps/server/src/interactions.ts` | Witness oracle, reaction/command lanes, standing facts |
| `apps/server/src/profiler.ts` | Party-perceived NPC dossier from words spoken aloud |
| `apps/server/src/corpus.ts` | Retriever fallback-ladder construction (vector → pgvector → BM25 → none) |
| `apps/server/src/db.ts` / `db/init/001_init.sql` | PostgreSQL access and schema (sessions JSONB, messages, rag_chunks) |
| `apps/server/src/content.ts` | Pregen bundles and frozen dev-session load/freeze paths |
| `scripts/extract-corpus.py` | PyMuPDF PDF → chunked JSONL corpus extraction |
| `docs/LORE-GROUNDING.md` | The lore-namespace falsification record and cheapest-first ladder |
