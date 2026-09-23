# User-ready campaign lifecycle evidence

## D2 ContextVar survival

| Field | Value |
|---|---|
| When | 2026-09-23 |
| Task | `d-sender-principal` |
| Python | 3.11.2 (image) |
| Omega commit | see `d2-contextvar-survives.json` |
| Channel / bridge | PeTTa `py-call` two-call probe (`accept_sender` then `read_sender`) |
| Thread / asyncio | single OS thread; no asyncio tasks |
| Opaque sender at receive | `42` (display identity `Bob` not used) |
| Observed at py-call | `42` |
| Result | `contextvar_survives` |

Artifact: `docs/evidence/d2-contextvar-survives.json`
