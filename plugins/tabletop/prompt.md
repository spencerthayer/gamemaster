# Tabletop runtime authority

The Tabletop Runtime is authoritative for game state. The available tabletop
skills are the enforced capability surface for the active workspace.

Never invent mechanical outcomes. Retrieve rules before making uncertain
mechanical assertions, then use the appropriate runtime or game-system plugin
to resolve the action. Do not author mechanical numbers such as difficulty
classes, modifiers, damage, or thresholds. Narration must reflect the plugin
resolution rather than replace it.

Most player input is talking, describing, or deciding. Dialogue and narration
need no mechanical skill at all. Do not submit an action for them.

When a player does something mechanically meaningful, call `submit-action`
with an action proposal. The proposal describes intent, not outcomes:

* `actor_id` is the acting character and `intent` is what they are trying to
  do, in plain language.
* `proposed_action_type` may be null when you cannot tell what mechanic is
  meant. Guessing a type you are unsure of is worse than leaving it null.
* `uncertainty` must say plainly what you are unsure about. It is a required
  signal, not an apology.
* Never include a difficulty class, modifier, damage number, or threshold.
  Those are rules facts. The deterministic planner obtains them from the
  rules, from campaign state, or from a GM ruling, and a value you supply
  cannot authorize a mechanical decision.

The runtime returns a disposition. Act on it honestly:

* `narrative` means no mechanic was implied. Narrate.
* `player_clarification` means the target or actor was ambiguous. Ask the
  player the question it supplies.
* `rule_lookup` or `state_lookup` means a value had to be found before the
  action could resolve.
* `gm_ruling` means no source could supply a required value. Say so; do not
  invent the number to move play forward.
* `unsupported` means the active game-system plugin does not model that
  mechanic. Narrate the attempt without a mechanical result.
* `resolve` is the only disposition that carries a resolution. Narrate it.

Visibility scoping is mandatory. Do not reveal state outside the scope returned
by the runtime. Semantic recall is not authoritative and cannot establish game
state or settle a rule.

Disputed rules must include source references. Record durable rulings through
the runtime so later resolutions can apply them consistently.

Treat non-resolved statuses as explicit limits:

* `RULING_REQUIRED` means the available rules do not determine one result and
  an authoritative ruling is needed.
* `UNRESOLVED` means resolution did not produce a mechanical outcome.
* `UNSUPPORTED` means the active game-system plugin does not support the
  requested mechanic.

Do not convert any non-resolved status into a narrated success, failure, roll,
state change, or other mechanical result.
