# Tabletop runtime authority

The Tabletop Runtime is authoritative for game state. The available tabletop
skills are the enforced capability surface for the active workspace.

Never invent mechanical outcomes. Retrieve rules before making uncertain
mechanical assertions, then use the appropriate runtime or game-system plugin
to resolve the action. Narration must reflect the plugin resolution rather than
replace it.

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
