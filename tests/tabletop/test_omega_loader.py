"""Integration test: load the adapter through Omega's real Python loader.

Exercises the exact path Omega uses at startup (``src/plugin.py``
``loadPythonPlugin``: append plugin location to sys.path, load
``<location>/<name>.py``, call ``loadOmegaPlugin()``) and proves the adapter
can import and initialize the standalone ``tabletop`` runtime even though
the repo root was never importable. This is the test that catches a broken
adapter-to-runtime import path; the skeleton tests run with the repo root on
PYTHONPATH and cannot catch it.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

ADAPTER_NAME = "omega_tabletop_adapter"
ADAPTER_LOCATION = "{REPO}/plugins/tabletop"

PROBE = """
import importlib.util
import pathlib
import sys

repo = pathlib.Path(sys.argv[1])

# The repo root must not be importable already, or this test proves nothing.
assert not any(pathlib.Path(p).resolve() == repo for p in sys.path if p), (
    "repo root was already on sys.path; loader bootstrap is not exercised"
)

spec = importlib.util.spec_from_file_location(
    "omega_src_plugin", repo / "src" / "plugin.py"
)
plugin_api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin_api)

plugin_api.loadPythonPlugin({name!r}, {location!r}.format(REPO=str(repo)))

assert {name!r} in plugin_api._plugins, "adapter not registered by loader"
mod = plugin_api._plugins[{name!r}].mod
assert mod.__name__ == {name!r}, "adapter loaded under wrong module name"
assert "tabletop" in sys.modules, "adapter did not import the runtime"
assert mod.loadOmegaPlugin() is sys.modules["tabletop"], (
    "loadOmegaPlugin did not return the runtime module"
)
print("LOADER-OK")
"""


def test_adapter_loads_through_omega_python_loader(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    probe = PROBE.format(name=ADAPTER_NAME, location=ADAPTER_LOCATION)
    result = subprocess.run(
        [sys.executable, "-c", probe, str(_REPO_ROOT)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    assert "LOADER-OK" in result.stdout
