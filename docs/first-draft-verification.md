# First-draft verification

Verified on 2026-09-22 from branch
`phase-33-40-verification-release`.

## Suite result

Command:

```text
python3.11 -m pytest tests/ -q
```

Result:

```text
........................................................................ [ 11%]
........................................................................ [ 22%]
........................................................................ [ 33%]
........................................................................ [ 44%]
........................................................................ [ 55%]
........................................................................ [ 66%]
........................................................................ [ 77%]
........................................................................ [ 88%]
........................................................................ [ 99%]
.                                                                        [100%]
649 passed in 4.39s
```

All test evidence below was included in that passing run.

## Definition of done

1. **Omega starts normally: NOT MET in this environment.**
   The required bounded command, `sh run.sh run.metta`, could not be run
   because its launcher and runtime are unavailable. The environment check
   reported:

   ```text
   run.sh: missing; sh run.sh run.metta cannot run
   petta: not found on PATH
   ```

   No startup claim is made from static inspection or unit tests.

2. **The tabletop plugin loads: MET.**
   `tests/tabletop/test_omega_loader.py::test_adapter_loads_through_omega_python_loader`
   loads and registers the adapter through Omega's real Python plugin loader.
   `tests/tabletop/test_phase5_adapter.py::test_metta_plugin_registers_workspace_skills_and_prompt_extension`
   checks the MeTTa registration surface.

3. **A separate plugin API exists: MET.**
   `tests/tabletop/test_skeleton.py::test_module_imports` imports
   `tabletop.api.plugin`, and
   `tests/tabletop/test_game_system_plugin.py::test_plugin_version_is_distinct_from_api_version`
   verifies the versioned API boundary.

4. **System plugins are discovered without editing Omega core: MET.**
   `tests/tabletop/test_plugin_integration.py::test_external_plugin_loads_without_modifying_repo`
   loads an external system plugin, while
   `tests/tabletop/test_skeleton.py::test_runtime_import_boundary_static` and
   `tests/tabletop/test_skeleton.py::test_runtime_import_boundary_dynamic`
   enforce the separation from Omega modules.

5. **Freeform works: MET.**
   `tests/tabletop/test_demo_freeform.py::test_freeform_example_campaign_runs_end_to_end`
   runs the freeform campaign flow end to end.

6. **Campaigns can be created: MET.**
   `tests/tabletop/test_campaign_store.py::test_creates_reads_and_lists_campaigns`
   creates, reads, and lists campaigns.

7. **SQLite persists: MET.**
   `tests/tabletop/test_demo_freeform.py::test_freeform_example_campaign_runs_end_to_end`
   closes the first SQLite connection, opens the same database again, and
   verifies stored campaign data.

8. **State survives restart: MET.**
   `tests/tabletop/test_demo_freeform.py::test_freeform_example_campaign_runs_end_to_end`
   reconstructs `TabletopRuntime` on the reopened database and verifies
   campaign state, entities, rulings, facts, retrieval data, and sessions.

9. **Facts are visibility-scoped: MET.**
   `tests/tabletop/test_visibility_filter.py::test_filters_facts_in_sql_for_character_and_gm_viewpoints`
   verifies SQL-level filtering for player and GM viewpoints.

10. **Relationships are stored and queried: MET.**
    `tests/tabletop/test_relationships.py::test_stores_setting_edge_with_all_fields`
    stores an edge, and
    `tests/tabletop/test_relationships.py::test_query_as_of_excludes_future_and_closed_edges`
    verifies temporal querying.

11. **PDF and Markdown are ingested: MET.**
    `tests/tabletop/test_ingest_pdf.py::test_ingests_pdf_pages_with_shared_shape_detection_and_provenance`
    and
    `tests/tabletop/test_ingest_markdown.py::test_ingests_markdown_with_nested_heading_paths_and_provenance`
    exercise both formats.

12. **Raw files stay accessible: MET.**
    `tests/tabletop/test_document_library.py::test_lookup_resolves_one_logical_document_under_both_roots`
    resolves the original raw path separately from the processed path.

13. **Provenance is preserved: MET.**
    `tests/tabletop/test_canon_lifecycle_events.py::test_promote_imported_fact_preserves_provenance`
    verifies provenance through promotion, and
    `tests/tabletop/test_retrieval_lexical.py::test_results_carry_human_citable_provenance`
    verifies it in retrieval results.

14. **Rules retrieval is filtered by namespace: MET.**
    `tests/tabletop/test_retrieval_lexical.py::test_namespaces_are_isolated`
    verifies namespace isolation.

