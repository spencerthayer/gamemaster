"""Omega-facing tabletop adapter.

The single integration point between Omega and the Tabletop Runtime. Phase 5
of the execution plan fills this in:

- initialize the Tabletop Runtime
- register tabletop skills via Omega's plugin API
- add prompt instructions (prompt extension API)
- discover configured campaigns and game-system plugins
- translate Omega skill calls into Tabletop Runtime calls
- translate results into safe, serializable responses

No game logic lives here. Entry point expected by Omega:
``loadOmegaPlugin()`` (MeTTa glue in ``tabletop.metta`` + Python bridge).
"""
