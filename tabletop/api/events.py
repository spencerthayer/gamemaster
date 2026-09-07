"""Typed event model for the append-only campaign event log.

Events carry sequence, event_type, campaign_id, session_id, scene_id,
actor_id, target_id, and payload. Immutable once written. Phases 8 and 12 of
the execution plan fill this in.
"""
