"""Resolution models.

``ResolutionContext`` and ``Resolution``: the deterministic outcome of a
resolved action, including the ``requires_ruling = True`` path with rule
references and context when a plugin cannot decide. The LLM may only
adjudicate what this boundary hands over. Phase 8 of the execution plan fills
this in.
"""
