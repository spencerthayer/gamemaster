!(import! &self (library Omega ./plugins/tabletop/omega_tabletop_adapter.py))

(= (loadOmegaPlugin)
   (let $prompt-path
     (joinPath ((projectRootDirectory) "plugins" "tabletop" "prompt.md"))
     (let $prompt (py-call (omega_tabletop_adapter.load_prompt_policy $prompt-path))
       (progn
         (let $status (py-call (omega_tabletop_adapter.initialize_response))
           (log INFO "tabletop-plugin" $status))
         ;; claim_skill_registration is the one-shot gate: first call returns the
         ;; bare workspace token (setting|campaign); later calls return
         ;; already-registered so register-workspace-skills adds nothing.
         (let $workspace (py-call (omega_tabletop_adapter.claim_skill_registration))
           (progn
             (log INFO "tabletop-plugin-workspace" $workspace)
             (register-workspace-skills $workspace)))
         (add-prompt-extension tabletop-runtime-policy $prompt)
         (log INFO "tabletop-prompt-extension" "tabletop-runtime-policy")
         ()))))

;; Reevaluated on each getPromptExtensions call. The value is the snapshot
;; text. The receipt call is separate and cannot replace that text.
;; collapse emits this rule before the ground policy atom. The Python
;; result therefore starts with the policy text, then the snapshot.
(= (prompt-extension tabletop-allocated-context)
   (let $text (py-call (omega_tabletop_adapter.allocated_context_text))
     (progn
       (py-call (omega_tabletop_adapter.record_allocated_context_receipt))
       $text)))

;; Workspace skill surfaces are fixed at startup from TABLETOP_WORKSPACE.
;; Forbidden operations are absent from add-skill, not merely discouraged.
;; Bare tokens (not JSON) match py-call results from claim_skill_registration.

(= (register-workspace-skills already-registered) ())
(= (register-workspace-skills workspace-unavailable) ())
(= (register-workspace-skills "already-registered") ())
(= (register-workspace-skills "workspace-unavailable") ())
(= (register-workspace-skills "setting") (register-workspace-skills setting))
(= (register-workspace-skills "campaign") (register-workspace-skills campaign))

(= (register-workspace-skills setting)
   (progn
     (add-skill query-setting
       "Query setting metadata and setting-scoped world facts visible in the current setting workspace"
       (query_in_quotes))
     (add-skill edit-setting
       "Edit setting metadata for the active setting workspace"
       (edit_in_quotes))
     (add-skill get-world-entity
       "Return one setting-owned world entity by stable identifier"
       (entity_id_in_quotes))
     (add-skill upsert-world-entity
       "Create or update one setting-owned world entity"
       (entity_in_quotes))
     (add-skill query-world-history
       "Query setting-scoped world history and durable world facts"
       (query_in_quotes))
     (add-skill record-world-history
       "Record a setting-scoped world history entry"
       (entry_in_quotes))
     (add-skill get-chunk
       "Reload one retrieved source chunk by its stable identifier"
       (chunk_id_in_quotes))
     (add-skill promote-world-fact
       "Confirm one proposed setting fact in the owned setting"
       (fact_id_in_quotes))
     (add-skill reveal-world-fact
       "Reveal one setting fact in the owned setting"
       (fact_id_in_quotes))
     ()))

