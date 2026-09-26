# Architecture

Gamemaster is a platform-agnostic tabletop RPG game-master platform layered on
top of SingularityNET Omega. This document defines the layers and the rules
that keep them from collapsing into each other.

## Layer model

```text
Player / GM
   |
Omega channel / provider / runtime
   |
omega-tabletop adapter
   |
Tabletop Runtime
   |
   +-- Game System Plugins
   +-- Content Packs
   +-- Campaign Store
   +-- Rules/RAG
   +-- Event Log
   +-- Visibility Engine
   +-- Relationship Graph
   +-- Source Document Store
```

The registered tabletop skill surface includes GM and player workspaces.
Campaign and setting workspaces expose GM operations. The player workspace
omits GM-only skills and binds one participant per process (ADR 0012 / 0013).

## Who owns what

| Layer | Owns | Must not |
|---|---|---|
| **Omega** | cognition, tools, model interaction, channels, agent loop | be rewritten to fit the tabletop domain |
| **omega-tabletop adapter** | the single Omega-facing integration point | contain game logic |
| **Tabletop Runtime** | campaign orchestration, persistence, knowledge boundaries, retrieval | know the meaning of any game mechanic |
| **Game System Plugin** | mechanics, system-specific schemas, deterministic resolution | depend on Omega or MeTTa |
| **Content Pack** | rules, settings, adventures, lore as data | execute code |
| **Campaign** | authoritative world state and history | depend on retrieval recall to be true |
| **LLM** | intent interpretation, narration, ambiguous adjudication | silently replace deterministic mechanics |

## Key rules

1. **Omega provides cognition. Tabletop Runtime provides orchestration. Game
   plugins provide mechanics. Content packs provide rules and world material.
   Campaign state provides truth.**
2. **Omega-facing code is thin.** The `tabletop` plugin is the only
   Omega-specific integration point. It initializes the runtime, registers
   skills, adds prompt guidance, discovers campaigns and system plugins,
   translates Omega skill calls into Tabletop Runtime calls, and serializes
   results back. No game logic lives there. Allocated campaign context enters
   Omega as a second prompt extension, recomputed when `getContext` runs.
   Omega still appends the current human message after that extension, using
   the `:-:-:-:` separator in `src/loop.metta`. The extension does not see
   that utterance.
3. **Game-system plugins do not depend on Omega or MeTTa.** They implement a
   platform-agnostic Python API. Capability negotiation decides which features
   a system offers; the API is not D&D-shaped. Armor class, saving throws, hit
   locations, spell slots, mana, refresh, and sanity are meanings attached by
   plugins, not primitives of the core.
4. **Deterministic mechanics are never skipped.** When a mechanic is known, the
   plugin resolves it. A system advertising `ACTION_RESOLUTION` is always
   called: `tabletop.orchestration.turn.resolve_action` is the only supported
   path to a mechanical result and manufactures none of its own. When the
   plugin cannot decide it returns `RULING_REQUIRED`, `UNRESOLVED`, or
   `UNSUPPORTED` with rule references and context. The LLM is never allowed to
   fabricate an outcome that a plugin could have resolved, and a missing
   mechanic is not permission to invent one.
5. **Campaign truth does not depend on vector-memory recall.** Facts live in a
   structured SQLite store fed by an append-only event log. Retrieval (lexical
   and vector) only helps the agent find material; it never defines truth.
6. **Original source files stay separately accessible.** RAG representations
   are derived, and provenance always points back at the raw document, page,
   and section.
7. **Content packs are non-executable.** Only explicitly trusted game-system
   plugins are executed. A mounted plugins directory is the only dynamic code
   boundary; uploaded or ingested documents never execute.
8. **Rulings beat sourcebooks.** Retrieval precedence is: campaign rulings,
   campaign house rules, active system rules, enabled supplements,
   adventure-specific rules, then GM adjudication.

## Omega integration

Omega loads plugins listed in `config/plugins.yaml`. The `tabletop` plugin is
implemented through Omega's own plugin API: it registers skills and prompt
extensions, and it is the only place that touches Omega internals. Individual
game systems are not Omega plugins.

Documented upstream modifications, if any, are tracked so the diff against the
upstream commit stays reviewable (see `UPSTREAM.md`).

