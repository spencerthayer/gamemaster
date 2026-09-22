# GURPS mechanics validation

This audit tests whether the game-system plugin API can represent GURPS
mechanics without adding D&D assumptions to the generic runtime. It checks
the API contract as it exists on the `phase-26-28-reference-systems` branch.
It does not claim that a GURPS plugin has been implemented.

## Result

The existing API can carry every mechanic in this audit. No required API
change was found.

The important result is the roll-under test. Nothing in `tabletop/api/`
compares a roll with a target, defines success as a high roll, or requires a
generic success field. `RollResult` transports only an expression, a numeric
total, and plugin-owned details
(`tabletop/api/resolution.py:145-180`). `Resolution.outcome` is also
plugin-owned and expressly does not require success, failure, damage, or
margin keys (`tabletop/api/resolution.py:222-251`). A GURPS plugin therefore
owns the `roll_total <= effective_skill` comparison and the interpretation
of critical results and margin.

The higher-is-better comparisons used by the D&D reference system are
confined to that plugin. Ability checks compare `roll.total >= dc`
(`systems/dnd5e/__init__.py:184`), saving throws use the same comparison
(`systems/dnd5e/__init__.py:220`), and attacks compare the roll with armor
class (`systems/dnd5e/__init__.py:276`). Those expressions are not in
`tabletop/api/` and do not constrain another plugin.

## Mechanic audit

### 3d6 roll-under resolution

Existing carriers:

- `GameAction.action_type` identifies a skill, attribute, defense, or other
  check. Plugin-owned `GameAction.parameters` can carry the selected skill,
  effective target number, modifiers, and other check inputs
  (`tabletop/api/actions.py:21-50`).
- `tabletop.dice.parser.parse()` accepts `3d6`, and
  `tabletop.dice.roller.roll()` returns the three individual dice and their
  sum without assigning success semantics
  (`tabletop/dice/parser.py:12-17`,
  `tabletop/dice/roller.py:15-34`).
- `RollResult` carries the expression, total, and plugin-owned details.
  `Resolution.outcome` can carry the effective skill, success or failure,
  margin, and critical category.

The API makes no higher-is-better assumption. The GURPS plugin must perform
the roll-under comparison itself.

Gap: none.

### Active defenses

Existing carriers:

- An attack can return a plugin-owned outcome such as
  `{"status": "awaiting-response", "response_type": "active-defense"}`.
  The resolution contract explicitly leaves outcome keys to the plugin
  (`tabletop/api/resolution.py:222-251`).
- The defender's follow-up can be another `GameAction`, with the defender as
  `actor`, the attacker as a target, and dodge, parry, or block data in
  `action_type` and `parameters`.
- Multiple stages do not require `RULING_REQUIRED`. That status is reserved
  for genuine GM judgment. A pending mechanical response belongs in the
  plugin-owned outcome, as documented in `docs/action-resolution.md`.
- State changes and events can apply injury only after the defense sequence
  has produced a settled result.

The plugin must define the correlation data passed between the attack and
defense actions. That data is system-owned and fits in outcome and action
parameters.

Gap: none.

### Maneuvers

Existing carriers:

- `GameAction.action_type` or `parameters` can declare the chosen maneuver.
- `ResolutionContext.state` can hold the actor's current maneuver and
  turn-scoped effects.
- `StateChange` can set or clear that state using generic `SET` and `DELETE`
  operations (`tabletop/api/resolution.py:37-128`).
- `Resolution.outcome` can report maneuver-specific restrictions or results.

The generic runtime does not enumerate actions or assume D&D action economy.

Gap: none.

### Skill defaults

Existing carriers:

- Plugin-owned character or entity schemas can describe attributes, learned
  skills, relative levels, and default relationships
  (`tabletop/api/plugin.py:163-177`).
- `ResolutionContext.state` supplies those values to the resolver.
- `GameAction.parameters` can identify the attempted skill and any selected
  default.
- `Resolution.outcome` can record the source skill or attribute, default
  penalty, effective skill, and resulting margin.
- `RuleReference` can cite the rule or skill definition used for the default
  (`tabletop/api/rules.py:17-39`).

Default selection and effective-skill calculation remain plugin logic.

Gap: none.

### Hit locations

Existing carriers:

- `Capability.HIT_LOCATIONS` advertises that a plugin implements this
  mechanic (`tabletop/api/capabilities.py:15-30`).
