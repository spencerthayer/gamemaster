# Action and Resolution Models

System-agnostic data contracts between Omega / LLM intent interpretation,
the Tabletop Runtime, and `GameSystemPlugin.resolve()`.

These types transport attempted actions and mechanical results. They do not
implement game mechanics, dice rolling, persistence, or narration.

## Ownership flow

```text
natural-language player input
        |
LLM interprets intent
        |
GameAction
        |
GameSystemPlugin.resolve(action, ResolutionContext)
        |
Resolution
        |
runtime applies declared StateChange values and records GameEvent values
        |
LLM narrates the returned mechanical result
```

The LLM may interpret intent and later narrate. It does not become
mechanical truth. Plugin resolution returns structured data. Scene prose
belongs to Omega, not to `Resolution`.

Plugins describe desired changes. Plugins do not mutate authoritative
campaign persistence through `ResolutionContext`. Later runtime phases
apply `state_changes` transactionally.

```text
input state  ->  resolver  ->  declared changes  ->  runtime application
```

not:

```text
plugin mutates the campaign database directly
```

## EntityRef

Lightweight immutable reference to a campaign entity.

| Field | Meaning |
|---|---|
| `id` | Opaque non-empty string. No UUID requirement. |
| `entity_type` | Optional caller label (`npc`, `object`, ...). The runtime never branches on it. |

No character/NPC distinction, no statistics, no system-specific slug rules
beyond a non-empty id. Hashable and serializable.

Examples: `mara`, `ithrazel` with `entity_type="npc"`, `door-17` with
`entity_type="object"`.

## GameAction

An attempted action. The generic core does not decide whether a game
understands the `action_type`.

| Field | Meaning |
|---|---|
| `actor` | Required `EntityRef`. |
| `action_type` | Required non-empty string. Arbitrary, including names no plugin knows yet. |
| `targets` | Zero, one, or many `EntityRef` values. |
| `parameters` | Plugin-owned mapping. The core stores it and does not interpret keys. |

D&D-shaped parameters may later include `weapon_id`. GURPS-shaped
parameters may include `maneuver` and `hit_location`. A narrative action
may include `approach`. Those keys belong to plugins.

Structural validation rejects empty `action_type`, a non-`EntityRef` actor,
and malformed targets. It does not validate action names.

## ResolutionContext

Data supplied to a plugin for one resolve call. Not a service locator.

| Field | Meaning |
|---|---|
| `campaign_id` | Required non-empty string. |
| `system_id` | Required non-empty string. |
| `scene_id` | Optional. |
| `state` | Plugin-visible snapshot. Opaque to the generic runtime. |

`state` is input. It must not include a SQLite connection, registry,
Omega handle, LLM client, or retriever. The plugin reads this snapshot
and returns requested changes on `Resolution`.

The runtime exposes this canonical state tree:

```text
ResolutionContext.state
{
  "campaign": {
    "system": { ... }          # campaign-level system-owned state
  },
  "entities": {
    "<entity-id>": {
      "system": { ... }        # entity system_state, plugin-owned
      "metadata": { ... }      # generic metadata, core-owned shape
    }
  },
  "scene": {
    "system": { ... }          # active scene system-owned state
  }
}
```

A plugin `StateChange.path` must start with `campaign`, `entities`, or
`scene`. The persistence adapter maps `("campaign", "system", ...)` to
campaign system state, `("entities", "<entity-id>", "system", ...)` to
that existing campaign entity's system state, and
`("scene", "system", ...)` to the explicitly selected scene's system state.
A plugin may write only under `system`. Metadata, ownership, provenance,
canon, knowledge, and visibility are core-owned and cannot be changed
through these paths. Plugins never see table or column names.

## StateChange

A desired mutation. The runtime can apply generic operations without
knowing what a path means.

| Field | Meaning |
|---|---|
| `operation` | `StateOperation.SET` or `StateOperation.DELETE`. |
| `path` | Tuple of components (`str` or non-negative `int`). |
| `value` | New value for `SET`. Must be omitted/`None` for `DELETE`. |

`path` is not a dotted string. Entity ids and keys may contain dots, so
`("entities", "cultist.1", "system.hp")` is one unambiguous location.
The runtime must not contain branches such as "if this path ends in
`hp`". Hit points, fatigue, and similar meanings live inside plugins.

The plugin does not supply a previous value. The store owns prior state
and can record it when applying the mutation in later phases.

Opaque mappings on these transport objects (`parameters`, `outcome`,
`state`, `details`, `payload`, and `StateChange.value`) accept only
JSON-safe values: `None`, `str`, `bool`, `int`, finite `float`, nested
string-keyed mappings, and sequences of those values. Sets, datetimes,
file handles, NaN, infinities, and arbitrary objects are rejected at
construction.

## RollResult

Transport for a dice result. Phase 10 owns parsing and rolling.

| Field | Meaning |
|---|---|
| `expression` | The expression that produced the total. |
| `total` | Numeric total (`int` or `float`). |
| `details` | Plugin-owned extras (individual dice, and so on). |

Critical success, advantage, and success margin are not required generic
fields. Dice are optional: a valid `Resolution` may have `rolls == ()`.

## RuleReference

