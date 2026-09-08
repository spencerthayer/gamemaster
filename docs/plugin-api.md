# Game System Plugin API

The contract every game-system plugin implements so the Tabletop Runtime can
use it without knowing anything about that game's mechanics.

## Purpose

The runtime orchestrates campaigns; game systems own mechanics. A plugin
answers the question: given a declared action and resolution context, what
deterministically happens? The runtime never learns what armor class or hit
points mean.

## Boundary

Game-system plugins are pure Python. They import `tabletop.api` only. They
never import Omega, MeTTa, channels, providers, or any runtime
implementation module. The Omega-facing adapter is the only Omega-aware
code, and it lives outside the runtime and the plugins.

## API version

`TABLETOP_PLUGIN_API_VERSION = "tabletop/v1"`, defined once in
`tabletop/api/plugin.py`. Compatibility is exact-match in Phase 6; Phase 7
enforces it during discovery. Plugin release version and API version are
separate concepts: a plugin at version 0.3.0 still implements
`tabletop/v1`.

## Identity

`GameSystemInfo` carries `id` (machine-readable lowercase slug), `name`
(human-readable), `api_version`, optional `version` (the plugin's own
release), and optional `description`. No filesystem paths, no manifest
data. Example:

```python
GameSystemInfo(
    id="gurps4e",
    name="GURPS 4e",
    api_version=TABLETOP_PLUGIN_API_VERSION,
    description="Structural direction, not an implemented system",
)
```

## Capabilities

`Capability` is a string-backed enum in `tabletop/api/capabilities.py`:
`dice`, `action-resolution`, `opposed-resolution`, `turn-order`, `damage`,
`healing`, `conditions`, `resource-tracking`, `equipment`, `magic`,
`character-advancement`, `hit-locations`, `social-conflict`.

Capabilities advertise **what this plugin currently implements**, never what
the underlying game conceptually supports. A D&D 5e plugin does not
advertise `dice` because D&D uses dice; it advertises `dice` only when its
dice mechanics exist. Examples:

- D&D 5e plugin (hypothetical, post-Phase-27): `dice`, `action-resolution`,
  `turn-order`, `damage`, `conditions`
- GURPS 4e plugin (hypothetical): `dice`, `action-resolution`,
  `opposed-resolution`, `hit-locations`
- Freeform plugin (implemented today): `frozenset()` — no mechanics exist yet

Negotiation:

```python
plugin.supports(Capability.DICE)          # -> bool
plugin.require_capability(Capability.DICE)  # raises UnsupportedCapabilityError
```

`UnsupportedCapabilityError` carries `system_id` and `capability` as plain
serializable fields.

## Lifecycle

`initialize()` and `shutdown()` are safe no-ops by default. Phase 6 has no
need for an initialization context object, so `initialize()` takes no
arguments; a narrow context will be introduced through an explicit
interface only when a real need exists. Plugins never receive the Omega
instance, LLM clients, database connections, or the global runtime.

## Resolution

`resolve(action, context)` is the generic resolution entry point.
`GameAction`, `ResolutionContext`, and `Resolution` are owned by Phase 8 and
referenced by forward annotation. A plugin that cannot resolve
deterministically must yield to GM adjudication (Phase 9) rather than
fabricate an outcome.

## Schemas

`character_schema()`, `entity_schema(entity_type)`, and `state_schema()`
return generic mappings describing system-owned state. They are **opaque to
the generic runtime** except for validation, display, and storage. D&D can
describe classes, AC, and HP later; GURPS can describe ST, DX, IQ, HT,
advantages, and skills; a narrative game can expose almost nothing. All
schemas default to `{}`.

## Validation

`validate_state(state)` returns a `ValidationResult` (with structured
`ValidationIssue` entries: `path`, `code`, `message`), never a bare bool.
Expected invalid game data is reported here. Exceptions
(`InvalidPluginConfigurationError`, `PluginInitializationError`,
`StateValidationError`, `UnsupportedCapabilityError`, all under
`GameSystemError`) are reserved for integration contract failures.