- `GameAction.parameters` can carry an aimed or randomly selected location.
- `RollResult` can carry a random-location roll.
- `Resolution.outcome` can carry the resolved location, attack penalty, and
  wound effects.
- `StateChange` can update location-specific injury or conditions.

No generic field limits targets to whole entities or assigns D&D armor
class semantics.

Gap: none.

### Damage types

Existing carriers:

- `Capability.DAMAGE` advertises damage support.
- `GameAction.parameters` can carry the basic damage expression and damage
  type.
- `Resolution.outcome` can carry basic damage, penetrating damage, injury
  multiplier, final injury, and type-specific effects.
- Plugin-owned state and schemas can describe weapon and attack profiles.
- `GameEvent.payload` can record typed damage without the runtime
  interpreting it (`tabletop/api/events.py:18-49`).

The core has no fixed list of damage types and no generic damage formula.

Gap: none.

### Damage resistance

Existing carriers:

- Plugin-owned entity state can describe general, location-specific,
  ablative, semi-ablative, hardened, or layered DR as needed.
- `ResolutionContext.state` exposes that state during resolution.
- `GameAction.parameters` and `Resolution.outcome` can carry armor divisors,
  applied DR, penetration, and final injury.
- `StateChange` can update mutable DR or armor state.

DR is not treated as D&D armor class. The D&D reference plugin's armor-class
comparison is local to that plugin.

Gap: none.

### Hit points and fatigue points

Existing carriers:

- Plugin-owned schemas and state can store HP and FP as separate values.
- `Capability.RESOURCE_TRACKING`, `Capability.DAMAGE`, and
  `Capability.HEALING` advertise the implemented behavior without defining
  either resource.
- `StateChange` can set HP and FP independently, including negative values.
  Its value contract accepts JSON-safe integers and imposes no zero floor
  (`tabletop/api/resolution.py:80-128`).
- `Resolution.outcome` and `GameEvent.payload` can report threshold effects,
  injury, fatigue loss, and recovery.

The generic API does not force HP and FP into one resource and does not clamp
them to D&D-style bounds. Any clamp in the D&D reference plugin is local to
that plugin (`systems/dnd5e/__init__.py:309`).

Gap: none.

### Advantages and disadvantages

Existing carriers:

- Plugin-owned character or entity schemas can describe traits, levels,
  modifiers, self-control numbers, and associated metadata.
- `ResolutionContext.state` provides traits to resolution.
- `GameAction.parameters` can identify invoked or resisted traits.
- `Resolution.outcome`, `StateChange`, and `GameEvent` can carry trait
  effects and changes.

The similarly named D&D advantage and disadvantage dice mechanic is not an
API concept. It appears only in the D&D plugin's private expression builder
(`systems/dnd5e/__init__.py:669-680`).

Gap: none.

### Contests of skill

Existing carriers:

- `Capability.OPPOSED_RESOLUTION` advertises contest support
  (`tabletop/api/capabilities.py:15-30`).
- One `GameAction` can name the initiating actor and one or more opposing
  entities through `targets`.
- `ResolutionContext.state` exposes every participant's system-owned skill
  data.
- `Resolution.rolls` accepts multiple `RollResult` values.
- `Resolution.outcome` can carry each effective skill, roll, margin, winner,
  tie, and the state of a repeated regular contest.
- A later contest exchange can be another action. Plugin-owned outcome and
  state can preserve any system-specific progress between exchanges.

The API neither assumes a single roll nor defines the highest total as the
winner.

Gap: none.

### Encumbrance

Existing carriers:

- `Capability.EQUIPMENT` and `Capability.RESOURCE_TRACKING` can advertise
  the relevant implemented surfaces.
- Plugin-owned schemas and state can represent carried equipment, weights,
  Basic Lift, encumbrance level, Move, and Dodge.
- `ResolutionContext.state` supplies those values to action resolution.
- `Resolution.outcome` can expose the selected encumbrance band and derived
  penalties.
- `StateChange` can update carried equipment and cached derived values when
  inventory changes.

The API has no fixed unit, inventory shape, movement formula, or D&D carrying
capacity rule.

Gap: none.

## Required API changes

None. This audit found no GURPS mechanic that requires a change to
`tabletop/api/`.

This conclusion is limited to representability by the current contract. A
future GURPS plugin still must define its schemas, action vocabulary,
multi-stage active-defense protocol, calculations, state paths, and outcome
shapes. Those are plugin implementation requirements, not gaps in the
generic API.
