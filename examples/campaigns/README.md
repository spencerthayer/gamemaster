# Campaign directory projections

Campaign directories are generated, human-readable views of campaign state.
SQLite remains authoritative. Regenerate these files instead of editing them.

Each YAML file starts with a generated-artifact header containing the campaign
ID and source event sequence.

The layout is:

- `campaign.yaml` contains campaign identity and the source sequence.
- `state/` contains plugin system state for the campaign, entities, and scenes.
  It never contains fact text.
- `world/` contains known facts that are not restricted to the GM.
- `rulings/` is reserved for projected rulings and remains present when empty.
- `sessions/` is reserved for projected sessions and remains present when empty.
- `gm/` contains unrevealed or GM-only facts and open threads. Content in this
  directory must not appear elsewhere in the projection.

Projection writes use temporary files and atomic replacement so an interrupted
write cannot truncate an existing file. Rebuilding the same source projection
produces byte-identical output.

## Describing a campaign that does not exist yet

These files are generated projections. To *describe* a new campaign, use a
setup manifest instead; see `setup/README.md`. The two are deliberately named
differently: `campaign.yaml` is a projection produced from SQLite, and
`campaign.setup.yaml` is a hand-authored input read to create one. Overloading
one filename would give it two meanings in the same directory tree.