## Discovery

Nothing in Phase 6 loads plugins dynamically. Manifest parsing, directory
scanning, and the registry belong to Phase 7. Tests instantiate plugin
classes directly.

## Security

Game-system plugins are executable, explicitly trusted code, discovered
from configured directories in later phases. Content packs are separate,
data-only artifacts and never execute. Ingested documents never execute.

## Architecture falsification notes

- **D&D 5e**: attack rolls, saving throws, AC, HP, and spell slots all fit
  inside a plugin's `resolve()` and its opaque schemas; none appear in the
  base class. Verified: the base class and this document contain no such
  term outside these notes.
- **GURPS 4e**: 3d6 roll-under, active defenses, DR, hit locations, FP, and
  maneuvers are plugin-internal. `opposed-resolution` and `hit-locations`
  capabilities exist as advertised identifiers without semantics.
- **Dying Earth / social systems**: `social-conflict` is a first-class
  capability; a plugin may implement it without any combat capability.
- **Diceless games**: a plugin advertising `frozenset()` (like freeform
  today) satisfies the full contract; `resolve()` needs no dice.
- **Rules-light games**: schemas default to `{}` and validation defaults to
  accepting, so a plugin may carry almost no structured state.

## Installation and discovery (Phase 7)

### Manifest format

Each plugin directory contains a `plugin.yaml`:

```yaml
id: gurps4e
name: GURPS 4e
api_version: tabletop/v1
version: 0.1.0
entrypoint: gurps4e:Gurps4ePlugin
description: Optional free text
```

Allowed keys: `id`, `name`, `api_version`, `entrypoint`, `version`,
`description`. Unknown keys are rejected (typos must fail loudly, not
silence). `entrypoint` is strictly `module.path:ClassName`. The manifest is
installation metadata only; capabilities always come from the loaded
plugin's `capabilities()` method, never from YAML.

### Directory layout

```text
/tabletop/plugins/example-system/     # one directory per plugin
├── plugin.yaml
└── example_system/
    ├── __init__.py
    └── plugin.py
```

Built-in systems (`systems/freeform`, `systems/dnd5e`) use the same layout
with the plugin directory itself as the Python package. Built-ins and
external plugins go through exactly the same discovery, validation, and
loading pipeline; nothing special-cases a built-in.

### Plugin roots and precedence

1. Roots supplied explicitly to the runtime
2. `TABLETOP_PLUGIN_PATH` environment variable, `os.pathsep`-separated
3. Built-in `<repo>/systems`

Explicit and environment-configured roots that do not exist fail startup:
a typo in a plugin mount must not silently disable a game system. The
built-in root is appended automatically and only when present.

### Discovery, loading, and the trust model

`discover_plugins()` inspects only immediate child directories of
configured roots and never executes plugin code. A directory without
`plugin.yaml` is ignored; a malformed manifest fails closed. `load_plugin()`
is the only point where plugin Python runs: API-version compatibility is
checked before import, the loaded object must be a `GameSystemPlugin`
instance, its identity must agree with its manifest (`id`, `name`,
`api_version`, and `version` when present), and `initialize()` runs before
registration. Duplicate system ids fail closed; there is no implicit
precedence between roots.

Discovery roots are the only executable trust boundaries. Content packs,
source documents, campaign data, and uploads are never scanned and never
execute. Game-system plugins are trusted code; content packs are data.

### Lifecycle and limitations

Plugins load once at runtime startup; adding or removing one requires a
restart (no hot reload). `shutdown_all()` attempts every plugin's
`shutdown()` even when some fail, and reports failures rather than
swallowing them. Plugin top-level module names must be unique within one
process (Python module cache). Third-party plugin Python dependencies must
already exist in the runtime environment; dependency installation is not
solved until the deployment phase. Installation itself is an operator
action (mounting or copying a trusted plugin directory); there is no
install/uninstall/marketplace mechanism.
