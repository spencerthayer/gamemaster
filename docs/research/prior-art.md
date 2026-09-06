# Prior Art: Tabletop GM Systems Research

This document records architectural findings only. No implementation code from
any research repository was copied into this project. See the license and reuse
section for each project.

Research repositories are cloned under `research/` and are git-ignored.

## Summary table

| Project | License | Code reused | Verdict |
|---|---|---|---|
| AiChatTrpg | Apache-2.0 | no | Permissive, but clean-room approach used |
| dungeonmaster | none | no | All-rights-reserved |
| claude-dnd-skill | AGPL-3.0 | no | Copyleft, network clause |
| open-tabletop-gm | AGPL-3.0-or-later | no | Copyleft, network clause |
| mythweaver | none | no | All-rights-reserved |
| dnd_experience | none | no | All-rights-reserved |

## AiChatTrpg

Repo: https://github.com/Leehow/AiChatTrpg (cloned to `research/AiChatTrpg`)

- Purpose: self-hostable AI game master for TRPGs. Bring-your-own-LLM, ruleset
  PDF plus adventure module as inputs. Single-player local first.
- Architecture: FastAPI backend + React frontend. Portable, framework-agnostic
  engine core in `backend/agents/trpg/framework/` using pure dataclasses as the
  I/O contract and `@runtime_checkable` Protocol adapters (LlmAdapter,
  RulesetAdapter, RetrievalAdapter, MultimediaAdapter, Clock). A single
  `run_trpg_turn` pipeline yields a stream of turn events. App-specific glue
  (ORM, routes) lives outside the portable core.
- Persistence model: PostgreSQL. Session rows carry JSON state columns
  (`memory_state`, `module_state`, `setup_state`, `narrative_summary`,
  `dice_history`). Durable memory lives in `trpg_memory_items` rows with kind,
  visibility, owner, lifecycle status, idempotency key. `check_logs` rows keep a
  full audit trail of every deterministic check.
- RAG design: dual memory. Structured JSON buckets (scenes, NPCs, plot_threads,
  world_facts, recent_events) plus an LLM-compressed rolling narrative summary.
  Durable event-sourced memory with type normalization and provenance. Working
  retrieval is lexical keyword scoring over parsed ruleset package/parameter
  families with a ReAct grep-retrieval agent; the vector RAG layer is an
  in-memory placeholder.
- Document ingestion: ruleset PDF/Markdown/TXT is parsed through a staged LLM
  pipeline into a structured ruleset JSON (core, packages, families, character
  sheet, dice IR), with OCR via MinerU and per-sub-step checkpointing. Adventure
  modules are parsed from PDF/DOCX/PPTX/XLSX/image, outline-normalized, and
  reduced to playable units.
- Deterministic mechanics: seeded, replayable dice with keep-highest/lowest;
  check engines as pure functions (CoC d100, BRP d100, d20-vs-DC, PBTA 2d6,
  pool-count) dispatched through a resolver, plus a versioned dice IR
  interpreter that returns status, axes, and a trace. Consequences engine
  converts result axes into deterministic resource deltas and mandatory
  follow-up checks. An LLM fallback exists but is last resort.
- Campaign state representation: one session row as campaign root holding PC
  card, module state (scene navigation), memory buckets, setup state, dice
  history, and narrative summary.
- NPC state model: static routines profiles (tier, persona, public/private card,
  knowledge fact ids, secret ids, plot constraints) plus per-scene mutable
  states (mood vector, goal pressure, physical state). NPC agents propose
  actions; the scene runtime resolves and integrates them.
- Visibility/private-information handling: a first-class Visibility enum
  (public, party_known, gm_only, npc_private, system_internal), per-NPC name
  reveal gates, visibility filtering on retrieval chunks, player-blocked
  scenario search, and leaker-checking on NPC prompts. Visibility signatures
  feed the prompt-cache key.
