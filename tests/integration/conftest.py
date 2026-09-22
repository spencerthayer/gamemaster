"""Host paths for integration bind mounts.

Docker creates a missing bind-mount source as root. ``library/`` is gitignored,
so a later test cannot create ``library/raw`` after the first ``compose up``.
Create the tree as the current user before any container starts.
"""

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def pytest_configure() -> None:
    for relative in ("library/raw", "campaigns", "plugins"):
        (_REPO / relative).mkdir(parents=True, exist_ok=True)