## Tabletop Runtime

The runtime is a pure-Python package (`tabletop/`) with no MeTTa or Omega
imports. It is standalone-testable. Its subsystems:

- `tabletop/api/` — plugin API, capabilities, actions, resolution, events,
  entities, rules, visibility contracts.
- `tabletop/plugins/` — manifest parsing, discovery from mounted directories,
  registry and lifecycle.
- `tabletop/campaign/` — store, models, event store, projections, relationships,
  visibility.
- `tabletop/documents/` — ingestion (markdown, text, PDF), provenance.
- `tabletop/retrieval/` — retriever interface, lexical (SQLite FTS5), vector,
  hybrid, namespace filtering.
- `tabletop/orchestration/` — turn handling, context building, adjudication,
  session lifecycle.
- `tabletop/storage/` — SQLite access and migrations.
- `tabletop/dice/` — generic dice parsing and rolling, no system semantics.

## Game-system plugins

Executable Python packages discovered from a configured plugins directory.
Each declares a manifest (id, name, API version, entrypoint, capabilities)
and implements `GameSystemPlugin`. See the plugin API doc for the full
contract.

## Content packs

Data-only artifacts partitioned by kind: `rules`, `setting`, `adventure`,
`campaign-seed`, `supplement`. They declare compatible systems and may mark
material as GM-only (`gm_only`). They never execute code.

## Retrieval

The runtime defines its own retrieval abstraction rather than reusing Omega's
single generic Chroma collection. Namespaces keep domains separate
(system rules, settings, adventure, campaign, rulings, character, NPC), and
provenance is preserved on every retrieved chunk so a disputed rule can be
checked against the original file.

## Persistence and truth

SQLite is the authoritative store. The event log is append-only and immutable;
state projections derive from events where practical. Human-readable campaign
projection files (markdown/YAML per campaign) are operator-friendly views of
the same truth, never a second authority.

## Scenes and the campaign clock

`SceneStore` (`tabletop/campaign/scenes.py`) is the only writer of `scenes`,
`scene_members`, and `campaign_clock`. Each mutation owns a transaction and
emits nothing; callers that own the event log use the `*_in_transaction`
variants so rows and their events move together.

`TabletopRuntime` exposes the scene lifecycle: `open_scene`, `close_scene`,
`transition_scene`, `get_current_scene`, `enter_scene`, `exit_scene`,
`get_game_time`, and `set_game_time`. There is deliberately no second
lifecycle service. `transition_scene` closes the current scene and opens the
next in one transaction, so a campaign is never left with no open scene
because the second half of the move failed.

A turn that does not name a scene writes into the campaign's open scene, so
scene-scoped state changes land where play is actually happening.

## Resume and prompt context

`build_scene_snapshot` (`tabletop/campaign/scene_snapshot.py`) is the single
read-only answer to "where is this campaign right now". Operator resume and
model prompt context both read it.

- It takes a `viewpoint` as a required argument. Choosing one implicitly is
  how GM-only state reaches a player prompt.
- It reads the event log, never chat history, and never summarizes a
  transcript.
- It writes nothing.
- Resume returns the latest scene even when it is closed, so a scene stays
  inspectable after a restart instead of the snapshot reporting "none".
  A campaign that has never opened a scene reports `None`, not a placeholder.
- Prompt context renders the snapshot as readable text; handing the model the
  raw mapping would put a Python repr in the prompt.

## Validation

`tabletop.campaign.validation` produces typed checks with stable ids such as
`campaign.exists` and `participant.gm.count`. `tabletop.campaign.readiness`
still exists for callers that want human-readable prose; the two answer
different questions and both are legitimate.

Readiness decides whether a *campaign* is sound. It deliberately ignores live
checks, because a broken environment is not a broken campaign. The CLI
separates the two exit classes: 1 for a failed static check, 2 for a runtime or
environment failure. A campaign that is configured correctly on a machine with
no Docker exits 2, not 1.

Live probes are explicit, injectable callables rather than hidden side
effects. Each is bounded by a timeout, and a probe that raises or hangs becomes
a failed check instead of taking the process down. Credentials are reported by
slot name, never by value.

`--channel-probe` is separate from `--live` because it is the only probe that
sends a real message. It is never implied.
