"""Structure smoke tests for the tabletop runtime skeleton.

Defends two contracts of the project structure phase:
1. every planned module exists and imports cleanly;
2. the runtime package imports zero Omega/MeTTa modules (architecture rule:
   the runtime is standalone-testable and Omega integration lives only in
   plugins/tabletop/). The cleanliness check runs in a subprocess because
   the rest of the test suite legitimately imports Omega modules in-process.
"""

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

RUNTIME_MODULES = [
    "tabletop",
    "tabletop.api",
    "tabletop.api.plugin",
    "tabletop.api.capabilities",
    "tabletop.api.actions",
    "tabletop.api.resolution",
    "tabletop.api.events",
    "tabletop.api.entities",
    "tabletop.api.rules",
    "tabletop.api.visibility",
    "tabletop.plugins",
    "tabletop.plugins.manifest",
    "tabletop.plugins.discovery",
    "tabletop.plugins.registry",
    "tabletop.campaign",
    "tabletop.campaign.store",
    "tabletop.campaign.models",
    "tabletop.campaign.event_store",
    "tabletop.campaign.projections",
    "tabletop.campaign.relationships",
    "tabletop.campaign.visibility",
    "tabletop.documents",
    "tabletop.documents.ingest",
    "tabletop.documents.models",
    "tabletop.documents.markdown",
    "tabletop.documents.pdf",
    "tabletop.documents.provenance",
    "tabletop.retrieval",
    "tabletop.retrieval.interface",
    "tabletop.retrieval.lexical",
    "tabletop.retrieval.vector",
    "tabletop.retrieval.hybrid",
    "tabletop.retrieval.models",
    "tabletop.orchestration",
    "tabletop.orchestration.turn",
    "tabletop.orchestration.context",
    "tabletop.orchestration.adjudication",
    "tabletop.orchestration.session",
    "tabletop.storage",
    "tabletop.storage.sqlite",
    "tabletop.storage.migrations",
    "tabletop.dice",
    "tabletop.dice.parser",
    "tabletop.dice.roller",
    "systems.freeform",
    "systems.dnd5e",
]

FORBIDDEN_PREFIXES = ("omega", "metta")


@pytest.mark.parametrize("module_name", RUNTIME_MODULES)
def test_module_imports(module_name):
    importlib.import_module(module_name)


def test_runtime_stays_free_of_omega_imports():
    repo_root = Path(__file__).resolve().parents[2]
    probe = (
        "import sys\n"
        f"for mod in {RUNTIME_MODULES!r}:\n"
        "    __import__(mod)\n"
        "bad = [m for m in sys.modules\n"
        f"       if m.lower().startswith({FORBIDDEN_PREFIXES!r})]\n"
        "assert not bad, f'runtime imported Omega/MeTTa modules: {bad}'\n"
    )
    env = dict(os.environ, PYTHONPATH=str(repo_root))
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env=env,
        cwd=repo_root,
    )
    assert result.returncode == 0, result.stderr
