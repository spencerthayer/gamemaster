# dungeonmaster (Cogitator / DMbot) — Research Report

- Repository: https://github.com/Pr0degie/dungeonmaster
- Cloned at: `research/dungeonmaster`
- License: none (all rights reserved)
- Reuse verdict: no code reused; clean-room

## Purpose

A local, voice-only AI game master played over a Discord voice channel, running
fully offline: Ollama for the LLM, faster-whisper for STT, Piper/XTTS for TTS
(`dmbot/stt/transcriber.py`, `dmbot/tts/piper.py`, `dmbot/tts/xtts.py`). The
ruleset and adventure arrive as legally owned PDFs converted offline to
markdown (`tools/pdf_to_md.py`); the DM learns the setting via RAG and the
mechanics via a declarative per-system profile. Deliberately system-agnostic:
the shipped profile is Imperium Maledictum, but nothing in
`dmbot/rules/engine.py` is hardcoded to it. The play language is German
(prompts and player-visible strings are German; logs are English).

## Architecture

Layered Discord voice pipeline plus a strict turn pipeline on top:

- **Receive**: `dmbot/voice/recv.py` wraps the foreign `discord-ext-voice-recv`
  library, undoes Discord's DAVE/E2EE layer itself, decodes Opus to PCM, and
  drops audio from the speaking bot (feedback protection). It also resamples
  48 kHz stereo to 16 kHz mono.
- **VAD**: `dmbot/voice/vad.py` runs silero-vad (ONNX, no torch) in fixed
  512-sample hops to segment the stream into per-user utterances.
- **STT**: `dmbot/stt/transcriber.py` runs faster-whisper on a dedicated worker
  thread (never the audio thread) with CPU int8 fallback.
- **Turn**: `dmbot/orchestrator.py` (`DMBrain`) runs classification side-calls,
  the narration LLM, and marker parsing; `dmbot/llm/stream_assembler.py` handles
  streaming generation.
- **Delivery**: `dmbot/voice/delivery.py` (`DeliveryPipeline`) turns the answer
  into TTS audio over a speak bridge, posted text, dice/scene/clock Discord UI
  panels, and per-turn latency lines. The runtime glue lives in
  `dmbot/runtime.py` and `dmbot/voice/dmcog.py`.
- Command sub-cogs (`dmbot/voice/dicecog.py`, `dmcog.py`, `lorecog.py`,
  `scenecog.py`, `timecog.py`, `voicecog.py`) handle player slash/bang commands.
- Prompt assembly has a single owner: `dmbot/llm/prompt_assembly.py` is a pure
  function that joins slices in fixed order `persona → recap → adventure →
  state_summary → npc_memory → rag → alias_hint`; callers pass resolved strings.

## Persistence

Per-voice-channel session directory (`data/sessions/<channel_id>/`) with a
strict three-file split (ADR 015; docstring in `dmbot/memory/history.py`):

1. **Read-only character sheet**: `characters.json`, loaded but never rewritten
   by code.
2. **Code-owned mutable state**: `state.json` (serialized from
   `WorldState` in `dmbot/memory/state.py`). `WorldState.save` writes to a
   temp file and `os.replace`s it — atomic on Windows and POSIX — so an HP
   change always survives a crash.
3. **Append-only journal**: `history.jsonl` (`dmbot/memory/history.py`), one
   JSON line per completed turn (`{ts, user_msg, answer, redo}`) plus typed
   events (`{"kind": "session"}`, `{"kind": "scene"}`, …). A `redo` record
   replaces the prior turn on load; a torn final line is tolerated. On `!leave`
   the file is rotated (renamed to `history.<stamp>.jsonl`, never deleted); on
   `!join` it is restored only into an empty history. Debug-campaign runs mark
   their archives `history.<stamp>.debug.jsonl` so debug and live records can
   never be confused.

Hard facts (wounds, clocks, deadlines, commitments, scene pointer) are advanced
only by code — the LLM proposes, code validates and applies.

## RAG & Retrieval

Three-stage hybrid (ADR 019), each stage with its own module:

1. **Always-on arc summary** — a short recap of the campaign so far, always in
   the prompt (`dmbot/memory/recap.py`).
2. **Deterministic scene tracker** — a code-owned scene pointer
   (`WorldState.scene_id`) selects exactly one hand-curated scene card from the
   adventure compendium (`dmbot/rag/adventure.py`). The adventure is
   deliberately never vectorized, so part-3 spoilers cannot surface while the
   party is in part 1.