- Session history model: single unified message timeline (user/gm/system/dice)
  with a bounded recent-message window and older messages folded into the
  narrative summary. Check results persist as role=system messages for a
  verbatim, attributable record.
- Relationship graph: none as a graph table. Relationships are a memory kind and
  per-NPC relationship vectors over a fixed subject set.
- Rule-source citation model: search hits carry package/family ids, names, and
  snippets rendered with source headings into the transcript. Check logs record
  the resolved procedure id. No page-level citations.
- Useful ideas: portable adapter-protocol engine core; versioned deterministic
  dice IR with trace; seeded replayable dice plus persistent check audit;
  event-sourced, evidence-backed memory with idempotency keys; marker-as-
  escape-hatch text directives instead of function calling; visibility-first
  context construction; dual memory (durable structured + cheap compressed
  narrative).
- Ideas rejected: the in-memory lexical RAG placeholder; JSON-bucket state as
  the authority; the interactive grep ReAct retrieval agent as a per-turn
  latency path; fire-and-forget background NPC workers with polling dance;
  per-turn synchronous memory extraction inside the turn stream.
- License: Apache-2.0.
- Code reused: no. Architecture studied and reimplemented clean-room.

## dungeonmaster (Cogitator / DMbot)

Repo: https://github.com/Pr0degie/dungeonmaster (cloned to `research/dungeonmaster`)

- Purpose: local, voice-only AI game master over Discord, running fully offline
  (Ollama, whisper, TTS). Ruleset and adventure loaded as PDFs; the DM learns
  the setting via RAG and the mechanics via a per-system profile. System-
  agnostic by design.
- Architecture: layered Discord voice pipeline (audio receive, VAD, STT, LLM,
  TTS, speaking bridge). Prompt assembly has a single owner: persona, recap,
  adventure, state summary, NPC memory, RAG, alias hint. Determinism layered via
  stateless constrained-JSON classifier side-calls (roll router, scene router,
  fact router). A golden-transcript evaluator replays transcripts with a mocked
  LLM for refactor regression gates.
- Persistence model: per-voice-channel directory with a strict three-file split:
  read-only character sheet JSON (never rewritten by code), code-owned mutable
  state JSON (atomic temp-file plus replace), and an append-only JSONL turn
  history rotated on session leave. Hard facts are advanced only by code.
- RAG design: three-stage hybrid. Always-on short arc summary; a deterministic
  scene tracker that selects exactly one scene card per turn (the adventure is
  deliberately not vectorized for spoiler discipline); vector RAG over rulebook
  and curated lore with heading-aware chunks, cosine distance in sqlite-vec,
  and a store-level meta table pinning the embedding model. Played-session
  transcripts are ingested per scene into their own corpus keyed by session
  stamp.
- Document ingestion: offline CLI converts legally owned PDFs to markdown, then
  heading-aware chunking (chunks never cross a heading, carrying their section
  title) with batch embedding.
- Deterministic mechanics: dice and resolution are code, never the LLM. A pure
  engine with injected `random.Random` for seedable tests. System profiles are
  data files carrying the resolution kind (roll-under), degree sliders,
  difficulty ladder numbers, auto success/failure bounds, and criticals. Combat
  arithmetic is pure with seeded RNG.
- Campaign state representation: a world-state object with locations, time,
  deadlines, clocks, quests, mission, facts, commitments, characters, NPCs,
  scene id and flags, and a compact recap, rendered as a structured German
  block. NPCs present in scene are listed separately from NPCs elsewhere.
- NPC state model: per-NPC goal, agenda log, memories, attitude on a fixed
  host-like scale, faction. NPCs with a non-empty goal become agenda NPCs whose
  proposed offscreen steps are validated, clamped, and appended by code.
- NPC memory: per-NPC capped memory list of gist plus verbatim key quote,
  extracted only at scene exit and wrap-up, validated and applied by code.
  Gossip spreads deterministically to same-faction NPCs.
