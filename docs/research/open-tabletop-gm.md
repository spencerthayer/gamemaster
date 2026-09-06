# open-tabletop-gm — Research Report

- Repository: https://github.com/Bobby-Gray/open-tabletop-gm
- Cloned at: `research/open-tabletop-gm`
- License: AGPL-3.0-or-later
- Reuse verdict: no code reused; clean-room

## Purpose

An LLM-agnostic, system-agnostic tabletop GM framework (`README.md`, `SKILL.md`). The design rule is stated in `SKILL.md` ("Script-First Rule") and `SYSTEM-PORTING.md`: the LLM never does arithmetic, lookups, or state bookkeeping — it narrates, role-plays, and writes structured output; Python scripts verify and persist. `SYSTEM-PORTING.md` adds the motivation for local models: dice, HP math, turn order, and timed effects are exact regardless of model capability — "the model's job is narration and judgment; the scripts handle the math."

It ships as a Claude/OpenCode skill (`SKILL.md` is a prompt, not a library) plus an optional "cinematic display companion" — a Flask server (`display/gm-display-app.py`) that renders narration, stats, and dice on a shared screen and binds player phones as dice rollers.

## Architecture

Two layers, documented in `SYSTEM-PORTING.md` ("How the architecture works"):

1. **GM core** — `SKILL.md` (13 GM-craft standards, pacing, NPC discipline), `SKILL-commands.md` (command procedures), `SKILL-branches.md` (mode-router branches). Knows nothing about any game system.
2. **System layer** — `systems/<system>/system.md` loaded alongside the core at `/gm load`; supplies dice-resolution logic, stats, health model, resource, conditions, advancement. `systems/TEMPLATE.md` is the scaffold for a new module and `SYSTEM-PORTING.md` is the full porting contract: what is universal (dice, initiative, timed effects, tracker, campaign files, calendar, display, Live State Flags), what is per-system config (`CONDITION_COLOURS` in `scripts/tracker.py`, rest mapping), and what is a full replacement (system-specific scripts under `systems/<system>/`).

Universal scripts live in `scripts/`: `dice.py`, `combat.py`, `tracker.py`, `calendar.py`, `campaign_search.py`, `gm_graph.py`, plus infrastructure (`paths.py`, `path_config.py`, `name_registry.py`, `npc_rename.py`, `import_campaign.py`, `migrate_system_version.py`). The working non-d20 system is `systems/brp/system.md` (Chaosium BRP, percentile roll-under, ORC-licensed; see `systems/brp/NOTICE`); `systems/dnd5e/` is the reference implementation with an SRD data layer (`build_srd.py`, `sync_srd.py`, `lookup.py`, `xp.py`). Per-system UI theming is manifest-driven, not per-system front-end code: `systems/UI-MANIFEST.md` and `systems/dnd5e/ui.json` feed one renderer. A note in `scripts/gm_graph.py` records that the LLM-based Haiku extractor of the upstream `claude-dnd-skill` was deliberately replaced by a fully local deterministic extractor — no model dependency in any graph subcommand.

## Persistence

Plain markdown under a configurable data root: `scripts/paths.py` defaults to `~/open-tabletop-gm` (override via `path_config.py` / `/gm path`), with `campaigns/<name>/` and a global `characters/` roster holding every PC's latest state across campaigns. `scripts/paths.py` also parses the `state.md` header regexes (`**System Module:**`, `**System Version:**`) and forces UTF-8 stdio for non-English console safety.

Campaign files (templates in `templates/`): `state.md` (live state), `world.md` (foundations, Three Truths, factions, adventure nodes, quest seeds), `npcs.md` (index) and `npcs-full.md` (full entries), `session-log.md` plus `session-log-archive.md`, and JSON sidecars written by scripts: `graph.json` (`gm_graph.py`), `tracker.json` (`tracker.py`), `calendar.json` (`calendar.py`), `session_tail.json` (display replay buffer); node names are mirrored into the cross-campaign `.name_registry.json` (`name_registry.py`).

The session save is a deterministic procedure the LLM executes, spelled out step-by-step in `SKILL-commands.md` (`/gm save`): append the session entry, increment session count, sync World State, rewrite `## Live State Flags`, run the arc check, archive old log entries non-destructively, and verify `session_tail.json` via `display/verify_tail.sh` with `display/write_canonical_tail.py` as a backstop.

## RAG & Retrieval

Deliberately not vector RAG. Context assembly is named deterministic queries:

