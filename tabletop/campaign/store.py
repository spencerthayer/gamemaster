"""SQLite-backed authoritative campaign store.

Tables: campaigns, sessions, scenes, entities, facts, relationships, events,
rulings, documents, document_chunks, plus clocks, resources, visibility
grants, and system_state as needed. Campaign truth lives here, never in
vector recall. Phase 11 of the execution plan fills this in.
"""