- Time model: one code-owned minute counter with everything else derived in
  rendering. Time advances per turn in code; the LLM marker is an accelerator.
  Real-time equals in-game time was rejected.
- Consequence clocks: Blades-style progress clocks. The model requests a tick;
  code validates the id, clamps one tick per clock per turn, and a full clock
  queues a one-shot consequence directive.
- Plot threads: a code-managed loose-threads list (Chekhov list) capped at 20
  open, extracted at wrap-up, with the top threads surfaced in the prompt.
- Private state: none at the player level. Spoiler control is structural: the
  adventure never enters the vector store, spoiler chapters are dropped at
  ingest, gated exits hide locked options, and GM-direction one-shot notes are
  queued into the next turn's user message.
- Session history model: append-only JSONL per completed turn plus typed journal
  events; rotated on session leave, restored into empty history on join, and
  fed into session-memory RAG ingest.
- Relationship graph: none. Per-NPC scalar attitude and faction with faction-
  scoped gossip propagation; no edge structure or traversal.
- Rule-source citation model: coarse block-level sourcing. Retrieved chunks are
  grouped under fixed labels (rulebook, world knowledge, session memory) and
  each chunk carries its section title as embedded context. Profile data files
  carry source annotations for verification. No runtime page/line citations.
- Useful ideas: "the LLM proposes, code validates and applies" as a universal
  seam; pure deterministic cores with injected RNG; three-tier roll request
  (inline marker fallback plus constrained-JSON router); spoiler discipline via
  data architecture; session artifact split with atomic writes and rotation;
  golden-transcript regression replay with a mocked LLM; heading-aware RAG
  chunking; per-corpus vector tables with embedding-model pinning and FTS5
  mirror; code-driven per-turn time advancement (marker as accelerator only).
- Ideas rejected: marker-only time advancement; adventure served via plain
  vector retrieval; LLM-owned clocks, flags, commitments, and attitude; LLM
  minting state objects; real-time equals in-game time; hidden/chained clocks
  in the first cut.
- License: none (all rights reserved).
- Code reused: no.

## claude-dnd-skill

Repo: https://github.com/neuralinitiative/claude-dnd-skill (cloned to `research/claude-dnd-skill`)

- Purpose: a Dungeon Master assistant for running persistent D&D 5e campaigns
  inside Claude Code as a plugin. Campaign creation, character management,
  combat tracking, NPC generation, dice rolling, session state persistence.
- Architecture: a skill holding procedural rules, templates, and Python tooling,
  layered over a per-campaign markdown data tree. Companion apps include a dice
  display server and a GM-side display.
- Persistence model: Markdown files are the source of truth under a campaign
  root, with derived JSON state (tracker, recap snapshots, graph, session
  flags). Cross-session continuity via Claude messages plus state files.
- RAG design: none. Context management is deterministic, not retrieval-based.
- Document ingestion: campaign import extracts plaintext from PDF (column-aware
  PyMuPDF with pdftotext fallback), markdown, txt, and docx, chunked at 4000
  words for the model to map onto campaign templates. SRD rules built into
  bundled JSON.
- Deterministic mechanics: explicit state-tracking scripts. Deterministic
  state-diff session recap (snapshot to snapshot, zero LLM), JSON-verified
  combat initiative and tracking, codified XP tables. A documented continuity
  problem after context compaction is solved with a typed relationship graph,
  not prompts.
- Campaign state representation: precise markdown template schemas: state (current
  situation, pinned facts, world state, faction moves, live state and combat,
  session flags, DM notes), world, NPCs (index plus full), session log, arc,
  character sheets.
- NPC state model: dual-file. An index table of NPCs plus a full entry file with
  role, HP/AC/attack, demeanor, motivation, secret, speech quirk, attitude
  scale, faction, current goal, schedule, personality pairs, and at least two
  relationships. A read-before-act rule requires reading the full entry before
  writing dialogue or decisions.
