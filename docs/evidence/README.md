# User-ready campaign lifecycle evidence

## D2 ContextVar survival

| Field | Value |
|---|---|
| When | 2026-09-23 |
| Task | `d-sender-principal` |
| Python | 3.11.2 (image) |
| Omega commit | see `d2-contextvar-survives.json` |
| Channel / bridge | PeTTA `py-call` two-call probe (`accept_sender` then `read_sender`) |
| Thread / asyncio | single OS thread; no asyncio tasks |
| Opaque sender at receive | `42` (display identity `Bob` not used) |
| Observed at py-call | `42` |
| Result | `contextvar_survives` |

Artifact: `docs/evidence/d2-contextvar-survives.json`

## Evidence log

Machine-readable proofs for empirical gates in the user-ready campaign lifecycle plan.

| Gate | File | Result |
|------|------|--------|
| G3 contradiction integration | [g3-contradiction-integration.json](g3-contradiction-integration.json) | pass |
| H3 smoke and docs | [h3-smoke-and-docs.json](h3-smoke-and-docs.json) | 793 passed, 4 skipped |

Sender ContextVar survival and SQLite concurrency proofs land on branches D and E when those PRs merge.
