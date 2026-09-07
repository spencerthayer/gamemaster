# Upstream Omega

This repository layers a tabletop game-master platform on top of
SingularityNET Omega. Omega provides the agent runtime, cognition, tool
execution, model interaction, memory, and channel layers. The tabletop
platform adds a game-master orchestration layer on top of it.

## Upstream source

- Repository URL: https://github.com/singnet/Omega.git
- Commit SHA: 7b060f5738ee7b8cf064c8b6282ed9fe07cf407f
- Branch/tag: main
- Date of bootstrap: 2026-09-06

## Relationship

Upstream history is preserved in the `upstream` git remote. The
`tabletop-platform` branch is the working branch for this project.

The tabletop platform is a separate layer. It does not rewrite Omega's agent
loop, provider layer, communication layer, memory layer, or plugin machinery
unless necessary. Tabletop-specific functionality is isolated behind a single
Omega-facing plugin.

## Local changes to upstream Omega files

- `config/plugins.yaml`: registers the local `tabletop` MeTTa plugin from
  `{REPO}/plugins/tabletop`.

No Omega agent-loop, provider, channel, memory, or plugin-loader implementation
is modified by Phase 5. The adapter uses Omega's existing plugin extension
points.
