"""Structure smoke tests for the tabletop runtime skeleton.

Defends the project structure phase contracts:
1. every planned module exists and imports cleanly;
2. the runtime stays inside its import boundary: no Omega surfaces
   (channels, providers, plugins, memory, profile, src) and no omega/metta
   named modules, checked statically via AST and dynamically in a clean
   subprocess (the rest of the suite legitimately imports Omega in-process).
"""

import ast
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

# Omega surfaces importable as namespace packages when the repo root is on
# sys.path, plus any omega/metta named module. The runtime must import none.
BANNED_TOPLEVEL = {"channels", "providers", "plugins", "memory", "profile", "src"}
BANNED_PREFIXES = ("omega", "metta")

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _module_file(module_name):
    parts = module_name.split(".")
    base = _REPO_ROOT.joinpath(*parts)
    if base.is_dir():
        return base / "__init__.py"
    return base.with_suffix(".py")


@pytest.mark.parametrize("module_name", RUNTIME_MODULES)
def test_module_imports(module_name):
    importlib.import_module(module_name)


def test_runtime_import_boundary_static():
    for module_name in RUNTIME_MODULES:
        tree = ast.parse(_module_file(module_name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                tops = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                tops = [node.module.split(".")[0]]
            else:
                continue
            for top in tops:
                assert top not in BANNED_TOPLEVEL, (
                    f"{module_name} imports Omega surface {top!r}"
                )
                assert not top.lower().startswith(BANNED_PREFIXES), (
                    f"{module_name} imports {top!r}"
                )


def test_runtime_import_boundary_dynamic():
    probe = (
        "import sys\n"
        f"for mod in {RUNTIME_MODULES!r}:\n"
        "    __import__(mod)\n"
        "banned_toplevel = " + repr(sorted(BANNED_TOPLEVEL)) + "\n"
        "banned_prefixes = " + repr(BANNED_PREFIXES) + "\n"
        "bad = [m for m in sys.modules\n"
        "       if m.split('.')[0] in banned_toplevel\n"
        "       or m.split('.')[0].lower().startswith(banned_prefixes)]\n"
        "assert not bad, f'runtime imported Omega/MeTTa modules: {bad}'\n"
    )
    env = dict(os.environ, PYTHONPATH=str(_REPO_ROOT))
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