- Scene subgraph extraction from the relationship graph: `scripts/gm_graph.py scene-context --place/--present/--threads` runs a hop-bounded BFS over edges active at a given session and prints a typed subgraph (`cmd_scene_context`, `_expand`, `_emit_subgraph`). Auto-pulled at `/gm load`.
- Pinned facts: `state.md → ## Pinned Facts`, read at every load (see Campaign State).
- Keyword search across campaign files: `scripts/campaign_search.py` — case-insensitive, multiple keywords AND-combined within one block, returns file name, nearest `##`/`###` heading, and matching lines with context (`find_section_heading`, `search_file`).
- Compaction resilience: after context compaction the prompt orders targeted re-reads — Live State Flags first, then Current Situation / Recent Events, then the single NPC entry, then Continuity Archive, then session log (`SKILL.md`, "Compaction resilience").

## Document Ingestion

`scripts/import_campaign.py`: extracts text only from PDF (pdftotext/poppler with PyMuPDF fallback), markdown (YAML frontmatter stripped), DOCX, TXT; chunks at `CHUNK_WORDS = 4000` words and prints per-chunk output for the GM model to analyze. `SKILL-commands.md → /gm import` then has the human review the structure and write campaign files (acts, chapters, key beats, steering notes) from the extracted text. No embedding pipeline, no vector store.

## Deterministic Mechanics

All in scripts, zero LLM involvement (`SKILL.md` Script-First Rule; `SKILL-scripts.md` is the syntax reference):

- **Dice**: `scripts/dice.py` parses `XdY±Z` with `kh`/`kl` keep modes and `adv`/`dis` flags (`parse_notation`), rolls via `random.randint`, formats full math, flags nat 20/1. Success-counting for dice-pool systems is described in `system.md` and applied by the GM to raw rolls (`SYSTEM-PORTING.md`).
- **Player dice**: `scripts/dice_player.py` routes a PC roll through the display server; the server rolls (see Visibility).
- **Conditions / timed effects**: `scripts/tracker.py` persists `tracker.json` per campaign — conditions with severity colours, concentration, death saves, round/minute/hour/indefinite effect durations with `effect tick` decrementing (`cmd_effect`, `_parse_duration`, `_fmt_effect`); pushes condition pills to the display.
- **Initiative and combat**: `scripts/combat.py` — GM-rolled initiative for all combatants regardless of `roll_mode` (`SKILL.md` "Initiative"); the turn sequence and combat-log flow are scripted in `SKILL.md` ("Per-turn combat sequence").
- **In-world calendar**: `scripts/calendar.py` persists `calendar.json` with configurable month/day names and lengths; `advance`/`rest`/`set`/`time` do hour-level arithmetic with time-of-day labels and push date announcements.
- **Roll-mode switch**: `state.md → ## Session Flags → roll_mode` selects `players` (default — GM calls for the roll, waits for the physical/phone roll, never auto-rolls a PC) vs `auto` (GM rolls everything openly with inline math). Per-character override arrives as a `[[<Char> roll mode: …]]` directive from `display/check_input.py`; precedence is per-character over campaign (`SKILL.md` "Dice convention").

## Campaign State

`templates/state.md` defines the sections; `SKILL.md` and `SKILL-commands.md` define their update discipline:

- `## Current Situation` — location, in-world date, party line.
- `## Pinned Facts` — a deliberately small stable canon set (promises, names, house rules), managed by `/gm pin`; explicitly never rewritten wholesale at save, unlike Live State Flags (`SKILL-commands.md → /gm pin`).
- `## World State` — canonical in-world date, season/weather, threat-arc stage, faction one-liners.
- `## Active Quests`, `## Open Threads & Rumours`, `## Faction Moves` (end-of-session answer to "what did each faction do while the party was occupied"), `## Recent Events`, `## Active Combat`.
- `## Live State Flags` — compact key-value facts (cover/party position, faction stances, NPC dispositions) rewritten at every save as the context-compaction survival anchor; the prompt re-reads this section alone before any recap claim (`SKILL.md` "Compaction resilience").
- `## Campaign Arc` — YAML block with `type: sandbox | structured | dynamic`; dynamic arcs carry acts, consequence-shaped beats (`what_changes`), `world_pressure`, `outstanding_beats`, `steering_notes`, `revision_log`; structured arcs carry chapters, `telegraph_scene`, and hub-and-spoke flags.
- `## Session Flags`, `## GM Style Notes`, and `## GM Notes (hidden from players)`.

## NPC Model

