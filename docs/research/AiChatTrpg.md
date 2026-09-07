# AiChatTrpg — Research Report

- Repository: https://github.com/Leehow/AiChatTrpg (cloned to `research/AiChatTrpg`)
- License: Apache-2.0
- Reuse verdict: no code reused; clean-room

## Purpose

Self-hostable AI Game Master for tabletop RPGs. The player brings their own LLM provider (OpenAI-compatible, Gemini, Claude), points the system at a ruleset PDF and an adventure module, and plays single-player (`README.md`, "What it is"). Pre-alpha, local-first by design; the hosted test deployment is explicitly experimental. Backend is Python 3.11+ / FastAPI / SQLAlchemy / PostgreSQL (`backend/main.py`, `backend/requirements.txt`); frontend is Vite + React 19 + TypeScript (`frontend/`). "pgvector not required" per the README tech-stack note — there is no vector database anywhere.

## Architecture

Two-layer design: a portable engine core plus app-specific glue.

- The portable core lives under `backend/agents/trpg/framework/` and imports nothing FastAPI/SQLAlchemy/project-specific (`framework/models.py` docstring). Its I/O contract is pure dataclasses: `Message`, `PromptMessage`, `LayeredPrompt`, `SessionState`, `TurnInputs`, `TurnResult`, `SessionStateDiff`. Side effects happen only at the boundary.
- Adapter seams are `@runtime_checkable` Protocol classes in `framework/adapters/protocols.py`: `LlmAdapter` (streaming chat with an optional cache-aware `LayeredPrompt` path), `RulesetAdapter` (slices ruleset data into `StableText`), `RetrievalAdapter`, `MultimediaAdapter` (TTS/portraits/scene art), and `Clock`. NoOp implementations ship in `framework/adapters/stubs.py`; concrete wiring lives outside the core (`services/trpg_framework_adapter*.py`).
- The single entrypoint is `run_trpg_turn` in `framework/pipeline.py`: an async generator yielding a typed `TurnEvent` stream (`framework/events.py` — metadata, thinking lines, BP snapshot, content deltas, marker events, side effects, and a final `DoneEvent` carrying the `TurnResult`). One turn runs three phases: `select_mode`, then preprocess → generate → postprocess (`framework/phases/`).
- Modes are a pure function of session state plus hint: GUIDE (character setup), IC (in-character play), BREAK (between adventures) (`framework/modes.py`); once `game_started` flips true it never un-flips.
- App glue lives in `backend/services/` (large flat module set) and `backend/routes/` (SSE chat streaming, ruleset/module upload, sessions). The framework never opens a transaction; it returns a `SessionStateDiff` (new messages, memory updates, consumed dice, scene transitions, state transitions) that the caller persists (`framework/models.py` docstring).
- Prompt assembly is stability-graded for provider prompt caching: `LayeredPrompt` splits into stable prefix / semi-stable context / variable suffix (BP1/BP2/BP3), with per-provider projection delegated to the LLM adapter (`framework/models.py`, `framework/bp/snapshot.py`).

## Persistence

PostgreSQL via SQLAlchemy + Alembic (`backend/alembic/`).

