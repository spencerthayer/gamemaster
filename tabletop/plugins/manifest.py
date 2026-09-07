"""Manifest parsing and validation.

Parses and validates manifests for executable game-system plugins and
non-executable content packs (kind: rules, setting, adventure, campaign-seed,
supplement). Rejects path traversal and malformed manifests. Phase 7 of the
execution plan fills this in.
"""
