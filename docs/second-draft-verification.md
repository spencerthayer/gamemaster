# Second-draft verification

Recorded from branch `event-history-completeness` while implementing
`.agents/plans/2026-09-22_091051-post-first-draft-hardening.plan.md`.

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
7. **Omega container startup: NOT MET.** `tests/integration/test_docker_opt_in.py`
   skips unless `GAMEMASTER_RUN_DOCKER=1`. This session did not boot the image.
8. **Production image build: NOT MET.** Same skip. `docker compose config` is
   not a build.
9. **Restart persistence, FTS5, and pypdf inside the container: NOT MET.**
10. **Visibility matrix: MET** if `tests/tabletop/test_visibility_matrix.py` passes.
    Fact expiry is not filtered. Relationship expiry is.
11. **Full suite: MET.** `python3.11 -m pytest tests/ -q` reported
    `698 passed, 2 skipped in 5.00s`. The two skips are the Docker and Omega
    opt-in tests.
12. **Schema document: MET.** `docs/campaign-model.md` lists migrations through
    `0015_turn_receipts.sql`.
13. **Campaign archival: NOT MET.** ADR 0010 remains accepted and unimplemented.

## Deferred

Archival, advisory judges, routing metadata, a new vector backend, OCR, UI,
further D&D coverage, and a player workspace stay out of this branch.
