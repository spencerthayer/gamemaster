"""Landlock policy path selection without applying a kernel ruleset."""

import importlib.util
import sys
from enum import IntFlag
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[1]


class _AccessFs(IntFlag):
    READ_FILE = 1
    READ_DIR = 2
    WRITE_FILE = 4
    TRUNCATE = 8
    MAKE_REG = 16
    MAKE_DIR = 32
    MAKE_SYM = 64
    REMOVE_FILE = 128
    REMOVE_DIR = 256
    MAKE_FIFO = 512
    MAKE_SOCK = 1024
    EXECUTE = 2048


class _Landlock:
    def __init__(self, strict=False):
        self.strict = strict


def _load_policy():
    stub = ModuleType("py_landlock")
    stub.AccessFs = _AccessFs
    stub.Landlock = _Landlock
    sys.modules["py_landlock"] = stub
    spec = importlib.util.spec_from_file_location(
        "filesystem_policy_under_test",
        _REPO_ROOT / "profile" / "policy.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_paths_skip_missing_and_keep_device_nodes(tmp_path, caplog):
    policy_module = _load_policy()
    missing = tmp_path / "tabletop-data"
    present = tmp_path / "present"
    present.mkdir()
    policy = policy_module.FileSystemPolicy()

    with caplog.at_level("WARNING"):
        existing = policy._existing_paths([missing, present, Path("/dev/null")])

    assert existing == [present, Path("/dev/null")]
    assert "Skipping missing policy path" in caplog.text
    assert str(missing) in caplog.text
    directories = [path for path in existing if path.is_dir()]
    files = [path for path in existing if not path.is_dir()]
    assert directories == [present]
    assert files == [Path("/dev/null")]
