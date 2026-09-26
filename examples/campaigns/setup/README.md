# Campaign setup manifests

`campaign.setup.yaml` describes a campaign that does not exist yet. Reading one
writes nothing, opens no database, and executes nothing it names.

Setup manifests are **inputs**. The `campaign.yaml` files elsewhere under
`examples/campaigns/` are **generated projections**. Keeping the names distinct
avoids two different meanings for one filename in one directory tree.

## The shipped example

```yaml
campaign_id: black-company
name: The Black Company
system_id: freeform

participants:
  - participant_id: gm-1
    display_name: The GM
    role: gm
  - participant_id: player-1
    display_name: Ada
    role: player
    character_ids: [pc-ada]

characters:
  - entity_id: pc-ada
    name: Ada
    participant_id: player-1

content:
  - path: rules/freeform-rules.md
    role: rules

starting_scene:
  scene_id: scene-1
  name: The Flooded Hall
  present: [pc-ada]

game_time:
  in_world_label: Day 3, dusk
```

Roles are `rules`, `setting`, `adventure`, `character`, `notes`, and
`reference`.

## Commands

Dry run first. It prints the plan and writes nothing.

```bash
export TABLETOP_DATABASE_PATH=/path/to/campaigns.db
gamemaster campaign setup --from examples/campaigns/setup/campaign.setup.yaml --dry-run
```

Apply it. You are shown the plan and asked to confirm.

```bash
gamemaster campaign setup --from examples/campaigns/setup/campaign.setup.yaml
```

Non-interactively, with `--yes`:

```bash
gamemaster campaign setup --from campaign.setup.yaml --yes
```

Without `--from`, the wizard asks the same questions and builds the same
manifest object, so an interactive run and a declarative run cannot drift apart.

## What the plan prints

```
plan for black-company:
  [do  ] CREATE campaign black-company  The Black Company on freeform
  [do  ] ADD participant gm-1  The GM (gm)
  [skip] GRANT character pc-ada  already present and identical
  [do  ] OPEN scene scene-1  The Flooded Hall
  [skip] SET game time black-company
3 to create, 2 already satisfied
```

`skip` means the row already exists and matches, so applying it is a no-op.

## Reruns are safe

Running setup twice creates nothing the second time. Every step is checked
before it is written, and a step whose existing row *disagrees* with the
manifest fails rather than overwriting it. Setup configures a campaign; it does
not silently rewrite authoritative data you may have changed by hand.

The database portion of an apply runs in one `BEGIN IMMEDIATE`, so a conflict
part-way through leaves nothing half-configured.

## What setup does not do

Setup configures and validates. It never starts Docker or any other process.
It ends by printing the commands to run next:

```
Next:
  gamemaster campaign start black-company
  gamemaster campaign validate black-company
  gamemaster campaign session start --session-id session-1
```

## Safety rules

- Unknown fields are **rejected**, not ignored. A typo in `system_id` would
  otherwise configure a campaign nobody asked for.
- Content paths are relative to the manifest and must stay inside its
  directory. Absolute paths, `..` traversal, and symlink escapes are refused.
- Executable file types are refused. Setup may name data, never code.
- A credential-shaped key anywhere in the manifest stops the run. A setup file
  belongs in version control, so `api_token` in one is either a mistake or a
  leak. Keep secrets in the environment.
- At most one `gm` participant per campaign.