- Visibility/private-information handling: content-level conventions. DM Notes
  sections hidden from players, secret fields on NPC/faction/place templates,
  hidden rolls through silent dice, server-side rolls that never surface on
  player devices. No role-based read enforcement beyond these conventions.
- Session history model: appended, non-destructive session-log per session
  section (recap, key events, decisions, XP, loot, open threads, DM
  calibration), archived after a threshold, plus deterministic state-diff recap
  and a campaign graph sweeps for relationship shifts.
- Relationship graph: yes. A typed-edge graph (JSON) across NPC, faction, place,
  item, thread nodes, maintained by extraction from session logs with source
  anchors. Partially deterministic pattern extraction with an LLM fallback.
- Rule-source citation model: bundled SRD JSON queried by a lookup tool with
  fuzzy match per-campaign ruleset resolution; SRD provenance tracked at the
  dataset level with CC-BY-4.0/Fair Use sourcing.
- Useful ideas: dual-level NPC storage (index plus full entry) with a
  read-before-act rule; deterministic state-diff as an LLM impartiality guard;
  typed-edge relationship graph as an index over canon; lazily loaded corpus
  pointers; JSON-in/out combat serialization for a clean model-tool contract;
  DM Notes and Secret fields as first-class schema; phased graph extraction
  (deterministic verb table first, LLM fallback only on unmatched).
- Ideas rejected: the LAN-only no-auth dice server (fine for a home table, not a
  network deployment); LLM-based graph extraction in phase one; committed YAML
  frontmatter arcs where tree structure is not needed.
- License: AGPL-3.0 (neuralinitiative).
- Code reused: no. AGPL network clause prevents reuse in this project.

## open-tabletop-gm

Repo: https://github.com/Bobby-Gray/open-tabletop-gm (cloned to `research/open-tabletop-gm`)

- Purpose: an LLM-agnostic, system-agnostic tabletop GM framework. The LLM never
  does arithmetic, lookups, or state bookkeeping; it narrates, role-plays, and
  writes structured output that Python scripts verify and persist.
- Architecture: two layers. A system-agnostic core (markdown templates plus
  Python scripts, identical across game systems) and per-system modules
  supplying rules text, display manifests, and system-specific tools. A system
  porting template documents the contract for adding new systems. The working
  non-D&D system is BRP/Chaosium percentile roll-under.
- Persistence model: plain markdown under a configurable campaign root. Campaign
  files include state, world, NPCs index and full, session log and archive,
  optional world seeds and graph JSON. A global character roster and a
  cross-campaign name registry. Deterministic session save recomposes templates
  and rewrites live state flags.
- RAG design: deliberately not vector RAG. Context assembly uses named
  deterministic queries: scene subgraph extraction from the relationship graph,
  pinned facts, and keyword search across campaign files with file and section
  attribution for continuity.
- Document ingestion: import extracts text only from PDF, markdown, DOCX, TXT,
  chunked at ~4000 words; the human reviews structure and writes files from
  import branches. Not a full embedding pipeline.
- Deterministic mechanics: all in scripts, zero LLM involvement. XdY dice parser
  with advantage/disadvantage and keep/drop, wrapped in a server-side roll that
  broadcasts non-spoofable results to player devices; condition and timed-effect
  tracker; initiative and combat log (GM-rolled initiative only); in-world
  calendar arithmetic. A roll-mode switch selects between waiting for physical
  dice and auto-rolling openly.
- Campaign state representation: state template sections including current
  situation, pinned facts (a deliberately small stable canon set, never
  auto-rewritten), world state, active quests, open threads and rumours, faction
  moves, recent events, active combat, and a live state flags section rewritten
  at every save as a context-compaction survival anchor.
- NPC state model: index table (name, role, faction, location, attitude, notes)
  plus full entries with demeanor, motivation, secret, speech quirk, attitude
  scale, faction, current goal, schedule, personality axes, and relationships.
