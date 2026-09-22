# Second-draft verification

The checks below were recorded during development of
`.agents/plans/2026-09-22_091051-post-first-draft-hardening.plan.md`.
The work ultimately landed on `main` at
`fd07a3d7fab908c246e6f62fd888ee4139605237`.

The commands below are the evidence. A skipped Docker test is not a pass.

## Checks

1. **Model-facing mutations emit replayable events: MET** if
   `tests/tabletop/test_replay_contract.py` passes in the suite run below.
2. **Replay matches stored quests, rulings, sessions, and new campaign facts: MET**
   if that module and `tests/tabletop/test_setting_events.py` pass.
3. **Compaction refetch: MET** if `tests/tabletop/test_chunk_refetch.py` passes.
   `get-ruling` reloads a ruling id stored as the chunk id on the ruling source.
4. **Ruling promotion is campaign-only: MET** if
   `test_promote_ruling_confirms_only_the_active_campaign` passes.
5. **Input fields cannot confirm canon: MET** if that test and
   `tests/tabletop/test_fact_lifecycle_skills.py` pass.
6. **Writes stay in the active campaign or owned setting: MET** if the
   boundary and lifecycle tests pass.
7. **Omega container startup: MET.**
   `GAMEMASTER_RUN_DOCKER=1 python3.11 -m pytest tests/integration/test_omega_startup.py`
   passed in 46.94s. Logs contained `tabletop-plugin`,
   `tabletop-plugin-workspace` with `campaign`, and
   `tabletop-prompt-extension`. The `swipl` process running `run.metta` was
   uid 65534. Landlock denies Docker Desktop bind mounts even when the path
   is allowed, so `entrypoint.sh` copies the plugin, library, and campaigns
   mounts onto the container filesystem before the agent starts. Tabletop
   SQLite stays on the named state volume, which the policy allows as
   read-write.
8. **Production image build: MET.** `docker compose build` produced
   `gamemaster:latest`. `SWIPL_IMAGE` remained `docker.io/library/swipl:10.0.2`.
   The index digest observed on 2026-09-22 was
   `sha256:9d44cac3f4235e297db87217e59aa81cdf9847970bd27ddb447072d5466ea8ae`.
   The linux/amd64 manifest digest was
   `sha256:ca286371b720b38dceb7a9ca74f5ce40db24355f7c827ad96a663d0ceb1ffa57`.
   `petta_lib_chromadb` is pinned to `218484875d5d1bfb217a9a03d3983dc1ed9d406c`.
9. **FTS5, pypdf, and a uid 65534 SQLite file inside the image: MET.**
   `tests/integration/test_docker_image.py` passed. The state directory is
   prepared with `chown 65534:65534` before the runtime user writes, matching
   `entrypoint.sh`. A second container on the same volume read the inserted row.
10. **Visibility matrix: MET** if `tests/tabletop/test_visibility_matrix.py` passes.
    Fact expiry is not filtered. Relationship expiry is.
11. **Full suite: MET.** `python3.11 -m pytest tests/ -q` reported
    `698 passed, 3 skipped in 7.73s`. The skips are the Docker image test,
    the Omega startup test, and the Docker opt-in marker test.
12. **Schema document: MET.** `docs/campaign-model.md` lists migrations through
    `0015_turn_receipts.sql`.
13. **Campaign archival: NOT MET.** ADR 0010 remains accepted and unimplemented.

## Deferred

Archival, advisory judges, routing metadata, a new vector backend, OCR, UI,
further D&D coverage, and a player workspace stay out of this branch.
