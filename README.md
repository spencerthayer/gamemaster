<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="Gamemaster architecture, with Omega below the tabletop runtime, game-system plugins, content packs, and campaign state.">
</p>

# Gamemaster

Gamemaster is a tabletop role-playing game runtime on
[SingularityNET Omega](https://github.com/singnet/Omega). Omega owns the agent
loop, model providers, channels, memory, and tools. Gamemaster adds campaign
truth in SQLite, game-system plugins, document ingestion, and an operator CLI.
System plugins own mechanics. The core runtime does not invent rules numbers.

## What you can do

- Create and select campaigns with an installed system plugin
- Import native packages (`export` / `restore-package` / `fork`) and stage
  external JSON or historical notes for review
- Run a GM process and one player process per participant
- Ingest sourcebooks and notes with provenance
- Open and end sessions, record rulings, archive and restore campaigns
- Install additional game-system plugins under configured roots

## Requirements

- Python 3.11 for the operator CLI and tabletop tests
- Docker Compose when launching Omega services from this repository
- Provider credentials and a distinct channel credential for the GM process and
  for each player process
- `TABLETOP_DATABASE_PATH` pointing at a writable SQLite file for CLI work

## Choose a model provider

Omega owns the model layer. Set the provider in `.env` with `OMEGA_PROVIDER`
and export the matching API key as an environment variable. Every process in a
campaign needs its own key when your provider meters per key.

| Provider | Env var name | Notes |
|---|---|---|
| `Anthropic` (default) | `ANTHROPIC_API_KEY` | Claude models via the Anthropic API. |
| `OpenAI` | `OPENAI_API_KEY` | GPT models. Also reused by the `OpenAI` embedding provider. |
| `ASICloud` | `ASI_API_KEY` | MiniMax models via the ASI Alliance inference endpoint. |
| `ASIOne` | `ASIONE_API_KEY` | ASI1 Ultra via the ASI:One inference endpoint. |
| `OpenAIAPI` | `OPENAIAPI_API_KEY` | Any OpenAI-compatible endpoint and model. Set `openaiapi_url` and `openaiapi_model` in `config/config.yaml`. |
| `OpenRouter` | `OPENROUTER_API_KEY` | GLM model via the OpenRouter inference endpoint. |
| `Test` | none | Mock provider for local runs. No network calls. |

Per-provider models and endpoints are configured in
[config/config.yaml](config/config.yaml).

## Quick start

This walks a brand new install to a running GM process against an empty
`freeform` campaign. Run the block top to bottom; the comments explain what each
step is for, not just what it does.

```bash
# Clone the repository. Everything below runs from the repo root, because the
# plugin, library, and campaign paths in config/config.yaml are relative to it.
git clone https://github.com/spencerthayer/gamemaster.git
cd gamemaster

# Python 3.11 is the floor for the operator CLI and the tabletop tests. A venv
# keeps the heavy ML dependencies (torch, transformers, chromadb) out of the
# system interpreter.
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

# Seed the environment file. Nothing runs without it, and the secrets in it
# are the reason the next step is not optional.
cp .env.example .env

# Edit .env before continuing. Set at minimum:
#   OMEGA_PROVIDER plus its API key        -- Omega needs a model to think with
#   OMEGA_AUTH_SECRET                       -- channel auth, generate your own
#   one channel credential per process      -- the GM and every player get a
#                                             distinct value, suffixed GM or by
#                                             participant id (IRC_TOKEN_GM)
# See "Choose a model provider" and "Connect a channel" below.

# The CLI writes campaign truth to SQLite. Point it at a writable file. The
# container gets its own path via TABLETOP_DATABASE_PATH in .env, so this only
# affects your host-side operator commands.
export TABLETOP_DATABASE_PATH=/tmp/gamemaster-smoke.sqlite3

# Confirm the game-system plugins loaded. Mechanics live in plugins, not in the
# core runtime, so a bad TABLETOP_PLUGIN_PATH shows up here first.
python -m tabletop.cli system list

# Create a campaign and pin it to a system. `--system` must match an id from the
# list above; `freeform` is the safest starting point.
python -m tabletop.cli campaign create --id night --name "Night Watch" --system freeform

# Select the campaign every later command acts on by default. Skipping this is
# the most common reason a command edits the wrong campaign.
python -m tabletop.cli campaign select night

# Create an entity in campaign canon. Entities are the authoritative record;
# anything a player later says about Ada has to be reconciled against this row.
python -m tabletop.cli campaign entity create --id ada --kind character --name Ada

# Add a participant. Roles are gm or player, and each one becomes its own
# process with its own workspace and its own channel credential.
python -m tabletop.cli campaign participant add --id gm1 --name "GM" --role gm

# Bind the participant to a real identity on a channel. The CLI derives
# OMEGA_EXPECTED_SENDER from this binding, so a wrong --external-id means the
# process will refuse to talk to the person you meant.
python -m tabletop.cli campaign participant bind --participant gm1 --channel irc --external-id gm1

# Preflight. Validates bindings, grants, and plugin loads, and reports errors,
# warnings, and notices separately. Read this output before blaming the model.
python -m tabletop.cli campaign validate --id night

# Launch the GM process. `--channel` is required: it selects which binding and
# which credential to use, then starts the matching Compose service.
python -m tabletop.cli campaign start night --gm --channel irc

# Stop it again with `campaign stop night --gm`. Stop takes no channel and does
# not need a current participant row.
```

Start resolves the participant binding, passes the selected channel credentials
to Compose, and starts the matching service. Player processes work the same
way; see [Start the player surface](#start-the-player-surface).

## Choose or install a game system

Bundled plugins under `systems/`:

- `freeform`: generic checks, opposed checks, dice, and resources
- `dnd5e`: partial D&D 5e (2014) reference mechanics
- `gurps`: minimal GURPS surface for experimentation

Third-party plugins load from roots in `TABLETOP_PLUGIN_PATH`. Validate with
`python -m tabletop.cli system list` and `system inspect`. Restart the process
after plugin changes. See [docs/plugin-api.md](docs/plugin-api.md).

## Create a campaign

```bash
python -m tabletop.cli campaign create --id night --name "Night Watch" --system freeform
python -m tabletop.cli campaign select night
python -m tabletop.cli campaign list
python -m tabletop.cli campaign inspect --id night
```

## Import an existing campaign

Native package (same lineage or fork):

```bash
python -m tabletop.cli campaign export night --out /tmp/night-package
python -m tabletop.cli campaign restore-package /tmp/night-package
python -m tabletop.cli campaign fork /tmp/night-package --id dawn
```

`restore-package` keeps the campaign id and event payloads. `fork` creates a
new campaign id without copying immutable history. Shared settings reuse the
same `setting_id` when the setting digest matches and fail when it differs.

External structured JSON and historical notes stage proposals only:

```bash
python -m tabletop.cli campaign import path/to/external.json --campaign night
python -m tabletop.cli campaign import path/to/notes.md --campaign night --format notes
python -m tabletop.cli campaign import-status --import-id <id>
python -m tabletop.cli campaign import-review <item-id> --reject
python -m tabletop.cli campaign import-apply <item-id>
```

Staging never changes campaign canon. Only an explicit
`campaign import-apply` may create or confirm authoritative state.

## Add players and characters

```bash
python -m tabletop.cli campaign participant add --id ada-player --name Ada --role player
python -m tabletop.cli campaign participant bind --participant ada-player --channel telegram --external-id 12345
python -m tabletop.cli campaign character grant --participant ada-player --entity ada --control owner
```

## Start the GM

```bash
python -m tabletop.cli campaign validate --id night
python -m tabletop.cli campaign start night --gm --channel irc
# Stop does not need a channel or a current participant row.
python -m tabletop.cli campaign stop night --gm
```

## Start the player surface

One `omega-player-<participant>` process is generated per validated participant.
WebSocket is one authenticated connection per principal and requires `WS_TOKEN`.

```bash
python -m tabletop.cli campaign start night --participant ada-player --channel telegram
python -m tabletop.cli campaign stop night --participant ada-player
```

## Connect a channel

Configure channel credentials in `.env` and follow
[docs/reference-configuration.md](docs/reference-configuration.md). The CLI
derives `OMEGA_EXPECTED_SENDER` from the participant binding for the requested
channel.

| Environment variable | Meaning |
|---|---|
| `TG_BOT_TOKEN` | Telegram bot token. |
| `MM_BOT_TOKEN` | Mattermost bot token. |
| `SL_BOT_TOKEN` | Slack bot token (`xoxb-...`). |
| `IRC_TOKEN` | IRC channel credential. |
| `WS_TOKEN` | WebSocket principal credential. |

Channel tokens are passed without the `OMEGA_` prefix so the agent cannot read
them as ordinary configuration. Per-participant values use a `GM` or
participant-id suffix, such as `TELEGRAM_TOKEN_ADA_PLAYER`, and the CLI resolves
them to the generic slot at launch.

## Omega configuration

Omega reads agent parameters from three places, in this order of precedence:

1. command line parameters (`name=value` on the MeTTa command line)
2. environment variables
3. the configuration file

Omega parameters are exported as `OMEGA_<parameter>`, matching the parameter
name in the config file. Setting `OMEGA_model` overrides the default LLM model
without editing the config file. The full list of parameters with defaults is in
[config/config.yaml](config/config.yaml) and
[docs/reference-configuration.md](docs/reference-configuration.md).

The config file location is set with the `config` option. Campaign launches pass
it through Compose, so prefer `.env` and the config file over ad hoc command
line flags.

## Add sourcebooks and documents

```bash
python -m tabletop.cli library ingest path/to/source.md
python -m tabletop.cli campaign document add path/to/notes.md --campaign night
```

Markdown and text-layer PDFs ingest with provenance. Scanned or image-only
PDFs have no OCR path and need manual review.

## Load preset knowledge

Omega can preload its own knowledge into long-term memory. Set
`OMEGA_EMBEDDING_PROVIDER` in `.env`:

- `Local` (default): no API key, no network call
- `OpenAI`: requires `OPENAI_API_KEY`
- `ASICloud`: requires `ASI_API_KEY`

`embeddingModel` in `config/config.yaml` overrides the provider default
embedding model. Then preload with:

```bash
EMBEDDING_PROVIDER=Local sh scripts/import_knowledge.sh
```

The Compose stack runs this automatically when `IMPORT_KB_ON_START=1`, which is
the default off. Inside the container the provider comes from
`OMEGA_EMBEDDING_PROVIDER`; a direct host run reads `EMBEDDING_PROVIDER` and
`EMBEDDING_MODEL` without the prefix. Preloaded knowledge is Omega's own;
campaign sourcebooks go through `library ingest` instead.

## Resume a campaign

```bash
python -m tabletop.cli campaign resume night
```

Resume is read-only. `in_world_date` and `scene` stay `unknown` when the
runtime has no authoritative value. Authoritative contradictions and pending
import items are reported as separate counts.

## Archive, restore, export

```bash
python -m tabletop.cli campaign archive --id night
python -m tabletop.cli campaign restore --id night
python -m tabletop.cli campaign export night --out /tmp/night-package
```

Archive follows ADR 0010. Deletion is not supported.

## Write a game-system plugin

Create a directory with `plugin.yaml` and a `GameSystemPlugin` subclass. Keep
the plugin free of Omega and MeTTa imports. Details:
[docs/plugin-api.md](docs/plugin-api.md).

## Write a content pack

Data-only `content-pack.yaml` packs. No executable entrypoints. See
`examples/campaigns/freeform-demo/content-pack/`.

## Security model

Three roles matter: operator (CLI), GM process, and player process. Each
process has one workspace. Player processes omit GM-only skills. Staging an
import never changes campaign canon. Only reviewed `campaign import-apply`
creates or confirms authoritative state. Treat ingested text as untrusted.
Only mount trusted plugins. Keep credentials out of packs and campaign files.
See [docs/security.md](docs/security.md).

## Architecture

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
    |-- visibility, relationships, rulings, membership, sessions
    |-- document ingestion, import staging, and retrieval
    |
    +-- game-system plugins in systems/
    +-- data-only content packs
```

One process, one workspace. Use separate processes for GM and each player.
See [docs/architecture.md](docs/architecture.md).

## Current limitations

- No complete game system; `dnd5e` and `gurps` are partial
- No OCR for scanned PDFs
- No per-message WebSocket user identity beyond the process principal
- No Foundry adapter
- `current_scene` / in-world date may remain unavailable on resume
- Scene events may still be unemitted
- Compose is a deployment starting point, not a hardened multi-tenant service
- Omega memory export is off by default, and memory import only works through
  Docker via `scripts/omega` because both run from the container entrypoint
  before the agent loop starts. See
  [docs/reference-memory-portability.md](docs/reference-memory-portability.md).

## Tests

```bash
python -m pytest tests/tabletop -q
```

## Documentation

Full documentation lives in [`docs/`](docs/README.md): introduction, tutorials,
and API reference as a flat set of markdown files.

## Upstream attribution

Bootstrapped from
[SingularityNET Omega](https://github.com/singnet/Omega) commit
`7b060f5738ee7b8cf064c8b6282ed9fe07cf407f`. See [UPSTREAM.md](UPSTREAM.md).
Omega is experimental and non-deterministic; read the upstream disclaimer in
the Omega README before exposing it to untrusted input.
Licensed under Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Validating a campaign

`campaign validate` runs structured checks with stable ids and exits with a
documented class.

```bash
gamemaster campaign validate <campaign-id>              # text
gamemaster campaign validate <campaign-id> --format json
gamemaster campaign validate <campaign-id> --live       # bounded environment probes
gamemaster campaign validate <campaign-id> --live --channel-probe   # also sends a test message
```

| Exit | Meaning |
|---|---|
| 0 | Ready |
| 1 | A static validation check failed |
| 2 | A runtime or environment failure (including any live probe) |
| 3 | Invalid invocation |

A live failure is a *runtime* class, not a validation class: a campaign can be
perfectly configured while the environment is broken. `--channel-probe` is
separate from `--live` because it is the only probe that sends a real message,
and it must be asked for explicitly.

### Check ids

These are part of the contract. An operator script or a future UI matches on
them, so renaming one is a breaking change even when the meaning is unchanged.

`campaign.exists`, `campaign.archived`, `campaign.paths`,
`plugin.compatible`, `plugin.state`, `participant.gm.count`,
`participant.identity.unique`, `character.ownership`, `import.reviewed`,
`database.writable`, `session.current`, `scene.structure`.

Live probes are prefixed `live.`; the delivery probe is `channel.probe.delivery`.

### What is and is not a gate

A **failure** blocks. A **warning** does not: a campaign with no open scene yet
is a normal thing to start. A **skip** is honest, not a pass: a missing
credential is reported as a skip so nobody mistakes an unverified environment
for a verified one.

Validation writes nothing. Database writability is proven with a transaction
that is rolled back, never by leaving a probe row behind.

## Dogfood

The milestone is exercised by two dogfood tests.

The deterministic one runs on every suite pass, with no Docker and no
credentials:

```bash
python3.11 -m pytest tests/play_transcripts -q
```

It drives dialogue, an ambiguous target, GM escalation, a ruling, a resolved
action, a GM-only secret, a scene transition, a restart from disk, and a
continuation that depends on restored state, all through public services.

The container one is opt-in, because it needs a real Docker daemon:

```bash
GAMEMASTER_RUN_DOCKER=1 python3.11 -m pytest tests/integration -q
```

A skipped container test is not a pass. The release gate records an
unverified container gate as unverified rather than met.