- Visibility/private-information handling: convention-based. NPC secret fields,
  a GM notes section, hidden-from-players world truths, hidden dice, GM-side
  display help never fed to players, token-gated transport. No per-player
  line-of-sight permission model.
- Session history model: appended session log, archived non-destructively,
  with continuity bullets folded into state. A stale-pointer sanity check on
  load surfaces outdated arc pointers.
- Relationship graph: yes. Typed, time-stamped edges in JSON across NPC, faction,
  place, item, thread, and party nodes, with edge vocab covering loyalty,
  opposition, membership, control, knowledge, friendship, obligations, blood,
  thread advancement. Edges carry since/until session and can be closed
  (canonical end) or superseded (retcon). A deterministic pattern extractor
  proposes edges with verbatim anchors; extraction is tested.
- Rule-source citation model: dataset-level provenance for bundled SRD JSON
  (source repo, branch, SHA, fetched date) and edge-level verbatim anchors in
  the graph. SRD records themselves lack per-record citations.
- Useful ideas: two-layer system abstraction with a documented porting contract;
  state template with pinned facts and live state flags as a compaction anchor;
  typed and time-stamped relationship graph with close-versus-supersede
  semantics; deterministic source-anchored extraction with human approval;
  roll-mode split between physical and automatic dice; name registry with safe
  rename across all coupling surfaces.
- Ideas rejected: vector RAG over campaign state (markdown plus scene subgraph
  wins for this domain); per-system front-end styling (single manifest-driven
  renderer instead); server-hosted auto-push display; LLM-dependent graph
  extraction.
- License: AGPL-3.0-or-later.
- Code reused: no.

## MythWeaver

Repo: https://github.com/xavibonell/mythweaver (cloned to `research/mythweaver`)

- Purpose: self-hosted, LLM-driven Dungeon Master for tabletop RPGs. A
  deterministic engine owns every number; the LLM narrates and calls engine
  tools, never invents mechanics. LLM-directed dynamic scene UI rendered from
  structured scene deltas.
- Architecture: TypeScript monorepo with engine, llm, rag, scene, shared
  packages plus a server and web app. A typed tool seam is the only mutation
  channel from the LLM to game state. The interaction resolver explicitly
  declares the engine, not the narrator, decides who perceives what.
- Persistence model: PostgreSQL. Sessions hold a JSONB game-state blob; the
  conversation transcript lives in a separate messages table, not inside the
  state blob. A rag_chunks table holds embeddings.
- RAG design: a fallback ladder, not a fused hybrid: in-memory vector index
  first, pgvector second, offline in-memory BM25 third, none last, each gated
  by env flags and degrading gracefully. Retrieved chunks carry a prose source
  for citation. Vector dimension is a hard invariant across providers. The lore
  namespace was designed, then falsified with a knowledge probe (the base model
  already knew the canon) and superseded by a cheap variety nudge.
- Document ingestion: PyMuPDF corpus extraction to chunked JSONL, then embed.
  Rules live in the SRD namespace for lookup; exemplar transcripts feed voice.
  Restricted to licensed material.
- Deterministic mechanics boundary: the engine owns every number. The LLM
  affects state only by calling engine tools (roll, move, interact, attack
  request, rule lookup, state get). Unbuilt mechanics throw a not-implemented
  phase error; the game cannot silently invent them. Physical dice are validated
  against engine rolls via a suspend loop: an ongoing turn persists until a
  declared roll matches.
- Campaign state representation: a full read-model GameState (characters, NPCs,
  combat, flags, facts ledger, pending turn, journal) snapshotted into the
  session row each turn, with cheap restart paths from pregen bundles and frozen
  dev-session states.
- NPC state model: two-tier persona (stateless derived profile plus optional
  authored override). Standing is not stored on the NPC; it lives as a mutating
  fact in an append-only ledger. A profiler composes a party-perceived dossier
  only from words spoken aloud, so it cannot leak internals.
