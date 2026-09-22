# Security

This document states the trust model for Gamemaster tabletop code. Claims that
say the code enforces a boundary are covered by
`tests/tabletop/test_security_boundaries.py`. Deployment guarantees that the
process cannot enforce alone are labeled as deployment requirements.

## Prompt injection from ingested documents

Ingested sourcebooks, notes, and other library files are untrusted text. They
may contain instructions aimed at the model. Treat every retrieved chunk as
data to cite or summarize, never as an instruction channel that can change
tool policy, visibility, or campaign authority. Retrieval can surface hostile
text; it must not grant that text authority over runtime behavior.

## Sourcebooks as untrusted data

Published rules and setting material enter the system as documents and content
packs. Content packs are data only: `content-pack.yaml` is validated strictly,
unknown fields are rejected, and an `entrypoint` field is rejected so a pack
cannot declare executable code. Loading a content pack never imports Python
from the pack directory.

## Plugin trust boundaries

Executable code loads only from configured plugin roots (`TABLETOP_PLUGIN_PATH`
and the built-in `<repo>/systems` root when present).
`discover_plugins(roots)` scans immediate children of those roots for
`plugin.yaml` and never executes plugin Python. `load_plugin` is the only
import point.

Document roots and content-pack directories are configured separately from
plugin roots. A document root is never searched for plugin entrypoints. A
`plugin.yaml` planted under a raw document tree is ignored unless that path is
explicitly listed as a plugin root.

## Constrained discovery

Plugin discovery is immediate-children-only. Nested directories are not
scanned. Symlink escapes are rejected: a discovered plugin directory must
resolve to a direct descendant of its configured root. Manifest entrypoints
must be `module:ClassName` with identifier characters only; absolute paths and
`..` traversal forms are rejected.

## Path traversal rejection

Path containment is enforced for:

- **Document library:** `DocumentLibrary.lookup` rejects absolute paths,
  lexical traversal that leaves the raw or processed root, and symlinks that
  resolve outside either root.
- **Content packs:** `gm_only` paths must be relative. Absolute paths are
  rejected even when they point inside the pack. Traversal and symlink escapes
  that leave the pack directory are rejected.
- **Plugin discovery:** children that resolve outside the configured root
  (including via `..` symlinks) are rejected.

## Secrets outside content

Do not store API keys, provider credentials, or other secrets inside content
packs, document libraries, or plugin trees meant for distribution. Secrets
belong in the deployment environment (for example process environment
variables), not in mounted lore or rules directories. The deterministic
importer module graph contains no credential or network client module.

## Extraction versus import

Extraction (model-produced proposals) and import (deterministic persistence)
are separate trust boundaries. Model-produced extraction is always untrusted
proposal data. The importer validates envelopes, rejects individual proposals
that fail policy, and inserts survivors with parameterized SQL. Imported facts
enter as non-canon until a separate promotion step.

Ingested document text is stored as values bound to SQL parameters. Static
analysis of the Markdown and PDF ingestion modules and the importer rejects
`eval`/`exec`/`compile`, and rejects SQL built with f-strings, `%`
interpolation, concatenation, or `str.format` in those modules.

## Read-only raw mounts

Raw document roots are immutable by API convention: `DocumentLibrary` exposes
lookup only and has no raw mutation method. Under Docker or Portainer
deployment, mount raw library and plugin directories read-only so the host
filesystem, not only application code, enforces that boundary. That mount
policy is a deployment requirement delivered with the compose stack (task 46).

## No docker socket mount

The deployment must not mount the Docker socket into the Gamemaster container.
Socket absence is a deployment requirement for the compose stack (task 46).
This repository does not yet ship that compose file; do not assume it exists
until task 46 adds it.
