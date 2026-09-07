!(import! &self (library Omega ./plugins/tabletop/omega_tabletop_adapter.py))

(= (loadOmegaPlugin)
   (progn
     (let $status (py-call (omega_tabletop_adapter.initialize_response))
       (log INFO "tabletop-plugin" $status))
     (add-skill current-campaign
       "Return the active tabletop campaign or an explicit configuration error"
       ())
     (add-skill current-scene
       "Return the active scene from authoritative campaign state"
       ())
     (add-skill query-rules
       "Query active tabletop rules and return source-aware runtime results"
       (query_in_quotes))
     (add-skill query-campaign
       "Query authoritative campaign information visible to the current viewpoint"
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
     (add-skill get-relationships
       "Return visible relationships for one campaign entity"
       (entity_id_in_quotes))
     (add-skill record-ruling
       "Record a durable campaign ruling through the authoritative runtime"
       (ruling_in_quotes))
     (add-skill end-session
       "Close the active tabletop session through the authoritative runtime"
       ())
     (add-prompt-extension tabletop-runtime-policy
       "TABLETOP_RUNTIME: Use tabletop skills for authoritative campaign state and mechanics. If a tabletop skill returns ok=false or capability_unavailable, treat that operation as unavailable and do not invent a mechanical result or campaign fact. Game-system plugins own deterministic mechanics; the Tabletop Runtime owns campaign continuity. Narration may describe returned outcomes but must not silently replace them.")
     ()))

(= (current-campaign)
   (py-call (omega_tabletop_adapter.current_campaign)))

(= (current-scene)
   (py-call (omega_tabletop_adapter.current_scene)))

(= (query-rules $query)
   (py-call (omega_tabletop_adapter.query_rules $query)))

(= (query-campaign $query)
   (py-call (omega_tabletop_adapter.query_campaign $query)))

(= (resolve-action $action)
   (py-call (omega_tabletop_adapter.resolve_action $action)))

(= (roll $expression)
   (py-call (omega_tabletop_adapter.roll $expression)))

(= (get-entity $entity-id)
   (py-call (omega_tabletop_adapter.get_entity $entity-id)))

(= (get-relationships $entity-id)
   (py-call (omega_tabletop_adapter.get_relationships $entity-id)))

(= (record-ruling $ruling)
   (py-call (omega_tabletop_adapter.record_ruling $ruling)))

(= (end-session)
   (py-call (omega_tabletop_adapter.end_session)))