- Visibility/private-information handling: the strongest design here. A
  default-closed visibility law: a datum enters the player world-model only
  when a deterministic event surfaces it at the table, and player-safe text is
  composed at write time, not redacted at read time. A whitelist projection
  builds player-facing state field by field; new fields are player-invisible by
  default; secret keys never reach players; unidentified items are masked;
  DCs are stripped. Journal events are player-safe at write time.
- Session history model: a canonical transcript table; composers receive only a
  recent window of transcript lines for planning; an engine-owned journal
  records what the table witnessed rather than what individual pieces know.
- Relationship graph: none as a graph structure. Social state is flattened into
  append-only facts; a scene graph is geometrical (zones, earshot, line-of-
  sight), not social.
- Rule-source citation model: lookup over the SRD returns chunks carrying a
  provenance field such as "Player's Handbook p.123"; short rule text is
  license-appropriate SRD material.
- Useful ideas: engine-owns-numbers with an explicit not-implemented phase for
  unbuilt mechanics; whitelist player projection with write-time player-safe
  composition (leakage-proof by construction); append-only facts ledger for
  evolving standing; RAG degrade ladder with a hard dimension invariant;
  provenance-carrying retrieved chunks; falsify-cheapest-first culture before
  building corpora; zero-cost replay and restart paths.
- Ideas rejected: a fully-fledged social relationship graph (falsified for
  well-known settings; a scalar standing plus facts ledger suffices); fused
  hybrid retrieval (a cascade is simpler and caches better).
- License: none (all rights reserved).
- Code reused: no.

## dnd_experience

Repo: https://github.com/jeffbulltech/dnd_experience (cloned to `research/dnd_experience`)

- Purpose: a single-player, AI-driven D&D 5e game master with FastAPI backend
  and React frontend using local LLMs (Ollama) for storytelling while enforcing
  rules and preserving a GM-screen concept.
- Architecture: conventional layered monolith: API routes delegate to services;
  models are SQLAlchemy ORM; schemas are Pydantic; pure utilities (dice) are
  isolated; admin ingestion lives in a CLI package. No over-engineering.
- Persistence model: relational. One campaign row owns child tables cascading on
  delete: game state (one-to-one), chat history, encounter, dice roll records,
  campaign attachments. Generic metadata/extra JSON columns on nearly every
  entity allow growth without column churn.
- Attachment ingestion: files stored on disk under a per-campaign directory with
  a UUID stored filename and the original name kept separately; metadata in DB.
  Attachments are not ingested into RAG; they are stored files plus metadata.
- RAG design: local Chroma vector store with Ollama embeddings over the SRD,
  chunked 800/overlap 120. Per-message retrieval returns citation objects with
  excerpt, source, and score; injected into the system prompt. One shared
  corpus today; a database filter hook is reserved but unused.
- Deterministic mechanics: pure, testable functions separated from the LLM.
  Regex-parsed dice expressions with keep-highest/lowest and modifiers,
  advantage/disadvantage, critical detection; ability-modifier math; validated
  character builder (standard array, point buy, species, proficiency). Dice and
  validation results are authoritative. Combat is logged but deliberately not
  resolved; resolution is a stated future iteration.
- Campaign state representation: one game-state row per campaign with location,
  active quests, a summary, and a generic metadata bucket, formatted into the
  prompt. Owner-only access.
- NPC state model: none. NPCs exist only as encounter participants during combat
  and as live prompt description. A structured memory system is an acknowledged
  roadmap gap.
- Visibility/private-information handling: prompt-level. A long system-prompt
  GM screen instructs the model never to reveal stats, secrets, maps, or hidden
  rolls, plus coarse owner-checked access control on campaign endpoints. No
  field-level public/private split; the prompt is the boundary.
