"""Context assembly.

Builds per-viewpoint context: current scene, active entities, viewer-visible
facts, recent events, relevant history, NPC agenda (GM side), relationships,
system info, retrieved rules, and campaign rulings. No full-file dumps into
prompts. Phase 30 of the execution plan fills this in.
"""
