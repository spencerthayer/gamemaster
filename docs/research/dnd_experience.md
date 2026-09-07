# dnd_experience — Research Report

- Repository: https://github.com/jeffbulltech/dnd_experience
- Cloned at: `research/dnd_experience`
- License: none (all rights reserved)
- Reuse verdict: no code reused; clean-room

## Purpose

A single-player, AI-driven D&D 5e game master. A FastAPI backend (`backend/app/`) with a React
frontend (`frontend/src/`) uses a local LLM (Ollama, default model `mistral` per
`backend/app/config.py`) for storytelling while a separate layer of deterministic Python enforces
rules (dice, ability math, character building). The stated identity is "storytelling by LLM,
mechanics by code": the LLM narrates and suggests rolls, but it never computes or authorizes a
mechanical result.

## Architecture

A conventional layered monolith with no framework magic — `backend/app/` is organized as:

- `app/api/` — thin FastAPI routers, one per domain (`campaigns.py`, `chat.py`, `dice.py`,
  `combat.py`, …). Routes only do auth checks, Pydantic parsing, and delegation.
- `app/services/` — all business logic (`campaign_service.py`, `chat_service.py`,
  `game_master.py`, `rag_service.py`, `ollama_service.py`, `dice_service.py`,
  `combat_engine.py`, `character_builder_service.py`, `attachment_service.py`, …). Services
  take a `Session` and plain payloads and raise `ValueError` for domain errors, which routes
  translate to HTTP 403/404.
- `app/models/` — SQLAlchemy 2.0-style ORM models (`Mapped`/`mapped_column`), one file per
  table; `app/schemas/` — Pydantic request/response models per domain.
- `app/utils/dice_roller.py` — pure dice math, no DB or I/O; unit-tested in
  `backend/tests/unit/utils/test_dice_roller.py`.
- `app/cli/` — admin tooling outside the request path: `rag_ingest.py` (build the vector
  store) and `seed_test_user.py`.

Cross-cutting plumbing is deliberately small: `config.py` (pydantic-settings), `database.py`,
`dependencies/auth.py` (JWT current-user), `rate_limit.py` (slowapi), `logging_config.py`,
and `main.py` (app assembly, CORS, security headers, uploads dir). The digest's
"no over-engineering" claim is verified: no DI container, no plugin system, no repository
layer — services query the ORM directly.

## Persistence

Relational (PostgreSQL, `postgresql+psycopg` default in `backend/app/config.py`; Alembic
migrations in `backend/migrations/versions/`). One `Campaign` row (`models/campaign.py`) owns
its children through `cascade="all, delete-orphan"` relationships plus `ondelete="CASCADE"`
FKs: a one-to-one `GameState` (`models/game_state.py`, `uselist=False`, `unique` campaign FK),
the `ChatMessage` transcript (`models/chat.py`), `Encounter` + `EncounterParticipant` rows
(`models/encounter.py`, `models/encounter_participant.py`), journaled `DiceRoll` records
(`models/dice.py` — with a **nullable** `campaign_id`, migration
`dice_rolls_nullable_campaign.py`, so the standalone roller works without a campaign), and
`CampaignAttachment` rows (`models/campaign_attachment.py`). Characters
(`models/character.py`) and their inventory (`models/inventory.py`) are user-scoped with
`SET NULL` on campaign delete rather than owned, and `CharacterDraft`
(`models/character_draft.py`) belongs to the user only — drafts survive outside any campaign.

The growth pattern the digest calls out is real: nearly every entity carries a generic JSON
column named `metadata` in SQL but exposed in Python as `extra`
(`mapped_column("metadata", JSON, default=dict)` in `models/chat.py`, `game_state.py`,
`dice.py`, `encounter.py`). These absorb per-feature state without migrations — the combat
action log and the token-usage/RAG metadata on GM messages both live there. JSON columns also
model semi-structured data directly: `Character.ability_scores`/`.skills`/`.attributes`,
`GameState.active_quests` (list of strings), `EncounterParticipant.conditions`/`.attributes`,
`InventoryItem.properties`.

## RAG & Retrieval

Local Chroma vector store over the 5e SRD, `backend/app/services/rag_service.py`:

