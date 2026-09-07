"""Turn handling.

Drives one turn: action intake, deterministic resolution via the active
system plugin, event emission, and response assembly. The LLM proposes
intent; the runtime and plugins resolve mechanics. Phase 30 of the execution
plan fills this in.
"""
