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

## Compose deployment

`docker-compose.yml` defines one Omega service with the tabletop plugin. Copy
the documented defaults from `.env.example` into the Portainer stack
environment and replace the example secret and provider credentials before
deployment. The stack builds the repository Dockerfile, persists Omega memory
and tabletop SQLite state in named volumes, and uses environment variables for
every bind-mount source and container path.

The compose file sets `read_only: true` on the plugin and library bind mounts.
This is a compose mount flag; it is not a claim that the underlying host
directories or operating system are otherwise read-only. The campaigns bind
mount is read-write at its mount point. Omega applies a Landlock policy
before plugin load. That policy can allow a normal volume, and it denies
Docker Desktop bind mounts even when those paths are listed. `entrypoint.sh`
therefore copies the plugin, library, and campaigns mounts onto the
container filesystem before the agent starts, and the runtime reads those
copies. The tabletop state path stays a named volume and is listed as
read-write in the Landlock policy.

The service does not mount `/var/run/docker.sock`. Do not add that mount:
access to the Docker socket would give the process control over the Docker
daemon and defeat the container boundary.