- **Ingestion** (`ingest_corpus`, invoked by `app/cli/rag_ingest.py`): loads `.txt`/`.md`/`.json`
  documents from `backend/data/srd` (`_load_documents`), splits with LangChain's
  `RecursiveCharacterTextSplitter` at `settings.rag_chunk_size = 800` / `rag_chunk_overlap = 120`
  (`config.py`), stamps each chunk with `source`, `chunk_index`, and a `chunk_id`
  (`"{source}#chunk-{index}"`), and persists to Chroma at `backend/data/vectors` (collection
  `dnd_srd_rules`), embedded by `OllamaEmbeddings` against the same local Ollama that narrates.
- **Retrieval** (`fetch_relevant_rules`): `similarity_search_with_score` with `rag_top_k = 4`
  per player message, returning a `RAGContext` of `RAGCitation` dataclasses (`excerpt`,
  `source`, `chunk_id`, `score`, leftover metadata). Graceful degradation is explicit: if the
  vector store directory doesn't exist or the search raises, an empty `RAGContext` is returned
  and play continues without rules context.
- **Injection**: `ollama_service._format_rag_context` renders the citations into a "Relevant
  rules and references" section of the system prompt.
- **Scope**: one shared, global corpus — every campaign searches the same collection. The
  `db: Session` parameter of `fetch_relevant_rules` is explicitly reserved and unused
  (`_ = db  # Reserved for future per-campaign filtering.`), confirming the digest's "database
  filter hook reserved but unused." Citations are also persisted per GM message:
  `handle_player_message` stores full citation dicts in the message's `extra` metadata and the
  raw chunks in `ChatMessage.rag_context`.

`summarize_chat_history` in the same service compresses older messages into a short
`chatSummary` string for the prompt instead of feeding the full history window verbatim.

## Document Ingestion

Not present in this codebase for campaign materials. The only ingestion pipeline is the
SRD→Chroma CLI above. Campaign attachments (`app/services/attachment_service.py`) are stored
files, not knowledge: upload writes bytes to
`settings.attachments_dir / <campaign_id> / <uuid4-hex><ext>` (UUID stored filename, original
name kept in `original_filename`, size and content type in the row; empty uploads rejected),
and they are never parsed, chunked, or retrieved. The closest mechanism is
`rag_service._load_documents`, and it only reads the fixed SRD directory.

## Deterministic Mechanics

Pure, importable functions separated from any LLM path:

- **Dice** (`app/utils/dice_roller.py`): a single anchored regex
  `^(\d*)d(\d+)((kh|kl)\d+)?((\+|-)\d+)?$` parses expressions like `2d20kh1+3`. Keep-highest /
  keep-lowest select subsets of the rolled pool; `kh1`/`kl1` on a d20 pool of 2 is flagged as
  advantage/disadvantage, and a plain `1d20` request with `has_advantage`/`has_disadvantage`
  booleans rolls a second die and keeps max/min. Critical success/failure are detected from
  the kept rolls on d20s (kept 20 / kept 1). Totals are `sum(kept) + modifier`; the result
  includes all individual rolls, kept rolls, flags, and a detail dict. Validation failures
  (`count < 1`, `sides < 2`, unparsable expression) raise `ValueError`. The RNG is the
  module-global `random` — no seeding or injection, so results are not reproducible.
- **Roll persistence** (`app/services/dice_service.py` + `app/api/dice.py`): every API roll is
  journaled to `dice_rolls` with roller type (`player`/`gm`/`system`), expression, total, and
  the full detail dict; history is queryable by campaign/character.
- **Ability math** (`app/services/character_builder_rules.py`): `(score - 10) // 2` modifier,
  level-banded proficiency bonus (2 at 1–4 up to 6 at 17+), species bonus application.
- **Character builder** (`character_builder_rules.py` + `character_builder_service.py` +
  `app/api/character_builder.py`): a server-validated, multi-step draft workflow. Ability
  scores must come from standard array `(15,14,13,12,10,8)`, 27-point point-buy with a fixed
  cost table, or manual entry; `_validate_proficiencies`, `_validate_equipment`,
  `_validate_spells`, `_validate_origin`, `_validate_class` gate each step; drafts persist
  step-by-step in `CharacterDraft.step_data` (JSON) with `current_step`/`status`, and
  `finalize_draft` promotes a validated draft into a `Character`.

Dice and validation results are authoritative — the GM prompt tells the model to *suggest*
rolls, and the LLM never writes totals back into state.

## Campaign State

