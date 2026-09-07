"""Universal action model.

``GameAction``: the system-agnostic representation of an actor's declared
intent, resolved by game-system plugins. The core runtime never knows the
meaning of AC, saving throws, hit locations, spell slots, mana, refresh, or
sanity. Phase 8 of the execution plan fills this in.
"""