`templates/npcs.md` defines both tiers. Index table: Name | Role | Faction | Location | Attitude | Notes. Full entries (`npcs-full.md` per campaign) carry demeanor, motivation, **secret**, speech quirk, attitude toward party on the hostile→unfriendly→neutral→friendly→allied scale, faction, current goal, schedule, four personality axes (Trustworthy↔Deceptive, Ambitious↔Content, Loyal↔Opportunistic, Brave↔Cowardly), and a minimum-two relationship list (Knows/Owes/Hates/Fears/Allied with). `SKILL.md` ("NPC detail discipline") requires reading the full entry before writing substantive dialogue for a named NPC; index rows carry surface traits only.

## Visibility & Private Information

Convention-based; no per-player line-of-sight permission model:

- NPC `Secret:` field in full entries (`templates/npcs.md`).
- `## GM Notes (hidden from players)` in `state.md`; GM-facing Three Truths and clue trails in `world.md`.
- Hidden rolls via `dice.py --silent` — narrate only the perceived result (`SKILL.md` "Active GM Mode").
- GM-side display help (`display/dm_help.py`, the `/help-request` tutor path) is sent to the GM's view via `send.py --tutor`, never to player-facing feeds.
- Transport secrecy: the display server issues a 64-hex token (`secrets.token_hex(32)` in `display/gm-display-app.py`) checked as `X-DND-Token` on every request; optional self-signed TLS via `display/setup_tls.py`. Player input and dice endpoints sanitize inputs.
- **Non-spoofable rolls**: the `/player-input/dice` endpoint rolls server-side with `secrets.randbelow` — the phone submits character/spec/modifier/advantage, the server computes the result, broadcasts it on the feed, and correlates it against pending GM dice-requests by request id (`display/gm-display-app.py:1992-2094`).

## Session History

Appended `session-log.md` entries (Recap / Key Events / Decisions Made / XP / Open Threads / DM Calibration per `templates/session-log.md`). Archival is non-destructive (`SKILL-commands.md → /gm save` step 6): after session count > 3 only the two most recent entries stay in the live log; older entries move to `session-log-archive.md` (append-only, never deleted) after a 3–5 bullet continuity summary is folded into `## Continuity Archive` in `state.md`. A stale-pointer sanity check runs at `/gm load` (`SKILL-branches.md`): if the arc pointer's `outstanding_beats` are cleared or the party plainly ended in the next beat's situation, the GM surfaces the drift instead of opening another scene in a finished beat. Display-side, `session_tail.json` is a replay buffer with health verification and canonical rewrite at save.

## Relationship Graph

Typed, time-stamped JSON graph at `<campaign>/graph.json`, managed by `scripts/gm_graph.py`. Schema (module docstring): `nodes` with `{id, type, name, tags, summary}` — node types npc, faction, place, item, thread (open vocabulary) plus a synthetic `party` node; `edges` with `{id, from, to, type, since_session, until_session, note}`. Edge vocabulary: `loyal_to, opposes, allied_with, member_of, lives_in, controls, knows_about, friends_with, lover_of, owes, rules, related_by_blood, advances_thread, blocks_thread`. Party stance toward NPCs/factions is a normalized 5-point `disposition`/`standing` edge (`allied|friendly|neutral|suspicious|hostile`, `DISPOSITION_LEVELS`) mirroring the display sidebar.

An edge is active at session N iff `since_session <= N` and (`until_session` is null or `> N`) (`_edge_active_at`). Two distinct end semantics:

- **close-edge** (`cmd_close_edge`): the canonical end — the relationship was real, then ended; sets `until_session` (optionally with a verbatim `closed_anchor` quote) and warns on overwrite.
- **supersede-edge** (`cmd_supersede_edge`): the hard retcon — the original edge was wrong; sets `superseded_by` (+ optional `supersede_reason`); the edge stays in the graph for audit trail but never surfaces as active state.

Queries: `list`/`show` with `--at-session` time travel, `subgraph` seeded BFS, `scene-context` place/present/threads seeding. The graph is explicitly an index over canonical markdown (`npcs-full.md`, `session-log.md` remain authoritative).

Extraction is deterministic and tested: `scripts/graph_extract_deterministic.py` pattern-matches session-log sentences against `data/graph/verb_table_seed.yaml` (verb forms → canonical edge type, pattern templates with X/Y/Z slots, emit lists, symmetric flag, confidence; inclusion/borderline/exclusion layers tuned on a ~1,014-observation corpus). Entity sets come from canonical files (`build_entity_set`), aliases resolve short forms, and every proposal carries verbatim source anchors plus session attribution (`session_for_offset`). The docstring records measured tradeoffs: recall ~50%, precision ~95%, zero LLM calls. `gm_graph.py extract --write` produces a proposals JSON consumed by `extract-apply` with `--pick`/`--review`; extraction is covered by `tests/test_deterministic_extract.py` and graph semantics by `tests/test_gm_graph.py`.