3. **Vector RAG** over rulebook and curated lore (`dmbot/rag/retrieve.py`):
   bge-m3 embeddings (multilingual, chosen over nomic-embed-text because the
   play language is German but the rulebook is English), cosine distance via
   sqlite-vec, top-3 across both sources with a 0.45 distance ceiling, plus a
   shape-based junk filter (OCR dash-run headings, statblock tags, picture-text
   dumps) tuned against `tools/rag_golden_set.json`.

Session memory is a fourth, separate corpus: `dmbot/rag/ingest_session.py`
chunks rotated `history.<stamp>.jsonl` per scene, embeds them into
`session_<channel_id>` sources with a stricter 0.38 ceiling and a small recency
malus, and maintains an FTS5 mirror for exact proper-noun recall
("what did NPC X say in session 3"). Debug archives route to
`session_debug_<channel_id>` by filename alone, making cross-contamination
structurally impossible.

## Document Ingestion

Offline CLI pipeline, no LLM: `tools/pdf_to_md.py` converts owned PDFs to
markdown; `dmbot/rag/ingest.py` then chunks with
`chunk_markdown` (~1600 chars per chunk) — chunks never cross a heading and
each carries its section title, which doubles as the citation label. Layout
noise (picture markers, bare page numbers) is stripped. Embedding is batched
via Ollama `/api/embed`. `rag_calibrate.py` plus `tools/rag_golden_set.json`
provide a retrieval calibration harness.

## Deterministic Mechanics

Dice and resolution are code, never the LLM. `dmbot/rules/engine.py` is a pure
engine taking an injected `random.Random` (seedable for tests); `roll` parses
`XdY±N`, `resolve_test` dispatches through a `RESOLVERS` registry (currently
`roll_under`), and combat arithmetic (`resolve_damage`, weapon damage + SL
minus soak) is pure with seeded RNG. The system profile
(`dmbot/rules/profile.py`) loads `data/systems/<name>.json` as pure data:
`resolution: "roll_under"`, a German `difficulty_ladder` with modifiers and
aliases, `auto_success_max: 5` / `auto_fail_min: 96` bands, criticals
(`"doubles"`), and optional combat/psyker/augmetics catalogs. An unknown
profile fails loudly into a rules-less mode rather than crashing. Roll
requests arrive three ways: the inline `<<TEST>>` narration marker
(`dmbot/rules/marker.py`) as fallback, the constrained-JSON roll router as the
primary when enabled, and player commands via `dmbot/voice/dicecog.py`.

## Campaign State

`WorldState` (`dmbot/memory/state.py`, one instance per voice channel) holds:
locations/scene id and flags, a code-owned in-game minute counter, deadlines,
Blades-style consequence clocks (`Clock`, allowed sizes 4/6/8), quests with an
`is_mission` flag, typed hard facts (item/promise, length-capped labels,
`open`/`revoked` statuses, cap 40), commitments, characters and NPCs
(wounds, attitude, agenda logs), and a compact recap. It renders as one
structured German block (`world_state_summary_de`) headed "harte Fakten —
verlass dich darauf, erfinde keine abweichenden Werte"; NPCs present in the
scene are listed separately from NPCs elsewhere.

## NPC Model

Per NPC (`Combatant` in `dmbot/memory/state.py`): a goal, an agenda log
(capped 10 offscreen steps), a memory list (capped 30), a scalar attitude on a
fixed five-step scale (`hostile → wary → neutral → friendly → loyal`), and a
faction. NPCs with a non-empty goal become agenda NPCs; the model proposes
offscreen steps and code validates, clamps, and appends them.

## Visibility & Private Information

No player-level private state (single shared voice channel, all players hear
the DM). Spoiler control is structural, not prompt-based: the adventure text
never enters the vector store (see RAG & Retrieval); spoiler chapters are
dropped at ingest; gated scene exits (`exit_requires` in
`dmbot/rag/adventure.py`) hide locked exits until their element is resolved,
and an unmet-gate rejection is logged only to the console, never the channel;
GM-direction one-shot notes are queued into the next turn's user message
rather than spoken. The nearest thing to private information is the separate
`session_debug_*` RAG namespace keeping debug-run transcripts out of live
memory.

## Session History

Append-only JSONL per completed turn plus typed journal events
(`dmbot/memory/history.py`), rotated on session leave, restored into an empty
history on join, and fed into the session-memory RAG ingest described above.
The same journals are the recording format for the golden-transcript evaluator
(`tests/golden/*.jsonl`); records may carry optional replay fields (raw LLM
text, parsed markers, router verdicts — ADR 046) that make them replayable.

## Relationship Graph

