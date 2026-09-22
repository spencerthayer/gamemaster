"""Trust-boundary claims documented in docs/security.md must hold in code."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tabletop.api.errors import ContentPackError, PluginManifestError
from tabletop.api.workspace import Workspace
from tabletop.documents.content_pack import load_content_pack
from tabletop.documents.library import DocumentLibrary
from tabletop.documents.markdown import MarkdownIngestor
from tabletop.documents.pdf import PdfIngestor
from tabletop.plugins.discovery import discover_plugins
from tabletop.plugins.manifest import load_manifest
from tabletop.runtime import PLUGIN_PATH_ENV_VAR, TabletopRuntime

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SECURITY_DOC = _REPO_ROOT / "docs" / "security.md"

_BANNED_IMPORTER_TOPLEVEL = {
    "providers",
    "httpx",
    "requests",
    "aiohttp",
    "openai",
    "anthropic",
    "credentials",
    "boto3",
}

_PLUGIN_MANIFEST = """\
id: freeform
name: Freeform
api_version: tabletop/v1
version: 0.1.0
entrypoint: freeform:FreeformPlugin
"""

_CONTENT_MANIFEST = """\
id: core-rules
name: Core Rules
pack_type: rules
system_id: freeform
version: 1.2.3
"""


def _write_content_manifest(directory: Path, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "content-pack.yaml").write_text(text)


def _library(tmp_path: Path) -> tuple[DocumentLibrary, Path, Path]:
    raw_root = tmp_path / "raw"
    processed_root = tmp_path / "processed"
    raw_root.mkdir()
    return DocumentLibrary(raw_root, processed_root), raw_root, processed_root


def _toplevel_imports(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text())
    tops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            tops.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            tops.add(node.module.split(".")[0])
    return tops


def _assert_no_eval_exec_or_sql_interpolation(module_path: Path) -> None:
    source = module_path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in {"eval", "exec", "compile"}:
                pytest.fail(f"{module_path.name} calls {func.id}()")
            if isinstance(func, ast.Attribute) and func.attr == "execute":
                if not node.args:
                    continue
                sql_arg = node.args[0]
                if isinstance(sql_arg, ast.JoinedStr):
                    pytest.fail(
                        f"{module_path.name} interpolates SQL via f-string at "
                        f"line {node.lineno}"
                    )
                if isinstance(sql_arg, ast.BinOp) and isinstance(
                    sql_arg.op, (ast.Add, ast.Mod)
                ):
                    pytest.fail(
                        f"{module_path.name} builds SQL by concatenation or % "
                        f"formatting at line {node.lineno}"
                    )
                if len(node.args) < 2:
                    # Parameterless DDL/static statements are fine; values from
                    # document text must never be spliced into the SQL string.
                    if isinstance(sql_arg, ast.Constant) and isinstance(
                        sql_arg.value, str
                    ):
                        continue
                    pytest.fail(
                        f"{module_path.name} execute() at line {node.lineno} "
                        "has no bound parameters"
                    )


def test_security_doc_states_required_threat_model_topics() -> None:
    assert _SECURITY_DOC.is_file(), "docs/security.md is missing"
    text = _SECURITY_DOC.read_text().lower()
    required = (
        "prompt injection",
        "untrusted",
        "plugin",
        "read-only",
        "docker socket",
        "discovery",
        "path traversal",
        "secret",
        "extraction",
        "import",
    )
    missing = [topic for topic in required if topic not in text]
    assert missing == [], f"docs/security.md missing topics: {missing}"


@pytest.mark.parametrize(
    "logical_path",
    ["../outside.md", "/tmp/outside.md", "subdir/../../outside.md"],
)
def test_document_library_rejects_traversal_and_absolute_paths(
    tmp_path: Path, logical_path: str
) -> None:
    library, _, _ = _library(tmp_path)
    with pytest.raises(ValueError, match="outside document root"):
        library.lookup(logical_path)


def test_document_library_rejects_symlink_escape(tmp_path: Path) -> None:
    library, raw_root, _ = _library(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("secret")
    (raw_root / "linked.md").symlink_to(outside)
    with pytest.raises(ValueError, match="outside document root"):
        library.lookup("linked.md")


@pytest.mark.parametrize(
    "gm_only",
    ["../outside.md", "/tmp/outside.md", "subdir/../../outside.md"],
)
def test_content_pack_rejects_traversal_and_absolute_paths(
    tmp_path: Path, gm_only: str
) -> None:
    _write_content_manifest(
        tmp_path, _CONTENT_MANIFEST + f"gm_only:\n  - {gm_only}\n"
    )
    with pytest.raises(ContentPackError, match="outside pack directory|absolute"):
        load_content_pack(tmp_path)


def test_content_pack_rejects_absolute_path_even_when_inside_pack(
    tmp_path: Path,
) -> None:
    target = tmp_path / "secrets" / "notes.md"
    target.parent.mkdir()
    target.write_text("secret")
    absolute = str(target.resolve())
    _write_content_manifest(
        tmp_path, _CONTENT_MANIFEST + f"gm_only:\n  - {absolute}\n"
    )
    with pytest.raises(ContentPackError, match="absolute"):
        load_content_pack(tmp_path)


def test_content_pack_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret.md"
    outside.write_text("secret")
    (tmp_path / "linked-secret.md").symlink_to(outside)
    _write_content_manifest(
        tmp_path, _CONTENT_MANIFEST + "gm_only:\n  - linked-secret.md\n"
    )
    with pytest.raises(ContentPackError, match="outside pack directory"):
        load_content_pack(tmp_path)


def test_plugin_discovery_rejects_symlink_escape(
    tmp_path: Path, make_plugin
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    make_plugin(outside, "evil")
    (tmp_path / "link").symlink_to(outside / "evil", target_is_directory=True)
    with pytest.raises(PluginManifestError, match="outside configured root"):
        discover_plugins([tmp_path])


def test_plugin_discovery_rejects_traversal_via_dotdot_symlink(
    tmp_path: Path, make_plugin
) -> None:
    """A child that resolves through .. must not be treated as in-root."""
    root = tmp_path / "plugins"
    sibling = tmp_path / "sibling"
    root.mkdir()
    sibling.mkdir()
    make_plugin(sibling, "escaped")
    (root / "escaped").symlink_to(Path("..") / "sibling" / "escaped")
    with pytest.raises(PluginManifestError, match="outside configured root"):
        discover_plugins([root])


def test_plugin_manifest_rejects_absolute_and_traversal_entrypoints(
    tmp_path: Path,
) -> None:
    for entrypoint in ("/abs/path:Thing", "../../plugin:Thing"):
        text = _PLUGIN_MANIFEST.replace(
            "entrypoint: freeform:FreeformPlugin", f"entrypoint: {entrypoint}"
        )
        path = tmp_path / "plugin.yaml"
        path.write_text(text)
        with pytest.raises(PluginManifestError, match="entrypoint"):
            load_manifest(path)


def test_both_manifest_loaders_reject_unknown_fields(tmp_path: Path) -> None:
    plugin_path = tmp_path / "plugin.yaml"
    plugin_path.write_text(_PLUGIN_MANIFEST + "extra_field: nope\n")
    with pytest.raises(PluginManifestError, match="unknown fields"):
        load_manifest(plugin_path)

    _write_content_manifest(tmp_path / "pack", _CONTENT_MANIFEST + "entrypoint: x:Y\n")
    with pytest.raises(ContentPackError, match="unknown fields|entrypoint"):
        load_content_pack(tmp_path / "pack")


def test_both_manifest_loaders_reject_unsafe_yaml_tags(tmp_path: Path) -> None:
    plugin_path = tmp_path / "plugin.yaml"
    plugin_path.write_text(
        "id: !!python/object/apply:os.system ['true']\n"
        "name: x\napi_version: tabletop/v1\nentrypoint: a:B\n"
    )
    with pytest.raises(PluginManifestError):
        load_manifest(plugin_path)

    sentinel = tmp_path / "yaml-executed"
    _write_content_manifest(
        tmp_path / "pack",
        (
            f"id: !!python/object/apply:pathlib.Path.touch ['{sentinel}']\n"
            "name: Core Rules\n"
            "pack_type: rules\n"
            "system_id: freeform\n"
            "version: 1.2.3\n"
        ),
    )
    with pytest.raises(ContentPackError):
        load_content_pack(tmp_path / "pack")
    assert not sentinel.exists()


def test_plugin_roots_are_separate_from_document_and_content_roots(
    tmp_path: Path, make_plugin
) -> None:
    raw_root = tmp_path / "documents" / "raw"
    processed_root = tmp_path / "documents" / "processed"
    content_root = tmp_path / "content"
    plugin_root = tmp_path / "plugins"
    raw_root.mkdir(parents=True)
    processed_root.mkdir(parents=True)
    content_root.mkdir()
    plugin_root.mkdir()

    # A document root that looks like a plugin must not be scanned for entrypoints.
    make_plugin(raw_root, "sneaky-doc-plugin")
    _write_content_manifest(content_root, _CONTENT_MANIFEST)
    make_plugin(plugin_root, "trusted")

    library = DocumentLibrary(raw_root, processed_root)
    assert not hasattr(library, "discover_plugins")
    assert "entrypoint" not in dir(library)

    runtime = TabletopRuntime(
        tmp_path,
        workspace=Workspace.SETTING,
        plugin_roots=[plugin_root],
        campaign_roots=[tmp_path / "campaigns"],
    )
    (tmp_path / "campaigns").mkdir()

    assert plugin_root.resolve() in runtime.plugin_roots
    assert raw_root.resolve() not in runtime.plugin_roots
    assert processed_root.resolve() not in runtime.plugin_roots
    assert content_root.resolve() not in runtime.plugin_roots
    assert {plugin.info.id for plugin in runtime._registry.list()} == {"trusted"}

    # Scanning only the document root would find the decoy; the runtime does not.
    decoys = discover_plugins([raw_root])
    assert [candidate.manifest.id for candidate in decoys] == ["sneaky-doc-plugin"]
    trusted_only = discover_plugins(runtime.plugin_roots)
    assert [candidate.manifest.id for candidate in trusted_only] == ["trusted"]


def test_document_root_is_never_searched_for_entrypoints_by_default(
    tmp_path: Path, make_plugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    raw_root = repo / "library" / "raw"
    plugin_root = repo / "plugins"
    raw_root.mkdir(parents=True)
    plugin_root.mkdir(parents=True)
    # from_environment always lists <repo>/systems; keep it present but empty.
    (repo / "systems").mkdir()
    make_plugin(raw_root, "from-docs")
    make_plugin(plugin_root, "from-plugins")
    monkeypatch.setenv("TABLETOP_WORKSPACE", "setting")
    monkeypatch.setenv(PLUGIN_PATH_ENV_VAR, str(plugin_root))
    monkeypatch.delenv("TABLETOP_CAMPAIGN_PATHS", raising=False)

    runtime = TabletopRuntime.from_environment(repo)
    ids = {plugin.info.id for plugin in runtime._registry.list()}
    assert "from-plugins" in ids
    assert "from-docs" not in ids


def test_importer_module_graph_contains_no_credential_or_network_module() -> None:
    module_path = _REPO_ROOT / "tabletop" / "documents" / "importer.py"
    tops = _toplevel_imports(module_path)
    assert tops.isdisjoint(_BANNED_IMPORTER_TOPLEVEL)

    probe = (
        "import sys\n"
        "import tabletop.documents.importer\n"
        f"banned = {sorted(_BANNED_IMPORTER_TOPLEVEL)!r}\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in banned]\n"
        "assert not bad, bad\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=str(_REPO_ROOT)),
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "module_path",
    [
        _REPO_ROOT / "tabletop" / "documents" / "markdown.py",
        _REPO_ROOT / "tabletop" / "documents" / "pdf.py",
        _REPO_ROOT / "tabletop" / "documents" / "importer.py",
    ],
)
def test_ingested_and_imported_text_is_never_evaluated_or_sql_interpolated(
    module_path: Path,
) -> None:
    assert module_path.is_file()
    _assert_no_eval_exec_or_sql_interpolation(module_path)
    # Ingestors exist and accept only file paths, not executable payloads.
    assert callable(MarkdownIngestor().supports)
    assert callable(PdfIngestor().supports)
