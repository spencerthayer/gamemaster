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

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# Edit .env: provider keys, OMEGA_AUTH_SECRET, and a distinct channel for the
# GM and for each player process.
export TABLETOP_DATABASE_PATH=/tmp/gamemaster-smoke.sqlite3
python -m tabletop.cli system list
python -m tabletop.cli campaign create --id night --name "Night Watch" --system freeform
python -m tabletop.cli campaign select night
python -m tabletop.cli campaign entity create --id ada --kind character --name Ada
python -m tabletop.cli campaign participant add --id gm1 --name "GM" --role gm
python -m tabletop.cli campaign participant bind --participant gm1 --channel irc --external-id gm1
python -m tabletop.cli campaign validate --id night
python -m tabletop.cli campaign start night --gm --channel irc
```

`campaign start` requires an explicit channel, resolves the participant binding,
passes the selected channel credentials to Compose, and starts the matching
service. Use `campaign stop night --gm` or
`campaign stop night --participant <id>` without a channel.

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
