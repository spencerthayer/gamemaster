"""Omega-facing tabletop adapter: the single Omega integration point.

Loaded by Omega's Python loader (``src/plugin.py`` ``loadPythonPlugin``) as
``plugins/tabletop/omega_tabletop_adapter.py`` under the plugin name
``omega_tabletop_adapter``. The ``tabletop`` module name stays exclusive to
the standalone runtime package.

Phase 5 of the execution plan completes this adapter: skill registration,
prompt extensions, campaign and system-plugin discovery, and translation
between Omega skill calls and Tabletop Runtime calls. No game logic lives
here.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def ensure_runtime_importable():
    """Make the standalone ``tabletop`` runtime importable under Omega.

    Omega's Python loader appends only the plugin directory to ``sys.path``
    (``src/plugin.py`` ``loadPythonPlugin``), so the repo-root runtime
    package is not visible to it. Insert the repo root once, idempotently,
    then import the runtime to prove the path works.
    """
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    import tabletop

    return sys.modules["tabletop"]


def loadOmegaPlugin():
    """Entry point Omega's Python loader calls after importing this module."""
    runtime = ensure_runtime_importable()
    # Phase 5: register tabletop skills and prompt extensions here.
    return runtime