Transport citation for a mechanical source. Phase 22 owns retrieval,
persistence, and precedence.

| Field | Meaning |
|---|---|
| `source_id` | Required source identifier. |
| `title`, `section`, `page`, `document_path`, `chunk_id` | Optional citation fields. |

## GameEvent

A proposed event emitted by resolution. Not a persisted event-store row.

| Field | Meaning |
|---|---|
| `event_type` | Required non-empty string. |
| `payload` | Plugin-owned mapping. |
| `actor`, `target` | Optional `EntityRef` values. |

Sequence numbers, persistent event ids, campaign/session ids, timestamps,
and store offsets belong to Phase 12.

`StateChange` describes a desired mutation. `GameEvent` describes
something that happened. Example: a change that sets a resource from 12
to 5, plus an event `resource_changed`. Later phases may derive one from
the other or store both. Phase 8 keeps them distinct.

## Resolution

Structured mechanical result of `resolve()`.

| Field | Meaning |
|---|---|
| `outcome` | Plugin-owned structured data. No required `success` boolean. |
| `rolls` | Zero or more `RollResult` values. |
| `state_changes` | Desired mutations; not yet applied persistence. |
| `rule_references` | Transport citations. |
| `events` | Proposed events. |
| `status` | `ResolutionStatus`; defaults to `RESOLVED`. |
| `ruling_question` | Required and non-empty iff `status` is `RULING_REQUIRED`; otherwise None. |
| `explanation` | Human-readable account of the mechanical decision. Required for `UNRESOLVED` and `UNSUPPORTED`. |
| `requires_ruling` | Derived property, True only for `RULING_REQUIRED`. Not a field. |

### ResolutionStatus (Phase 9)

A resolve call ends in exactly one of four states. The distinction matters
because all three non-resolved states reach the GM, and they are different
problems.

| Status | Meaning | Presented as |
|---|---|---|
| `RESOLVED` | A deterministic mechanical result exists. | the outcome |
| `RULING_REQUIRED` | Enough information exists; the rules leave a judgment call. | "The rules leave this to GM judgment." |
| `UNRESOLVED` | The mechanic exists; facts, rules, or data are missing. | "Required information is missing." |
| `UNSUPPORTED` | This plugin does not implement the mechanic. | "This system plugin does not implement this." |

Invariants enforced by the dataclass:

- `RULING_REQUIRED` needs a `ruling_question`; every other status forbids one.
- `UNRESOLVED` and `UNSUPPORTED` carry no mechanical result: empty `outcome`,
  no rolls, no state changes, no events. They require an `explanation`.
  `rule_references` stay allowed.
- `RULING_REQUIRED` may carry rolls and outcome data, but no state changes and
  no events: nothing is settled until the GM rules.

A missing mechanic is not permission to invent a mechanic.

`explanation` is not scene narration, LLM prose, or character dialogue.
Example: `"Roll total 14 meets target 12."` Not: `"Your sword bites into
the cultist."`

`outcome` examples that all fit without new core fields:

- D&D-shaped: `{"success": true, "attack_total": 18, "damage": 7}`
- GURPS-shaped: `{"status": "awaiting-response", "response_type": "defense", "margin": 3}`
- Narrative: `{"result": "mixed-success", "cost": "social-obligation"}`

Multi-stage mechanics belong in `outcome` (for example
`status: awaiting-response`). There is no generic `defense_required`
field. `RULING_REQUIRED` is for genuine GM adjudication, not for the next
mechanical step a player or plugin still owns.

## The orchestration guard (Phase 9)

`tabletop.orchestration.turn.resolve_action(registry, action, context)` is
the only supported path to a mechanical result. It looks up the system named
by `context.system_id`, returns `UNSUPPORTED` when that system does not
advertise `Capability.ACTION_RESOLUTION`, and otherwise calls the plugin and
returns its `Resolution` unchanged. It contains no branch that produces a
mechanical result itself. An unknown system id raises `PluginNotFoundError`
(a configuration failure, not a game outcome); a plugin returning a
non-`Resolution` raises `InvalidResolutionError`.

`tabletop.orchestration.adjudication` carries the non-resolved cases across
the boundary: `requires_adjudication()`, `adjudication_request()` building an
`AdjudicationRequest` (status, action, context, detail, rule references, and
a status-specific `headline`), and `AdjudicationResult`, whose `resolution`
must be `RESOLVED` because a ruling ends the question. Phase 9 establishes
when adjudication is reachable; it does not perform adjudication.

## Immutability and serialization

Public models use frozen dataclasses. Mappings are deep-copied on
construction and wrapped in `MappingProxyType`, so later mutation of the
caller's dict does not rewrite the constructed object. Nested values are
checked recursively against the JSON-safe domain described under
StateChange.

`to_dict()` returns JSON-compatible primitives: nested `EntityRef` values
become objects, tuples become lists, mappings become dicts, `None` stays
null-compatible. This is generic Python/JSON transport, not Omega
serialization.

## Errors

Structural failures raise `InvalidActionError` (entity refs and actions)
or `InvalidResolutionError` (context, resolution, rolls, state changes,
events, rule references). Both subclass `GameSystemError`. The generic
core still does not interpret plugin-owned mechanics.
