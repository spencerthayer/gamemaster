"""GM adjudication path.

Handles actions a system plugin cannot deterministically resolve:
``requires_ruling = True`` flows here with rule references and context; the
resulting ruling is recorded as a first-class campaign ruling. Phase 9 and
Phase 23 of the execution plan fill this in.
"""