- One session row is the campaign root (`backend/orm/models/game_session.py`, table `sessions`): JSON columns `character` (PC card shaped by the ruleset's sheet template), `module_state` (scene navigation), `memory_state` (structured buckets), `setup_state` (resumable setup wizard), `dice_history` (recent rolls), plus `narrative_summary` text and `last_summarized_msg_count`. `ruleset_id` is nullable so a draft session can exist before a ruleset is chosen. The schema is multi-user from day one (`user_id` FK) even though the product is single-player today.
- Durable memory rows in `backend/orm/models/trpg_memory.py`: `trpg_memory_items` carries kind, text, visibility, owner, subject ids, source turn/event ids, source quotes, confidence/salience, truth status, lifecycle status, supersede links, and a unique-per-session `idempotency_key` (unique index `ux_trpg_memory_items_session_idempotency`). Companion tables: `trpg_memory_revisions` (before/after audit per memory), `trpg_state_changes` (typed, unique `change_id`, path/value/previous-value JSON — the event-sourced state journal), `trpg_world_state_snapshots` (versioned snapshots), and `trpg_turn_traces` (per-turn audit: player input, context cache key, visibility signature, retrieved block ids, memory proposal/commit counts).
- The module docstring on `trpg_memory.py` states the intent plainly: the JSON `sessions.memory_state` remains a compatibility projection while the rows carry source, visibility, lifecycle, and idempotency metadata.
- Every deterministic check is audited in `check_logs` (`backend/orm/models/check_log.py`): actor, `procedure_id`, engine, skill, seed, source, plus full request/result/roll-trace/warning JSON payloads. `sessions.dice_history` is described as "the compact compatibility mirror".
- Chat messages live in their own `messages` table with FK back to the session (`backend/orm/models/message.py`, role = user/gm/system/dice), so history can be streamed and paginated independently of the session row.

## RAG & Retrieval

Dual memory plus a deliberately modest retrieval story.

- Structured memory: `memory_state` JSON buckets — scenes, npcs, plot_threads, world_facts (`game_session.py` `_empty_memory_state`) — fed by an auto-memory extractor (`services/trpg_auto_memory.py`). Narrative memory: an LLM-compressed rolling summary of aged-out messages (`services/trpg_narrative_compression.py`).
- Durable memory is event-sourced with type normalization and provenance: proposals become `MemoryItem`s with source turn/event ids and quotes, gated by a commit validator (`services/trpg_memory/memory_commit_gate.py`, `proposal_validator.py`, `evidence.py`), and mirrored down into the legacy buckets by `trpg_memory/legacy_projection.py`.
- Working retrieval is lexical: keyword scoring over the parsed ruleset's `rule_packages` and `parameter_families` (`services/trpg_ruleset_search.py` — keyword split/token overlap, capped excerpts, max 4 hits). On top of that sits a ReAct grep-retrieval agent with `grep_search` / `read_source` tools (`backend/agents/grep_retrieval/agent.py`, `core.py`), wired to OpenAI-compatible runtimes; the IC runner routes `[SEARCH_RULES]` through it to bridge bilingual term mismatches (`services/trpg_ic_runner.py`, Wave 3 comment).
- The vector RAG layer is an admitted in-memory placeholder: `services/trpg_rag/rules_rag.py` ("minimal rules RAG facade ... this in-memory implementation is a placeholder for vector/full-text search"), plus `scenario_rag.py`, `namespace_models.py`, and `rag_visibility_filter.py`. Chunks are scored by token-set intersection.

## Document Ingestion

Two separate ingestion pipelines.

- Ruleset PDF/Markdown/TXT (`services/ruleset_file_ingestion.py`, `services/ruleset_parse_workflow.py`): normalized markdown feeds a staged LLM pipeline (`backend/agents/trpg/rule_parser_v6/pipeline.py`) producing a structured v6 ruleset JSON — discovery, core, companion, rule packages, parameter families, character sheet, and a final manifest plus a compiled dice-rule IR (stage 8). The orchestrator runs eight stages in "full" mode, supports targeted single-stage re-runs, emits SSE progress with heartbeats, and calls a `save_checkpoint` callback after every sub-step so a mid-run crash keeps earlier work (`rule_parser_v6/pipeline.py` docstring). OCR for scanned PDFs is pluggable under `backend/services/ocr/`: MinerU local and cloud backends, a plain pdftext backend, and a result parser that strips images while keeping formulas and tables (`services/ocr/mineru_local.py`, `mineru_cloud.py`, `result_parser.py`; provider selection in `services/ocr_config.py`). Draft/compile flows with undo live in `services/ruleset_drafts.py`, `ruleset_draft_compile.py`, `ruleset_draft_undo.py`.
- Adventure modules (`services/module_parser.py`): routes uploaded PDF/DOCX/PPTX/XLSX/image to the OCR backend and markdown/txt straight through, returning a `ModuleParseResult`. Semantic structuring (`services/module_semantic.py` + `module_semantic_*` siblings) builds an outline, normalizes document structure, extracts NPC cards (`services/module_npc_cards.py`), derives global refs and playable units (`module_semantic_shape.py`), and produces a briefing plus appendix index. The module_parser docstring notes the open-source layer stops at file → markdown; deeper module intelligence is the upstream product's.

## Deterministic Mechanics

Dice and checks are code, not LLM judgment.

- Seeded, replayable dice: `backend/agents/trpg/dice_roller.py` parses expressions like `2d6kh1` (keep-highest/keep-lowest), seeds a `random.Random` from a string seed (unseeded falls back to system entropy), and returns per-die rolls with kept/dropped flags, notes, and the seed in the metadata for replay.
- Check engines are pure functions `(CheckArgs, spec) → CheckResult` dispatched through a resolver registry: `check_engines.py` implements `coc_7e_d100` (regular/hard/extreme/critical/fumble tiers, bonus/penalty mis-usage flagged in the audit), `brp_d100` (no hard/extreme tiers), `d20_vs_dc`, `pbta_2d6`, and `pool_count_n`; registration in `check_engine_registry.py`, dispatch in `check_resolver.py`. An unresolved spec falls back to `custom_llm` — the LLM is the last resort, not the default.
- A versioned dice-rule IR interpreter (`backend/agents/trpg/check_ir/`) executes `resolution_ir_v1_1` JSON compiled from rulesets: `core.py` (facade and `run_resolution_ir`), `core_types.py` (`Die` value object with kept / exploded-from / locked metadata; validation/execution/manual-resolution exceptions), `core_steps.py`, `core_axes.py`. It returns a stable serializable result — status, primary tier, pass flag, full multi-axis map, and a trace. Built-in precompiled specs (e.g. the CoC 7e sanity roll in `check_ir/packs/coc7.py`) serve as runtime fallbacks when an uploaded ruleset lacks its own compiled procedure.
- A consequences engine converts result axes into deterministic effects: resource deltas, threshold flags, mandatory follow-up checks, and prompt-visible narration events (`services/trpg_rule_consequences.py` with resource / catalog / CoC-sanity helper modules); it is wired directly into check-log persistence (`services/trpg_check_logs.py`).
- Player-visible checks use a pending-placeholder lifecycle: the GM emits a `[pending_check ...]` marker, the frontend shows a roll button, and on click the runtime resolves, replaces the placeholder in the source GM message with a rendered `[dice]` block, and spawns an IC continuation turn (`services/trpg_pending_checks.py` module docstring).

## Campaign State

The single `sessions` row is the campaign root: PC card, module/scene state, memory buckets, setup wizard state, dice history, and the rolling narrative summary (see Persistence). In-play snapshots flow through the framework as `SessionState` dataclasses; the framework computes a `SessionStateDiff` the caller persists. A parallel "authoritative" runtime (`services/trpg_runtime/`) maintains event-sourced state changes and world state snapshots keyed by turn id, with a per-turn output contract (`turn_output_contract.py`) and trace (`turn_trace.py`).

## NPC Model

Split into a static profile and a per-scene mutable state (`backend/services/trpg_npc/models.py`):

- `NPCProfile` (static): tier, narrative role, public card and private card, a 12-axis `PersonaVector` (Big-Five plus dominance/empathy/paranoia/impulse-control/ambition/fanaticism/deception), baseline mood, voice card, mode list, `PlotConstraints`, mechanics blob, `knowledge_fact_ids`, and `secret_ids` — the last two tie the NPC into the memory layer's visibility model.
- `NPCState` (mutable, per scene): scene/location, status, current mode, a `MoodVector` (valence/arousal/dominance plus ten concrete emotions), `goal_pressure` dict, `physical_state`, active flags, and a `state_version` counter. `HotStatePatch` carries temporary per-turn mood and goal-pressure deltas.
- `RelationshipVector` is a fixed seven-subject set (trust, respect, fear, suspicion, resentment, debt, affinity) — relationships are vectors over a fixed subject set, not graph edges.
- NPC agents propose, code disposes: `NPCIntentCard` / `ActionProposal` dataclasses feed an orchestrator whose background ActionBoard plans routine behavior while thin foreground arbitration resolves proposed actions each turn (`services/trpg_npc/orchestrator.py`, `action_resolver.py`, `npc_planner.py`, `behavior_policy.py`, `plot_integrator.py`); the scene runtime integrates the results (`services/trpg_scene_runtime.py`). A best-effort post-visible worker runs routine planning and state persistence after the GM reply is saved (`services/trpg_npc/post_visible_worker.py`).

## Visibility & Private Information

Visibility is a first-class enum on memories, state changes, and context blocks: `Visibility` in `backend/services/trpg_context/models.py` with exactly `public`, `party_known`, `gm_only`, `npc_private`, `system_internal`.

- `services/trpg_context/visibility.py` implements `can_read` per actor profile (player, NPC, memory extractor, rule adjudicator) and a `visibility_signature(actor)` that feeds the prompt-cache key — the turn trace persists `visibility_signature` and `context_cache_key` (`orm/models/trpg_memory.py`, `TRPGTurnTrace`; also `trpg_context/context_builder.py`, `prompt_compiler.py`).
- Retrieval chunks are filtered through the same predicate before entering a prompt (`services/trpg_rag/rag_visibility_filter.py`), and the scenario namespace blocks player search entirely by default (`trpg_rag/scenario_rag.py`, `allow_player_scenario_search=False`).
- Per-NPC name reveal gates: `mask_hidden_true_name` in `services/trpg_npc/visibility.py` overwrites the NPC `name` field with `player_known_name` until `name_revealed` flips — an explicit data invariant replacing a prose rule, because the comment records that LLMs reliably leaked true names within a few turns when both fields were present. `[REVEAL_NAME]` is a first-class marker kind (`framework/markers/parse.py`).
- Generated NPC/GM text is checked for secret leakage before display: `LeakChecker` in `services/trpg_npc/leak_checker.py` compares output against forbidden memories (exact-substring and token-overlap thresholds, CJK-aware tokenization) and emits per-memory warnings.

## Session History

One unified message timeline. `Message` rows carry role user/gm/system/dice plus a phase label (guide/ic/break) that is only a render/filter hint — the framework treats `chat_history` as a single timeline regardless of phase (`framework/models.py`, `Message`). Each turn sends only the most recent N messages; older ones are folded into `session.narrative_summary` by an LLM compression pass that fires only above a threshold and otherwise skips (`services/trpg_narrative_compression.py` — trigger, dropped-window, and fallback-concat semantics are spelled out in its docstring). Resolved checks persist a dedicated `role="dice"` message row containing the rendered block and full metadata, with the check-log row FK-linked to it (`services/trpg_pending_checks.py`, `record_check_execution(..., message_id=dice_msg.id)`); rule-search excerpts persist as `role="system"` rows so the next turn's GM sees them verbatim (`services/trpg_ic_runner.py`). `[DISCREPANCY: prior-art.md says "Check results persist as role=system messages"; in the clone, check results persist as role="dice" rows (services/trpg_pending_checks.py) while role="system" is used for rule-search excerpts (services/trpg_ic_runner.py). The spirit of the claim — verbatim, attributable non-GM rows — holds; the role labels are swapped.]`

## Relationship Graph

Not present in this codebase as a graph. There is no relationship/edge table anywhere in `backend/orm/models/`. Relationships exist in two weaker forms: a `relationship` memory kind in the durable memory layer, and the per-NPC `RelationshipVector` over the fixed seven-subject set (`trpg_npc/models.py`), maintained per target in `PostTurnResult.updated_relationships` (`trpg_npc/orchestrator.py`). The closest mechanism to a graph is therefore memory-row provenance plus NPC-held vectors, which cannot answer multi-hop relationship questions.

## Rule-Source Citation

Search hits carry their provenance: `SearchHit` in `services/trpg_ruleset_search.py` has source ("package" or "family"), the package/family id, a human-readable name, and the snippet, rendered with source headings into the transcript; the result is injected as its own chat row so the GM can cite or apply it next turn (`trpg_ic_runner.py`). Excerpts are hard-capped (per-section and per-entry character limits, max 4 hits) rather than dumping package bodies. Check logs record the resolved `procedure_id` (`orm/models/check_log.py`), so every mechanical outcome points back to the procedure that produced it. No page-level citations exist anywhere — once OCR flattens a PDF to markdown, page numbers are gone.

## Useful Ideas

- The portable adapter-protocol engine core: pure-dataclass I/O, five runtime-checkable Protocols, one streaming `run_trpg_turn` entrypoint, and a returned diff the caller persists. The framework/testability seam is the single most transferable idea here.
- The versioned deterministic dice IR (`resolution_ir_v1_1`) with per-die trace, multi-axis results, and a manual-resolution escape status; plus seeded replayable rolls and a persistent `check_logs` audit keyed by procedure id.
- Event-sourced durable memory with provenance (turn/event ids, quotes), idempotency keys, supersede lifecycle, and revision history — memory that can be audited and corrected, with legacy buckets demoted to projections.
- Marker-as-escape-hatch: every special directive is a parsed text marker (`[TAG key=val]`), never provider function calling, keeping the streaming path uniform across backends (`framework/markers/`; rationale spelled out in the `trpg_ruleset_search.py` docstring).
- Visibility-first context construction: one enum, one `can_read` predicate, a visibility signature in the cache key, structural name masking instead of prompt rules, and post-generation leak checking.
- Dual memory: cheap structured buckets plus an LLM-compressed narrative summary, each with a defined trigger and owner module.
- Prompt-cache-aware layered prompts (stable/semi-stable/variable) with per-provider projection left to the adapter.

## Ideas Rejected

- The in-memory lexical RAG placeholder (`trpg_rag/rules_rag.py` token-set intersection over a handful of chunks) — not a retrieval strategy, and the code says so itself.
- JSON-bucket `memory_state` as the authority — the repo's own docstrings demote it to a compatibility projection; structured rows plus the event journal are the truth.
- The interactive grep ReAct agent as a per-turn latency path — an agentic tool loop with multiple LLM round-trips inside a turn stream is the wrong default; use a prebuilt index with the agent only as a fallback.
- Fire-and-forget background NPC workers with the polling dance (`post_visible_worker.py` inflight-task set, background planner tasks) — hard to test, easy to drop work silently.
- Per-turn synchronous memory extraction inside the turn stream — the compression docstring already shows the strain (threshold gating, fallback-concat on LLM failure); extraction belongs outside the critical path.
- Fully-fledged vector infrastructure — deliberately absent ("pgvector not required"), which for single-player lexical retrieval is the right call and validates not needing a vector DB until scale demands one.

## License & Reuse

Apache-2.0 (`LICENSE`, plus a `NOTICE` file). Per the shared research-hygiene conclusion in `docs/research/prior-art.md`: no code, prompts, templates, or data were copied; this project's implementations are clean-room rederivations of architectural ideas only.

## Key Files

| Path (relative to repo root) | Role |
| --- | --- |
| `backend/agents/trpg/framework/models.py` | Pure-dataclass I/O contract: SessionState, TurnInputs, TurnResult, SessionStateDiff, LayeredPrompt |
| `backend/agents/trpg/framework/adapters/protocols.py` | The five `@runtime_checkable` adapter Protocols (LLM, ruleset, retrieval, multimedia, clock) |
| `backend/agents/trpg/framework/pipeline.py` | `run_trpg_turn` — the single streaming turn entrypoint |
| `backend/agents/trpg/framework/phases/` | Preprocess / generate / postprocess phase implementations |
| `backend/agents/trpg/framework/markers/` | Marker parse (`parse.py`) and apply-to-diff (`apply.py`) |
| `backend/agents/trpg/dice_roller.py` | Seeded dice expression roller with keep-highest/lowest and replay metadata |
| `backend/agents/trpg/check_engines.py` | Pure check engines: CoC d100, BRP, d20-vs-DC, PBTA, pool-count |
| `backend/agents/trpg/check_ir/core.py` | Versioned `resolution_ir_v1_1` interpreter returning status/axes/trace |
| `backend/orm/models/game_session.py` | `sessions` row: JSON state columns, narrative summary, dice history |
| `backend/orm/models/trpg_memory.py` | Durable memory, revisions, state changes, world snapshots, turn traces |
| `backend/orm/models/check_log.py` | Full per-check audit rows (procedure id, seed, request/result/trace JSON) |
| `backend/services/trpg_context/models.py` | `Visibility` enum and context-block/cache-zone model |
| `backend/services/ruleset_parse_workflow.py` | Markdown → staged v6 ruleset JSON parse driver |
| `backend/agents/trpg/rule_parser_v6/pipeline.py` | Eight-stage parser orchestrator with per-sub-step checkpointing |
| `backend/services/module_parser.py` | Module file → markdown via OCR backends (`services/ocr/`) |