Not present in this codebase. There is no edge structure or traversal; the
closest mechanism is per-NPC scalar attitude plus faction, with deterministic
faction-scoped gossip propagation (`dmbot/memory/npc_memory.py`: memories of
importance ≥ 4 spread to same-faction NPCs without another LLM call).

## Rule-Source Citation

Coarse block-level sourcing. Retrieved chunks are grouped under fixed prompt
labels (rulebook ground truth before lore; session memory has its own block —
see `_SOURCES` / session label in `dmbot/rag/retrieve.py`), and every chunk
carries its heading as embedded context, so the model cites sections, not
pages. Profile data files carry human-readable source annotations (e.g. the
combat `_note` in `data/systems/imperium_maledictum.json` cites the rulebook
page the approximate weapon damage figures come from). No runtime page/line
citations.

## Useful Ideas

- "The LLM proposes, code validates and applies" as the universal seam: dice
  routing, scene moves, commitments, NPC memories, attitude drift, clocks, and
  threads all pass through code gates.
- Pure deterministic cores with injected RNG — the entire rules engine unit
  tests without Discord or Ollama.
- Constrained-JSON classifier side-calls (roll/scene/fact routers) instead of
  trusting the narration model's inline markers; a separate structured call
  fixes decision quality that narration alone demonstrably lacks.
- Spoiler discipline via data architecture: the adventure is a deterministic
  scene-pointer compendium, never vectorized.
- The three-file session artifact split with atomic state writes, append-only
  journal, rotation, and restore-only-if-empty semantics.
- Golden-transcript regression replay (`dmbot/tools/eval_replay.py`): rotated
  journals replay through a `PlaybackClient` that hands back recorded LLM
  responses in order, and every recorded state verdict (scene moves, clock
  ticks, time advances) is re-derived and compared — deviations gate refactors.
- Heading-aware chunking carrying section titles; per-corpus vector tables
  with a meta-table embedding-model pin and an FTS5 mirror for exact-term
  recall.
- Code-driven per-turn time advancement (2 minutes per turn, 15 per scene
  change) with the `<<ZEIT>>` marker as an accelerator clamped to 12 hours.

## Ideas Rejected

- Marker-only time advancement — the narration model's end-of-answer markers
  were unreliable, so per-turn code advancement was added instead.
- Serving the adventure via plain vector retrieval — spoiler leakage and
  nondeterminism; replaced by the scene-pointer compendium.
- LLM-owned clocks, flags, commitments, or attitude — all are code-owned hard
  state with model proposals clamped or rejected.
- LLM minting free-text state objects — fact labels pass a length/shape gate;
  doubtful classifications are discarded, not repaired.
- Real-time equaling in-game time — a fictional minute counter decoupled from
  wall time.
- Hidden or chained clocks in the first cut — clocks are a small closed set
  (sizes 4/6/8) visible on a Discord panel.

## License & Reuse

No LICENSE file in the clone (confirmed); all rights reserved by the author.
Nothing was copied: all implementations in this project are clean-room, and
`research/` is git-ignored study material only.

## Key Files

| Path | Role |
| --- | --- |
| `dmbot/orchestrator.py` | `DMBrain`: prompt build, LLM turn, marker parsing, router wiring |
| `dmbot/voice/delivery.py` | Turn delivery: TTS bridge, posted text, dice/clock/scene UI, latency |
| `dmbot/llm/prompt_assembly.py` | Single owner of system-prompt slice order |
| `dmbot/llm/roll_router.py` | Stateless constrained-JSON roll-need classifier (schema + parse) |
| `dmbot/llm/scene_router.py` | Constrained-JSON scene-move classifier over reachable exits |
| `dmbot/llm/fact_router.py` | Constrained-JSON commitment classifier (item/quest/promise) |
| `dmbot/rules/engine.py` | Pure dice + resolution engine with injected RNG, resolvers registry |
| `dmbot/rules/profile.py` | System-profile loader/validator (data-driven ruleset mechanics) |
| `data/systems/imperium_maledictum.json` | Shipped profile: roll-under, difficulty ladder, combat/psyker catalogs |
| `dmbot/memory/state.py` | `WorldState`: hard facts, clocks, deadlines, NPCs, atomic `state.json` |
| `dmbot/memory/history.py` | Append-only JSONL journal: append, redo-aware load, rotate |
| `dmbot/rag/adventure.py` | Scene-card compendium: deterministic pointer, gated exits, spoiler split |
| `dmbot/rag/retrieve.py` | sqlite-vec retrieval: thresholds, junk filter, session-memory search |
| `dmbot/tools/eval_replay.py` | Golden-transcript replay with mocked LLM (`PlaybackClient`) |