## Rule-Source Citation

Dataset-level provenance for the bundled SRD JSON: `systems/dnd5e/build_srd.py` writes a `_meta` block into `dnd5e_srd.json` with per-source repo, branch, commit `sha`, and `fetched_at` for both upstream repos (5e-bits/5e-database and foundryvtt/dnd5e); `systems/dnd5e/sync_srd.py` compares stored SHAs against live upstream commit SHAs and rebuilds only when either source moved. Edge-level citation exists in the graph via verbatim anchors on extracted proposals and closed edges. The SRD records themselves carry no per-record citations — consistent with prior-art.md. BRP rules text cites its source at document level (`systems/brp/system.md` header + `NOTICE`).

## Useful Ideas

- Two-layer system abstraction with a documented porting contract (`SYSTEM-PORTING.md`): an explicit list of what is universal, what is per-system configuration, and what is a full replacement — including honest compatibility expectations by system type.
- State template split of Pinned Facts (stable canon, never auto-rewritten) vs Live State Flags (rewritten every save) as a compaction survival anchor, with a targeted re-read ladder after compaction.
- Typed, time-stamped graph edges with distinct close-vs-supersede semantics and session time-travel queries.
- Deterministic source-anchored edge extraction with human approval (`extract --write` → review → `extract-apply --pick`), tuned from a corpus, with honestly documented recall/precision tradeoffs.
- Roll-mode split (`players` vs `auto`) with per-character override and a server-side non-spoofable roll path.
- Name registry with safe rename across all coupling surfaces: `scripts/name_registry.py` (cross-campaign `.name_registry.json`, collision checks, retire, prose-scan for unregistered names) and `scripts/npc_rename.py` (whole-word variant replacement across markdown files, honorific-stripped variants, graph node rename via IDs, archive audit note, pre-rename backup).
- Manifest-driven per-system UI theming (`systems/UI-MANIFEST.md`, `systems/dnd5e/ui.json`) instead of per-system front-ends.
- Consequence-shaped arc beats (`what_changes` as a state-of-the-world, not an event) with an explicit pre-emption protocol at `/gm save`.

## Ideas Rejected

- Vector RAG over campaign state — markdown files plus named queries (scene subgraph, pinned facts, keyword search with file/section attribution) win for this domain.
- Per-system front-end styling — single manifest-driven renderer instead.
- Server-hosted auto-push display as the primary surface — the display is an optional companion; campaign truth stays in local markdown.
- LLM-dependent graph extraction — the Haiku-based upstream extractor was replaced by the deterministic verb-table extractor; no graph subcommand calls a model (`scripts/gm_graph.py` docstring).
- Fully-fledged graph databases — a flat JSON file with linear edge scans suffices at campaign scale.

## License & Reuse

`LICENSE` is the GNU Affero General Public License ("GNU Affero General Public License as published by…"), matching the AGPL-3.0-or-later entry in the prior-art summary table. The AGPL network clause applies to the Flask display server as much as to the scripts. Verdict: no code reused; clean-room.

## Key Files

| Path | Role |
|---|---|
| `SKILL.md` | GM core prompt: standards, compaction ladder, roll-mode convention, display sync |
| `SYSTEM-PORTING.md` | Porting contract: universal vs per-system vs full-replacement |
| `systems/brp/system.md` | Working non-d20 system: BRP percentile roll-under rules |
| `systems/dnd5e/build_srd.py` | Builds bundled SRD JSON with `_meta` source provenance |
| `scripts/dice.py` | XdY±Z parser with keep/drop and adv/dis |
| `scripts/gm_graph.py` | Typed time-stamped graph; close/supersede; scene-context queries |
| `scripts/graph_extract_deterministic.py` | LLM-free verb-table edge extractor with verbatim anchors |
| `scripts/tracker.py` | Conditions, timed effects, death saves (`tracker.json`) |
| `scripts/name_registry.py` | Cross-campaign name registry and collision checks |
| `scripts/npc_rename.py` | Safe rename across all coupling surfaces |
| `templates/state.md` | State template incl. Pinned Facts, Live State Flags, Campaign Arc |
| `display/gm-display-app.py` | Display server: token auth, server-side dice roll, phone routing |
