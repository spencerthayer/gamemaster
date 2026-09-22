<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="Gamemaster architecture, with Omega below the tabletop runtime, game-system plugins, content packs, and campaign state.">
</p>

# Gamemaster

Gamemaster is an early tabletop role-playing game runtime built on
[SingularityNET Omega](https://github.com/singnet/Omega). It separates the
agent loop from game mechanics, source material, and campaign state. The
repository contains a working first draft of that separation, not a finished
game-master product.

## Why Omega is the foundation

Omega already supplies the agent loop, model-provider integration, channels,
memory, tools, and plugin loading. Gamemaster adds one Omega-facing adapter in
`plugins/tabletop/` and keeps tabletop code in a separate Python runtime. This
lets the project add campaign behavior without replacing Omega's core loop.

## Layers

```text
Player or GM
    |
Omega channels, providers, memory, and tools
    |
plugins/tabletop adapter
    |
tabletop runtime
    |-- workspace-scoped skills and turn orchestration
    |-- SQLite campaign state and append-only events
    |-- visibility, relationships, rulings, and sessions
    |-- document ingestion and retrieval
    |
    +-- game-system plugins in systems/
    +-- data-only content packs
```

The generic API uses actions, entities, resolutions, events, and visibility
scopes. System-specific concepts stay inside game-system plugins. A mechanic
advertised by the active plugin is resolved through that plugin. Unsupported
or unresolved mechanics produce an adjudication result without invented
mechanical numbers.

Each process has one workspace, selected at startup with
`TABLETOP_WORKSPACE=setting` or `TABLETOP_WORKSPACE=campaign`. The workspace
cannot be changed on a running process. Use separate processes when both
surfaces are needed at the same time.

See [docs/architecture.md](docs/architecture.md) for the detailed boundaries.

## Game-system plugins

Game-system plugins are trusted Python code. Each plugin has a strict
`plugin.yaml`, implements the `tabletop/v1` API, and advertises only the
capabilities it implements. Built-in and configured external plugins use the
same discovery and loading path. Plugins load at startup, so changes require a
restart.

The repository includes:

- `freeform`, a small generic reference system for checks, opposed checks,
  dice, and resource tracking.
- `dnd5e`, a partial D&D 5e reference implementation for the 2014 revision.
  It covers selected checks, attacks, damage, basic conditions, initiative,
  movement, and rests. It does not provide full 5e support. Spells, classes,
  feats, monster stat blocks, multiclassing, and other mechanics remain out of
  scope.

The 5e demo under `examples/campaigns/dnd5e-demo/` is an automated reference
flow, not a complete playable rules implementation.

## Content packs

Content packs describe rules, settings, adventures, campaign seeds, or
supplements. Their manifests are data only. The loader rejects executable
entrypoints, unknown fields, unsafe YAML object tags, and GM-only paths that
escape the pack directory.

Content-pack loading currently validates metadata and trust boundaries. It
does not install dependencies or turn an arbitrary pack into a ready campaign.

## Persistence

Campaign data is stored in SQLite. The schema covers campaigns, entities,
facts, documents, relationships, sessions, rulings, retrieval records, and
append-only event history. State changes and their event records are applied
in one transaction during the tested turn flow.

SQLite is the authority for campaign state. Human-readable campaign files are
projections and source material, not a second writable source of truth.

## Documents and retrieval

Markdown and text-layer PDFs can be ingested into provenance-bearing chunks.
PDF extraction preserves page numbers when text is present. Scanned or
image-only PDFs have no OCR path in this draft. They are marked unsupported
and need manual review.

Retrieval has isolated namespaces and source references. Lexical search uses
SQLite FTS5. Vector search is optional. When configured, it checks the
embedding model and dimension and falls back to lexical search when the
semantic tier is unavailable or incompatible. The current vector backend
stores vectors in SQLite and computes similarity in Python, so it is intended
for small corpora.

Retrieval is never campaign truth. A retrieved campaign chunk is still a
search result. Authoritative state remains in the structured campaign store
and event history.

## Current maturity

This is a first draft for development and architecture validation.

Implemented and covered by tests:

- the Omega adapter and workspace-scoped skill registration
- capability-based plugin loading and two reference plugins
- SQLite campaign state, append-only events, sessions, visibility,
  relationships, rulings, and provenance
- Markdown and PDF text-layer ingestion
- lexical retrieval and an optional vector-to-lexical cascade
- freeform and partial 2014 5e end-to-end demonstration tests

Also present, but not covered by a runtime behavior test: a Compose deployment
definition for one Omega process with persistent state. Its configuration is
checked with `docker compose config`.

Important limits:

- there is no complete game system
- scanned PDFs need manual review
- plugins are trusted code and require a restart after changes
- one workspace runs per process
- the Compose stack is a deployment starting point, not a hardened
  multi-tenant service

## Quick start

Python 3.11 is required for the tabletop test suite. From the repository root:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest tests/tabletop -q
```

Run the focused demonstrations with:

```bash
python -m pytest \
  tests/tabletop/test_demo_freeform.py \
  tests/tabletop/test_demo_dnd5e.py \
  -q
```

These commands exercise the runtime directly. Running an interactive Omega
channel also requires provider and channel configuration from the
[Omega documentation](https://github.com/singnet/Omega#readme).

## Docker Compose and Portainer

Copy the environment template, review every value, create the bind-mount
directories if needed, then start the stack:

```bash
cp .env.example .env
mkdir -p library campaigns
docker compose config
docker compose up --build
```

At minimum, replace `OMEGA_AUTH_SECRET`, choose the provider and channel, and
set the provider credential such as `ASI_API_KEY`. The Compose file defines
one Omega service. Named volumes persist Omega memory and tabletop SQLite
state. Plugin and library mounts are read-only. The campaigns mount is
read-write. The entrypoint copies those three mounts onto the container
filesystem before Omega applies Landlock, because that policy cannot read
Docker Desktop bind mounts. The stack does not mount the Docker socket.

For Portainer:

1. Create a Git-backed stack from this repository.
2. Copy the values from `.env.example` into the stack environment.
3. Replace the example secret and provider settings.
4. Make the host paths named by `TABLETOP_PLUGIN_HOST_PATH`,
   `TABLETOP_LIBRARY_HOST_PATH`, and `TABLETOP_CAMPAIGNS_HOST_PATH` available
   to the Docker host.
5. Deploy the stack.

Relative bind paths resolve from the stack directory. Portainer and Docker
host path behavior varies by installation, so use absolute host paths if the
relative defaults do not resolve as expected.

## Write a game-system plugin

Create one directory under a configured plugin root:

```text
my-system/
|-- plugin.yaml
`-- my_system/
    |-- __init__.py
    `-- plugin.py
```

Use a manifest like:

```yaml
id: my-system
name: My System
api_version: tabletop/v1
version: 0.1.0
entrypoint: my_system.plugin:MySystemPlugin
description: Selected mechanics for My System
```

Subclass `tabletop.api.plugin.GameSystemPlugin`. Implement `info`,
`capabilities()`, and `resolve()` for the mechanics the plugin supports.
Return `UNSUPPORTED`, `UNRESOLVED`, or `RULING_REQUIRED` when code cannot
produce a deterministic result. Do not advertise unfinished capabilities.

Set `TABLETOP_PLUGIN_PATH` to the parent directory. Multiple roots use the
operating system path separator. Restart the process after adding or changing
a plugin. Full details are in [docs/plugin-api.md](docs/plugin-api.md).

## Write a content pack

Create a directory with `content-pack.yaml` and the source files:

```yaml
id: example-adventure
name: Example Adventure
pack_type: adventure
system_id: freeform
version: 0.1.0
gm_only:
  - secrets.md
  - npc-agendas.md
```

Valid `pack_type` values are `rules`, `setting`, `adventure`,
`campaign-seed`, and `supplement`. Keep every `gm_only` path relative to the
pack directory. Do not add an `entrypoint`. Content packs cannot execute code.
See `examples/campaigns/freeform-demo/content-pack/` for a small example.

## Security warning

Treat every ingested document as untrusted text. A sourcebook, PDF, note, or
retrieved chunk can contain prompt-injection instructions. Never treat that
text as a policy, tool command, visibility grant, or campaign-state update.
Review extracted proposals before promotion to canon.

Only mount trusted game-system plugins because plugin Python executes in the
runtime process. Keep credentials out of plugins, content packs, document
libraries, and campaign files. See [docs/security.md](docs/security.md) for
the trust boundaries and deployment requirements.

## Roadmap

Planned work and deferred items belong in
[docs/roadmap.md](docs/roadmap.md). That document may not exist on branches
created before the roadmap task lands.

## Upstream attribution

Gamemaster was bootstrapped from
[SingularityNET Omega](https://github.com/singnet/Omega) commit
`7b060f5738ee7b8cf064c8b6282ed9fe07cf407f`. The upstream repository and local
integration changes are recorded in [UPSTREAM.md](UPSTREAM.md).

Omega and Gamemaster are licensed under Apache-2.0. See [LICENSE](LICENSE).