- Session history model: append-only chat-history table with role, content,
  stored RAG context, and metadata. Two retrieval modes: newest-first internal
  fetch for prompt building and cursor-paginated fetch for the UI. No SQL
  session grouping; history is a flat per-campaign stream.
- Relationship graph: none.
- Rule-source citation model: RAG citations carry an excerpt, a source
  identifier, and a similarity score, injected into the prompt. No page-level
  citations.
- Useful ideas: layered service separation; generic metadata/extra JSON columns;
  pure deterministic engines separated from the LLM; compact regex dice
  expression with keep/drop; prompt-only GM screen as the cheapest visibility
  starting point; cursor pagination by id; attachments as disk plus DB metadata
  with UUID names and synced cleanup; retrieval caching with graceful fallback.
- Ideas rejected: prompt-only GM screen as the long-term visibility model (leaky
  for shared play; a visibility engine is preferred); storing full RAG citation
  context on every message; combat logging without resolution; a global shared
  RAG corpus across campaigns; owner-only access baked in as the only model.
- License: none (all rights reserved).
- Code reused: no.

## Cross-cutting conclusions adopted into this project

Adopted:

1. Applies-only-at-the-boundary execution: the engine/runtime owns mechanics;
   the LLM proposes intent and narration. Adopted from dungeonmaster and
   MythWeaver.
2. Event-sourced history with a typed event log, evidence-backed facts, and
   idempotency. Adopted from AiChatTrpg's durable memory design.
3. Visibility as a first-class property with default-closed facts and write-time
   player-safe composition. Adopted from MythWeaver and AiChatTrpg.
4. Deterministic dice engine with seeded RNG and no system semantics. Adopted
   from dungeonmaster (injected RNG) and dnd_experience (compact regex).
5. Typed, time-stamped relationship edges over SQLite with close-versus-supersede
   semantics. Adopted from open-tabletop-gm and claude-dnd-skill.
6. Heading-aware document chunking preserving section titles, raw sources kept
   separate from processed representations. Adopted from dungeonmaster.
7. Retrieval as a fallback ladder plus metadata filtering rather than one opaque
   fused index. Adopted from MythWeaver's degrade ladder.
8. Campaign ruling search before generic sourcebook retrieval. Adopted from
   dungeonmaster's per-campaign profile and ruling patterns.
9. Human-readable markdown projections that are derived artifacts, not the
   authority. Adopted from claude-dnd-skill and open-tabletop-gm, with the
   authority inverted to SQLite plus events.
10. Capability-based, discoverable game-system plugins with a documented porting
    contract. Adopted from open-tabletop-gm's two-layer abstraction.
11. Session artifact split: read-only source sheets, code-owned mutable state,
    append-only journal. Adopted from dungeonmaster.
12. Seeded dice, persistent audit of every deterministic resolution (check log).
    Adopted from AiChatTrpg.

Rejected:

1. Vector memory as the source of campaign truth. All systems that climbed past
   a toy state lost facts; truth must live in structured state plus events.
2. LLM-owned clocks, flags, attitudes, or agenda advancement. Code validates
   and applies; the LLM proposes. From dungeonmaster.
3. A single global RAG corpus for all campaigns and rules. Namespacing and
   precedence are required. From dnd_experience and AiChatTrpg.
4. Fully-fledged graph databases for relationships. A lightweight SQLite edge
   table suffices. From MythWeaver's falsification.
5. Prompt-only GM secrecy as the visibility mechanism. Leaky for anything beyond
   single-player. From dnd_experience.
6. Adventure content served via indiscriminate vector retrieval. Spoiler
   discipline must be structural. From dungeonmaster and open-tabletop-gm.

## Research workspace hygiene

The `research/` directory exists only for architectural study. It is git-ignored
(`/research/`). No code, data, prompts, or templates were copied from any
research repository. All game-system, content-pack, and runtime implementations
in this project are clean-room.