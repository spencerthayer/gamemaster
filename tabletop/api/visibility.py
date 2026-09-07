"""Visibility scopes and scoping contracts.

Scopes: PUBLIC, PARTY, GM, CHARACTER:<id>, NPC:<id> (plus FACTION, GROUP).
Facts carry explicit visibility; context construction filters per active
viewpoint. Default-closed: a datum reaches a viewer only when surfaced.
Phase 14 of the execution plan fills this in.
"""