(= (register-workspace-skills campaign)
   (progn
     (add-skill query-setting
       "Query setting metadata and setting-scoped world facts visible in the current setting workspace"
       (query_in_quotes))
     (add-skill get-world-entity
       "Return one setting-owned world entity by stable identifier"
       (entity_id_in_quotes))
     (add-skill query-world-history
       "Query setting-scoped world history and durable world facts"
       (query_in_quotes))
     (add-skill get-chunk
       "Reload one retrieved source chunk by its stable identifier"
       (chunk_id_in_quotes))
     (add-skill read-session
       "Read one campaign session record by session identifier"
       (session_id_in_quotes))
     (add-skill start-session
       "Open one campaign session through the authoritative runtime"
       (session_in_quotes))
     (add-skill end-session
       "Close the active tabletop session through the authoritative runtime"
       ())
     (add-skill current-scene
       "Return the active scene from authoritative campaign state"
       ())
     (add-skill get-party-state
       "Return the current party membership and party-visible state"
       ())
     (add-skill get-open-threads
       "Return open campaign threads tracked in authoritative state"
       ())
     (add-skill mutate-quest
       "Create or update a campaign quest record"
       (quest_in_quotes))
     (add-skill read-campaign-secret
       "Read one GM-scoped campaign secret by stable identifier"
       (secret_id_in_quotes))
     (add-skill current-campaign
       "Return the active tabletop campaign or an explicit configuration error"
       ())
     (add-skill query-campaign
       "Query authoritative campaign information visible to the current viewpoint"
       (query_in_quotes))
     (add-skill query-rules
       "Query active tabletop rules and return source-aware runtime results"
       (query_in_quotes))
     (add-skill resolve-action
       "Resolve a structured game action through the active game-system plugin"
       (action_in_quotes))
     (add-skill roll
       "Roll a system-agnostic dice expression through the deterministic runtime dice engine"
       (expression_in_quotes))
     (add-skill get-entity
       "Return one campaign entity by stable identifier with visibility filtering"
       (entity_id_in_quotes))
     (add-skill get-fact
       "Return one campaign or owned-setting fact by stable identifier"
       (fact_id_in_quotes))
     (add-skill get-relationships
       "Return visible relationships for one campaign entity"
       (entity_id_in_quotes))
     (add-skill record-ruling
       "Record a durable campaign ruling through the authoritative runtime"
       (ruling_in_quotes))
     (add-skill promote-ruling
       "Confirm one proposed ruling in the active campaign"
       (ruling_id_in_quotes))
     (add-skill get-ruling
       "Return one ruling in the active campaign by stable identifier"
       (ruling_id_in_quotes))
     (add-skill promote-fact
       "Confirm one proposed campaign fact in the active campaign"
       (fact_id_in_quotes))
     (add-skill reveal-fact
       "Reveal one confirmed campaign fact in the active campaign"
       (fact_id_in_quotes))
     ()))

(= (query-setting $query)
   (py-call (omega_tabletop_adapter.query_setting $query)))

(= (edit-setting $edit)
   (py-call (omega_tabletop_adapter.edit_setting $edit)))

(= (get-world-entity $entity-id)
   (py-call (omega_tabletop_adapter.get_world_entity $entity-id)))

(= (upsert-world-entity $entity)
   (py-call (omega_tabletop_adapter.upsert_world_entity $entity)))

(= (query-world-history $query)
   (py-call (omega_tabletop_adapter.query_world_history $query)))

(= (record-world-history $entry)
   (py-call (omega_tabletop_adapter.record_world_history $entry)))

(= (get-chunk $chunk-id)
   (py-call (omega_tabletop_adapter.get_chunk $chunk-id)))

(= (promote-world-fact $fact-id)
   (py-call (omega_tabletop_adapter.promote_world_fact $fact-id)))

(= (reveal-world-fact $fact-id)
   (py-call (omega_tabletop_adapter.reveal_world_fact $fact-id)))

(= (promote-fact $fact-id)
   (py-call (omega_tabletop_adapter.promote_fact $fact-id)))

(= (reveal-fact $fact-id)
   (py-call (omega_tabletop_adapter.reveal_fact $fact-id)))

(= (read-session $session-id)
   (py-call (omega_tabletop_adapter.read_session $session-id)))

(= (start-session $session)
   (py-call (omega_tabletop_adapter.start_session $session)))

(= (end-session)
   (py-call (omega_tabletop_adapter.end_session)))

(= (current-scene)
   (py-call (omega_tabletop_adapter.current_scene)))

(= (get-party-state)
   (py-call (omega_tabletop_adapter.get_party_state)))

(= (get-open-threads)
   (py-call (omega_tabletop_adapter.get_open_threads)))

(= (mutate-quest $quest)
   (py-call (omega_tabletop_adapter.mutate_quest $quest)))

(= (read-campaign-secret $secret-id)
   (py-call (omega_tabletop_adapter.read_campaign_secret $secret-id)))

(= (current-campaign)
   (py-call (omega_tabletop_adapter.current_campaign)))

(= (query-campaign $query)
   (py-call (omega_tabletop_adapter.query_campaign $query)))

(= (query-rules $query)
   (py-call (omega_tabletop_adapter.query_rules $query)))

(= (resolve-action $action)
   (py-call (omega_tabletop_adapter.resolve_action $action)))

(= (roll $expression)
   (py-call (omega_tabletop_adapter.roll $expression)))

(= (get-entity $entity-id)
   (py-call (omega_tabletop_adapter.get_entity $entity-id)))

(= (get-fact $fact-id)
   (py-call (omega_tabletop_adapter.get_fact $fact-id)))

(= (get-relationships $entity-id)
   (py-call (omega_tabletop_adapter.get_relationships $entity-id)))

(= (record-ruling $ruling)
   (py-call (omega_tabletop_adapter.record_ruling $ruling)))

(= (promote-ruling $ruling-id)
   (py-call (omega_tabletop_adapter.promote_ruling $ruling-id)))

(= (get-ruling $ruling-id)
   (py-call (omega_tabletop_adapter.get_ruling $ruling-id)))
