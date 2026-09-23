"""CLI tests for library and content-pack commands."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(database: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["TABLETOP_DATABASE_PATH"] = str(database)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("TABLETOP_CAMPAIGN", None)
    return env


def _run(
    *args: str,
    database: Path,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tabletop.cli", *args],
        cwd=cwd or REPO_ROOT,
        env=_env(database),
        capture_output=True,
        text=True,
        check=False,
    )


def test_library_ingest_and_document_add(tmp_path: Path) -> None:
    database = tmp_path / "campaign.db"
    assert (
        _run(
            "campaign",
            "create",
            "--id",
            "night",
            "--name",
            "Night",
            "--system",
            "freeform",
            database=database,
            cwd=tmp_path,
        ).returncode
        == 0
    )
    notes = tmp_path / "notes.md"
    notes.write_text("# Notes\n\nA short note about the gate.\n", encoding="utf-8")
    ingest = _run(
        "library",
        "ingest",
        "notes.md",
        "--campaign",
        "night",
        database=database,
        cwd=tmp_path,
    )
    assert ingest.returncode == 0, ingest.stderr
    assert "ingested document" in ingest.stdout

    more = tmp_path / "more.md"
    more.write_text("# More\n\nAnother note.\n", encoding="utf-8")
    add = _run(
        "campaign",
        "document",
        "add",
        "more.md",
        "--campaign",
        "night",
        database=database,
        cwd=tmp_path,
    )
    assert add.returncode == 0, add.stderr


def test_content_pack_validate_list_ingest(tmp_path: Path) -> None:
    database = tmp_path / "campaign.db"
    assert (
        _run(
            "campaign",
            "create",
            "--id",
            "night",
            "--name",
            "Night",
            "--system",
            "freeform",
            database=database,
            cwd=tmp_path,
        ).returncode
        == 0
    )
    pack = tmp_path / "packs" / "core"
    pack.mkdir(parents=True)
    (pack / "content-pack.yaml").write_text(
        "id: core-rules\n"
        "name: Core Rules\n"
        "pack_type: rules\n"
        "system_id: freeform\n"
        "version: 1.0.0\n",
        encoding="utf-8",
    )
    (pack / "rules.md").write_text("# Rules\n\nDo the thing.\n", encoding="utf-8")

    validate = _run(
        "content-pack",
        "validate",
        str(pack),
        database=database,
        cwd=tmp_path,
    )
    assert validate.returncode == 0, validate.stderr

    bad = tmp_path / "packs" / "bad"
    bad.mkdir(parents=True)
    (bad / "content-pack.yaml").write_text(
        "id: bad\n"
        "name: Bad\n"
        "pack_type: rules\n"
        "system_id: freeform\n"
        "version: 1.0.0\n"
        "description: unexpected\n",
        encoding="utf-8",
    )
    invalid = _run(
        "content-pack",
        "validate",
        str(bad),
        database=database,
        cwd=tmp_path,
    )
    assert invalid.returncode != 0

    listed = _run(
        "content-pack",
        "list",
        "--root",
        str(tmp_path / "packs"),
        database=database,
        cwd=tmp_path,
    )
    assert listed.returncode == 0
    assert "core-rules" in listed.stdout

    ingest = _run(
        "content-pack",
        "ingest",
        str(pack),
        "--campaign",
        "night",
        database=database,
        cwd=tmp_path,
    )
    assert ingest.returncode == 0, ingest.stderr
    assert "core-rules" in ingest.stdout
