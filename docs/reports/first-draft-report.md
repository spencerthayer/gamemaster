# First-draft agent report

## Repository state

This first draft is based on the SingularityNET Omega bootstrap commit
`7b060f5738ee7b8cf064c8b6282ed9fe07cf407f`. The current working branch is
`phase-33-40-verification-release`.

The tabletop work is mostly isolated from Omega behind its plugin extension
points. `entrypoint.sh` is an upstream Omega file that was modified to prepare
the tabletop SQLite directory for the container user and preserve the five
`TABLETOP_*` variables through Omega's startup environment allowlist. That
change, along with the local plugin registration change, is recorded in
`UPSTREAM.md`.

## Research verdict

No code, prompts, templates, or data from the research repositories were
copied into this repository. The research informed requirements and
architectural tradeoffs only. The resulting architecture and implementation
are clean-room work.

## Architecture implemented

The implemented first draft has these major parts:

- A SQLite campaign store for campaigns, entities, facts, relationships,
  sessions, rulings, provenance, and retrieval records.
- An append-only event log for auditable campaign mutations and state history.
- Visibility-scoped facts and context assembly for GM, party, character, and
  public viewpoints.
- A document library that ingests Markdown, text, and text-layer PDFs while
  retaining source provenance and access to raw files.
- Namespace-filtered lexical retrieval through SQLite FTS5, plus optional
  vector and hybrid retrieval. Retrieval helps find material but does not
  define campaign truth.
- A versioned, system-neutral game-system plugin API with capability
  negotiation and mounted plugin discovery.
- A turn loop that builds context, delegates known mechanics to the active
  plugin, routes unresolved actions to adjudication, applies approved changes,
  records events, and returns the actual resolution for narration.

## Runnable functionality

The pure-Python tabletop runtime and its automated examples are runnable in the
verified test environment. The recorded full suite exercises campaign
creation and persistence, restart recovery, facts and visibility, temporal
relationships, document ingestion and provenance, lexical retrieval, plugin
loading and discovery, deterministic dice, turn resolution and adjudication,
session history, freeform play, and the limited 2014 D&D 5e reference flow.

The Omega process itself was not started in this environment. The bounded
startup command could not be run because `run.sh` was absent and PeTTa was not
on `PATH`. Omega and PeTTa startup therefore remains unverified here. Static
inspection and plugin-loader tests do not replace that startup check.

## Tests

The latest full-suite result recorded in `docs/first-draft-verification.md` is:

```text
python3.11 -m pytest tests/ -q
649 passed in 4.39s
```

No failures are recorded for that run. The same verification document lists
the tested boundaries and clearly separates them from the unverified Omega
startup.

## Known limitations

- The D&D plugin implements only part of the 2014 5e rules. It is an API
  validation reference, not a complete game system.
- OCR is not implemented. Scanned or image-only PDFs need manual review and
  cannot be treated as successfully extracted source text.
- There is no player-facing or GM-facing UI.
- Each process supports one startup-selected workspace. Serving setting and
  campaign workspaces requires separate processes.
- Campaign deletion is unsupported. The intended archival lifecycle is also
  not implemented, so campaigns remain in the database.
- Vector search is a brute-force option intended for small corpora. It is not
  campaign truth.
- Extracted scanned PDFs need manual review, even if some text appears usable.
- Seven historical Autotests README PDFs were removed from the current tree
  but remain reachable in git history.
- The full Omega container image was not booted.
- Token estimation is approximate and must not be treated as provider billing
  or exact context accounting.

## Architectural risks

- A new public mutation could update campaign state without appending its
  corresponding event. Existing tested paths preserve the pairing, but every
  future mutation must keep that invariant.
- A deployment whose SQLite build lacks FTS5 will lose lexical search. The
  runtime can report retrieval as unavailable, but deployment checks must
  detect the missing capability.
- Concurrent writers have not been tested. Transaction behavior under
  concurrent campaign activity remains unknown.

## Ranked next steps

1. Verify normal Omega startup with PeTTa by running the real launcher and
   confirming that the tabletop plugin registers successfully.
2. Build and boot the full Compose image, then verify persistence, mounts,
   environment propagation, and startup behavior in the container.
3. Add the UI and tabletop-specific channel flows only after the runtime and
   container startup paths are verified.

## Readiness statement

This repository is a tested first draft of the tabletop runtime. It is not a
production-ready product.