15. **Generic dice resolve deterministically: MET.**
    `tests/tabletop/test_dice_roller.py::test_same_seed_produces_the_same_roll`
    verifies identical results from an identical seed.

16. **An unresolved action requests GM adjudication: MET.**
    `tests/tabletop/test_turn_loop.py::test_unresolved_routes_to_adjudication_without_persist`
    verifies the unresolved result and its adjudication request.

17. **Narration uses the returned resolution: MET.**
    `tests/tabletop/test_turn_loop.py::test_narration_comes_from_resolution_not_replacement`
    verifies that narration equals the plugin resolution explanation and
    contains its mechanical values.

18. **Session history persists: MET.**
    `tests/tabletop/test_session_model.py::test_end_session_preserves_agendas_clocks_rulings_and_events`
    verifies retained history, and the freeform end-to-end test verifies it
    after reopening the database.

19. **The small 5e plugin proves the APIs: MET.**
    `tests/tabletop/test_demo_dnd5e.py::test_dnd5e_example_campaign_runs_combat_end_to_end`
    exercises the 5e plugin through the generic runtime, and
    `tests/tabletop/test_demo_dnd5e.py::test_tabletop_api_has_no_live_dnd5e_vocabulary`
    verifies that 5e concepts did not leak into the generic API.

20. **Tests cover the major boundaries: MET.**
    The full command above ran 649 tests. Specific boundary checks include
    `tests/tabletop/test_skeleton.py::test_runtime_import_boundary_static`,
    `tests/tabletop/test_mechanics_boundary.py::test_declared_capability_means_the_plugin_is_always_called`,
    `tests/tabletop/test_security_boundaries.py::test_plugin_roots_are_separate_from_document_and_content_roots`,
    and
    `tests/tabletop/test_architecture_invariants.py::test_12_setting_workspace_exposes_no_campaign_operations`.

21. **Docker and Portainer are documented: MET.**
    These commands passed:

    ```text
    docker compose config
    rg -n "^## Docker Compose and Portainer$|^For Portainer:$" README.md
    rg -n "Portainer" docs/security.md
    ```

    `docker compose config` emitted warnings for unset optional environment
    variables, then exited successfully. The searches found the deployment
    instructions in `README.md` and `docs/security.md`.

22. **The roadmap is present: MET.**
    This command passed:

    ```text
    test -f docs/roadmap.md
    ```

23. **Proposed facts require explicit promotion: MET.**
    `tests/tabletop/test_architecture_invariants.py::test_03_proposed_fact_cannot_be_stored_as_known`
    prevents proposed facts from entering known state, and
    `tests/tabletop/test_canon_invariants.py::test_promote_changes_only_canon_state_and_returns_frozen_fact`
    verifies the explicit promotion operation.

24. **Promotion and reveal are separately auditable: MET.**
    `tests/tabletop/test_canon_lifecycle_events.py::test_promote_appends_only_fact_promoted`
    and
    `tests/tabletop/test_canon_lifecycle_events.py::test_reveal_appends_only_fact_revealed`
    verify separate event records.

25. **Deleting an ingested document removes provenance-owned records and detached records survive: MET.**
    `tests/tabletop/test_architecture_invariants.py::test_06_deleting_document_removes_only_records_derived_from_it`
    verifies deletion scope, and
    `tests/tabletop/test_architecture_invariants.py::test_05_source_purge_removes_attached_imports_but_preserves_owned_facts`
    verifies survival after ownership is detached.

26. **An interrupted ingest resumes from a slice boundary: MET.**
    `tests/tabletop/test_ingest_jobs.py::test_interruption_preserves_completed_slices_and_resume_skips_them`
    verifies completed-slice preservation and boundary resume behavior.

27. **A compacted context entry can be refetched: MET.**
    `tests/tabletop/test_context_compaction.py::test_compaction_creates_exact_refetchable_stub_and_round_trips`
    verifies compaction and exact refetch.

## Repository identity

Command:

```text
git remote -v
```

Both required remotes are present:

```text
origin   https://github.com/spencerthayer/gamemaster.git (fetch)
origin   https://github.com/spencerthayer/gamemaster.git (push)
upstream https://github.com/singnet/Omega.git (fetch)
upstream https://github.com/singnet/Omega.git (push)
```

`UPSTREAM.md` records the bootstrap commit as
`7b060f5738ee7b8cf064c8b6282ed9fe07cf407f`. The current HEAD observed during
verification was `4c8bcda` before this documentation commit. The current HEAD
is not claimed to be the bootstrap commit.