One `GameState` row per campaign (`models/game_state.py`): `location` (string),
`active_quests` (JSON list of strings), `summary` (free-text), `extra` metadata, `updated_at`.
Created automatically with each campaign and lazily on read
(`game_state_service.get_game_state` creates an empty row if missing); `update_game_state`
applies a Pydantic `model_dump(exclude_unset=True)` patch with owner checks. No event log, no
history of state changes, no per-entity quest records — quests are strings, not rows. The
state is rendered into the prompt by `game_master._format_game_state` ("Current location: …",
"Active quests: …", "Narrative summary: …"); no code path lets the model mutate `GameState`.

## NPC Model

Not present in this codebase. NPCs are not entities: they exist as (a) optional
`EncounterParticipant` rows during combat — `name`, `initiative`, `hit_points`/`max_hit_points`,
`armor_class`, `conditions` list, freeform `attributes` JSON (`models/encounter_participant.py`)
— and (b) descriptions the model improvises in narration. Nothing survives between scenes
outside an encounter row; there is no memory of NPC knowledge, attitudes, or secrets — the
digest's "structured memory is an acknowledged roadmap gap" matches the code. The closest
mechanism is the participant table plus the prompt's instruction to keep NPC motives hidden.

## Visibility & Private Information

Prompt-level, and the prompt is a substantial engineered artifact:
`ollama_service.SYSTEM_PROMPT_TEMPLATE` defines a figurative "GAME MASTER SCREEN" with seven
numbered secrecy categories — monster stats/tactics, unexplored maps/traps, NPC secrets and
motivations, loot locations, planned encounters, session notes/plot threads, and hidden die
rolls — each with concrete instructions on how to describe instead of reveal ("describe a
200 HP dragon as 'massive and formidable'"). Response-style rules add reactive storytelling
(never menu options or preemptive check suggestions). There is no structural enforcement: no
field-level public/private split, no player-facing projection, no message filtering. The only
code-level boundary is coarse access control — every campaign-scoped route/service checks
`campaign.owner_id == current_user.id` and otherwise 403s/404s (e.g. `api/campaigns.py`,
`api/chat.py`, `attachment_service._require_campaign_access`). Acceptable for its
single-player design; the digest's rejection of prompt-only secrecy as a general mechanism
stands.

## Session History

Append-only flat stream: `chat_history` (`models/chat.py`) with `role`
(`player`/`gm`/`system`), `content`, `rag_context` (JSON list of the retrieved chunks stored
on the GM message), and `extra` metadata (model name, token counts, RAG citations with
scores, chat summary, `combatActive` flag). Messages are inserted in
`game_master.handle_player_message` / `stream_player_message` — the player entry first (so
streaming UIs see it immediately), then the GM entry. No session/scene grouping, no
branching, no edit history: one ordered list per campaign.

Two read paths in `app/services/chat_service.py`:

- `fetch_chat_history` — newest-first internal fetch (limit 120 in `game_master`) for prompt
  building, passed through `rag_service.summarize_chat_history` before injection.
- `fetch_chat_history_page` — cursor pagination for the UI: `WHERE id < :before`,
  `ORDER BY id DESC`, fetch `limit + 1` to compute `has_more`, and return the last item's id
  as `next_cursor` (`schemas/chat.ChatHistoryPage`). Cursor by monotonic id, not offset.

GM responses stream over Server-Sent Events (`api/chat.py` `/chat/stream` →
`game_master.stream_player_message`): `event: token` deltas, then `event: done` with the
metadata payload.

## Relationship Graph

Not present in this codebase. No edge table, no entity pairings, no graph queries anywhere in
`app/models/` or `app/services/`. The closest mechanism is prose: relationships exist only as
narration inside chat messages and the GM prompt's instruction to keep NPC motivations hidden
until discovered.

## Rule-Source Citation

RAG citations are the only rule-provenance mechanism. `RAGCitation` (`rag_service.py`) carries
`excerpt` (the chunk text), `source` (formatted from chunk metadata via `_format_source`,
defaulting to "SRD reference"), `chunk_id` (`"{source}#chunk-{index}"`), and `score` (Chroma
similarity score); `as_dict()` drops empty fields, and the dicts are returned in the API
response `metadata` and persisted on the GM chat row. No page/section-level citations, no
document versioning, no link-back to a rules table — an excerpt plus a score is the whole
contract, and nothing verifies the model's claims against the excerpts.

## Useful Ideas

- Layered service separation with `ValueError`-based domain errors translated at the route
  layer — keeps routers trivial and business logic framework-free.
- A generic `metadata`/`extra` JSON column on nearly every entity absorbs growth (combat log,
  token usage, RAG citations) without column churn or migrations.
- Pure deterministic engines (`utils/dice_roller.py`, `character_builder_rules.py`) fully
  separated from the LLM path, with unit tests — the LLM proposes, code resolves.
- Compact regex dice grammar with `kh`/`kl` keep/drop and explicit adv/dis flags — small,
  testable, and sufficient for 5e's common rolls.
- Prompt-only "GM screen" as the cheapest possible visibility starting point: a structured,
  numbered secrecy checklist in the system prompt.
- Cursor pagination by monotonic id with a `limit + 1` `has_more` probe.
- Attachments as disk files (per-campaign directory, UUID stored names) plus DB metadata, with
  the file unlinked when the attachment row is deleted (`attachment_service.delete_attachment`).
- Retrieval caching with graceful fallback (`lru_cache`-held Chroma/Ollama clients, empty
  context on failure) and journaling every roll with roller type and detail for audit.

## Ideas Rejected

- Prompt-only GM screen as the long-term visibility model: it is a single-player convenience,
  leaky for any shared play; a first-class visibility engine with default-closed facts is the
  adopted alternative.
- Storing full RAG citation context (excerpts and scores) on every GM message: bloats history
  rows with retrievable-but-static data; citing sources at query time is enough.
- Combat logging without resolution: `combat_engine.process_action` appends timestamped action
  dicts to `Encounter.extra["actions"]` and explicitly defers "detailed rules resolution to
  future iterations" — no damage application, no turn order enforcement, no death. Adoption
  requires resolution semantics from day one.
- A global shared RAG corpus across all campaigns: the unused `db` filter hook shows the need
  was anticipated but unaddressed; per-campaign namespacing and precedence are required.
- Owner-only access baked in as the only authorization model: fine for single-player, but
  role/party membership must be structural before multi-player.
- Unseeded module-global RNG in the dice roller: results cannot be reproduced or audited; an
  injected, seeded RNG is the adopted pattern.
- Cascade-deleting campaign DB rows without touching child artifacts on disk:
  `campaign_service.delete_campaign` relies on DB cascade only, which is incomplete for
  file-backed data (see License & Reuse note below).

## License & Reuse

No LICENSE file exists anywhere in the clone (verified by filename search over the whole tree).
All rights reserved by the author; nothing may be reused. This report is architectural study
only — no code, prompts, or templates were copied. Clean-room.

[DISCREPANCY: prior-art.md's "useful ideas" list credits "attachments as disk plus DB metadata
with UUID names and synced cleanup." Synced cleanup holds for per-attachment deletes
(`attachment_service.delete_attachment` unlinks the file), but
`campaign_service.delete_campaign` deletes the campaign row via DB cascade and never removes
the campaign's upload directory, so campaign deletion orphans every attachment file on disk.]

## Key Files

| Path | Role |
| --- | --- |
| `backend/app/config.py` | All settings: DB URL, Ollama model/num_ctx/temperature, RAG chunk 800/overlap 120/top-k 4, attachments dir |
| `backend/app/models/campaign.py` | Campaign aggregate root with cascading child relationships |
| `backend/app/models/chat.py` | Append-only chat history with `rag_context` and generic metadata column |
| `backend/app/models/game_state.py` | One-to-one campaign state: location, quests, summary, metadata |
| `backend/app/models/dice.py` | Dice roll journal (nullable campaign for standalone rolling) |
| `backend/app/utils/dice_roller.py` | Pure regex dice engine: keep/drop, adv/dis, crits, modifiers |
| `backend/app/services/game_master.py` | Orchestrates RAG + history + state/character formatting + Ollama, persists the exchange |
| `backend/app/services/ollama_service.py` | The GM-screen system prompt, Ollama calls, RAG context formatting, streaming |
| `backend/app/services/rag_service.py` | SRD ingestion into Chroma, similarity retrieval, citation dataclasses, history summarizer |
| `backend/app/services/character_builder_rules.py` | Standard array / point-buy validation, modifier and proficiency math |
| `backend/app/services/combat_engine.py` | Encounter/participant tracking with action log; no rules resolution |
| `backend/app/services/chat_service.py` | Newest-first internal history fetch and cursor-paginated UI fetch |
