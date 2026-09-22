# Prompt context verification

Recorded on branch `allocated-context-render` after the Omega container
observed a fresh `CHARS_SENT` payload. A skipped Docker test is not a pass.
`src/loop.metta` was not edited.

The policy file is no longer a static prompt extension. `allocated_context_text`
returns that policy once, then the snapshot, from a single snapshot build.

## Checks

1. **`build_context` has a production caller, and that caller is the tabletop
   prompt extension: MET.** `plugins/tabletop/omega_tabletop_adapter.py`
   `allocated_context_text` calls
   `TabletopRuntime.prompt_context_snapshot_with_receipt`, which builds
   `prompt_context_snapshot` once and stores the receipt for that snapshot.
2. **Dynamic context is recomputed for every Omega context build: MET.**
   `GAMEMASTER_RUN_DOCKER=1 python3.11 -m pytest tests/integration/test_omega_prompt_context.py -q`
   passed in 51.84s. A fact imported after plugin load appeared in a later
   `CHARS_SENT` payload.
3. **Order inside one `CHARS_SENT` payload: MET.** The same test required
   `Tabletop Runtime is authoritative`, then `Allocated tabletop context`,
   then the fact, then `:-:-:-:`. The assertion does not require one physical
   log line.
4. **The current human text stays on Omega's append path: MET.** The fact
   text is before `:-:-:-:`. The extension does not copy the utterance.
5. **The renderer calls `build_context` and writes nothing: MET.**
   `tests/tabletop/test_prompt_context.py` counts `events` and
   `setting_events` before and after the snapshot. No provider call is in
   `tabletop/orchestration/prompt_context.py`.
6. **The receipt is diagnostic and restart-stable: MET.**
   `tests/tabletop/test_prompt_receipt.py` reopens the database and still
   reads the row. `project_campaign` does not gain the receipt. Inserting a
   receipt appends no event.
7. **Receipt failure does not block the returned text: MET.**
   `tests/tabletop/test_prompt_extension.py` forces the receipt writer to
   raise and still reads the same snapshot text.
8. **Another campaign's fact is absent from `CHARS_SENT`: MET.** The
   container test asserts `OTHER-CAMPAIGN-SENTINEL` is absent from every
   `CHARS_SENT` payload.
9. **`src/loop.metta` is unchanged: MET.** This branch does not diff that
   file.
10. **No new skill: MET.** `plugins/tabletop/tabletop.metta` adds a prompt
    extension rule and does not add `add-skill`.
11. **Plugin copies stay sealed: MET.** The container test, as uid 65534,
    finds `plugins/tabletop/tabletop.metta` is not writable. The same run's
    startup companion,
    `GAMEMASTER_RUN_DOCKER=1 python3.11 -m pytest tests/integration/test_omega_startup.py -q`,
    passed in 38.54s.
12. **Campaign archival: NOT MET.** ADR 0010 remains accepted and
    unimplemented.
13. **Roadmap: MET** after this document. Milestone 6 names the landed GURPS
    plugin. Milestone 4 names the live prompt extension.
14. **Default suite: MET.** `python3.11 -m pytest tests/tabletop -q` reported
    `642 passed in 5.49s`. `python3.11 -m pytest tests/ -q` reported
    `707 passed, 4 skipped in 5.25s`. The four skips are the Docker and Omega
    opt-in tests.
