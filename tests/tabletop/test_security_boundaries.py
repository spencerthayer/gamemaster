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
from tabletop.campaign.models import CanonState, KnowledgeState
from tabletop.campaign.store import CampaignStore
from tabletop.documents.content_pack import load_content_pack
from tabletop.documents.extraction import (
    KNOWN_EXTRACTOR_VERSIONS,
    ProposedEntity,
    ProposedExtraction,
    ProposedFact,
)
from tabletop.documents.importer import ImportEnvelopeError, import_extraction
from tabletop.documents.library import DocumentLibrary
from tabletop.documents.markdown import MarkdownIngestor
from tabletop.documents.pdf import PdfIngestor
from tabletop.plugins.discovery import discover_plugins
from tabletop.plugins.manifest import load_manifest
from tabletop.runtime import PLUGIN_PATH_ENV_VAR, TabletopRuntime
from tabletop.storage.sqlite import connect, migrate

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

_SQL_MARKERS = ("SELECT", "INSERT", "UPDATE", "DELETE", "FROM", "INTO", "VALUES")

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


def _looks_like_sql(value: object) -> bool:
    if not isinstance(value, str):
        return False
    upper = value.upper()
    return any(marker in upper for marker in _SQL_MARKERS)


def _is_format_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
    )


def _is_interpolated_sql_expr(node: ast.AST) -> bool:
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return True
    if _is_format_call(node):
        return True
    return False


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
                if _is_interpolated_sql_expr(sql_arg):
                    pytest.fail(
                        f"{module_path.name} builds SQL via f-string, %, "
                        f"concatenation, or str.format at line {node.lineno}"
                    )
                if len(node.args) < 2:
                    # Parameterless static statements are fine; values from
                    # document text must never be spliced into the SQL string.
                    if isinstance(sql_arg, ast.Constant) and isinstance(
                        sql_arg.value, str
                    ):
                        continue
                    pytest.fail(
                        f"{module_path.name} execute() at line {node.lineno} "
                        "has no bound parameters"
                    )

        # Reject SQL templates built with % or str.format anywhere in the module,
        # even before they are passed to execute().
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            if isinstance(node.left, ast.Constant) and _looks_like_sql(node.left.value):
                pytest.fail(
                    f"{module_path.name} percent-interpolates SQL at line {node.lineno}"
                )
        if _is_format_call(node):
            receiver = node.func.value  # type: ignore[attr-defined]
            if isinstance(receiver, ast.Constant) and _looks_like_sql(receiver.value):
                pytest.fail(
                    f"{module_path.name} builds SQL with str.format at line {node.lineno}"
                )
        if isinstance(node, ast.JoinedStr):
            text = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            if _looks_like_sql(text):
                pytest.fail(
                    f"{module_path.name} builds SQL with an f-string at line {node.lineno}"
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

    _write_content_manifest(
        tmp_path / "pack", _CONTENT_MANIFEST + "nonsense_field: unexpected\n"
    )
    with pytest.raises(ContentPackError, match="unknown fields"):
        load_content_pack(tmp_path / "pack")


def test_content_pack_rejects_entrypoint_field(tmp_path: Path) -> None:
    _write_content_manifest(tmp_path, _CONTENT_MANIFEST + "entrypoint: pack_code:run\n")
    with pytest.raises(ContentPackError, match="unknown fields"):
        load_content_pack(tmp_path)


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


def test_plugin_discovery_is_immediate_children_only(
    tmp_path: Path, make_plugin
) -> None:
    make_plugin(tmp_path, "alpha")
    make_plugin(tmp_path / "nested", "beta")
    candidates = discover_plugins([tmp_path])
    assert [candidate.manifest.id for candidate in candidates] == ["alpha"]


def test_discover_plugins_never_executes_plugin_python(
    tmp_path: Path, make_plugin
) -> None:
    sentinel = tmp_path / "discovery-imported"
    make_plugin(tmp_path, "sideeffect")
    module_file = tmp_path / "sideeffect" / "sideeffect" / "__init__.py"
    module_file.write_text(
        module_file.read_text()
        + f"\nimport pathlib\npathlib.Path({str(sentinel)!r}).write_text('imported')\n"
    )
    candidates = discover_plugins([tmp_path])
    assert [candidate.manifest.id for candidate in candidates] == ["sideeffect"]
    assert not sentinel.exists(), "discover_plugins executed plugin Python"


def test_content_pack_load_never_imports_pack_python(tmp_path: Path) -> None:
    sentinel = tmp_path / "pack-python-imported"
    (tmp_path / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('imported')\n"
    )
    _write_content_manifest(tmp_path, _CONTENT_MANIFEST)
    load_content_pack(tmp_path)
    assert not sentinel.exists(), "content-pack load imported pack Python"


def test_document_library_has_no_raw_mutation_api(tmp_path: Path) -> None:
    library, _, _ = _library(tmp_path)
    public_methods = {
        name
        for name in dir(library)
        if not name.startswith("_") and callable(getattr(library, name))
    }
    assert public_methods == {"lookup"}


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


@pytest.fixture
def import_conn(tmp_path: Path):
    connection = connect(tmp_path / "security-import.db")
    migrate(connection)
    connection.execute(
        "INSERT INTO settings (setting_id, name, created_at) VALUES (?, ?, ?)",
        ("setting-1", "Test Setting", "2026-09-22T00:00:00Z"),
    )
    CampaignStore(connection).create_campaign(
        "campaign-1",
        "First",
        "test",
        setting_id="setting-1",
    )
    connection.execute(
        "INSERT INTO ingest_jobs "
        "(job_id, document_hash, parser_version, slice_strategy_version, "
        "status, total_slices, completed_slices, failed_slices, "
        "started_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'completed', 1, 1, 0, ?, ?)",
        (
            "job-1",
            "hash-1",
            "1",
            "1",
            "2026-09-22T00:00:00Z",
            "2026-09-22T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO documents "
        "(document_id, content_hash, source_path, title, document_shape, "
        "content_pack_id, system_id, visibility, ingested_at) "
        "VALUES (?, ?, ?, ?, 'prose', NULL, NULL, 'GM', ?)",
        (
            "document-1",
            "doc-hash-1",
            "/tmp/doc.md",
            "Doc",
            "2026-09-22T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO document_chunks "
        "(chunk_id, document_id, ordinal, heading_path, page, text, "
        "content_hash, content_pack_id, system_id, visibility) "
        "VALUES (?, ?, 0, ?, NULL, ?, ?, NULL, NULL, 'GM')",
        (
            "chunk-1",
            "document-1",
            '["Doc"]',
            "Mara lives in Greyhaven.",
            "chunk-hash-1",
        ),
    )
    try:
        yield connection
    finally:
        connection.close()


def test_importer_rejects_bad_envelope_and_imports_facts_as_non_canon(
    import_conn,
) -> None:
    with pytest.raises(ImportEnvelopeError, match="unknown fields"):
        import_extraction(
            import_conn,
            {
                "job_id": "job-1",
                "slice_index": 0,
                "extractor_version": next(iter(KNOWN_EXTRACTOR_VERSIONS)),
                "entities": [],
                "facts": [],
                "extra": True,
            },
        )
    assert import_conn.execute("SELECT COUNT(*) AS n FROM facts").fetchone()["n"] == 0

    with pytest.raises(ImportEnvelopeError, match="extractor_version"):
        import_extraction(
            import_conn,
            ProposedExtraction(
                job_id="job-1",
                slice_index=0,
                extractor_version="not-a-known-extractor",
                entities=(),
                facts=(
                    ProposedFact(
                        fact_id="fact-1",
                        fact_scope="campaign",
                        setting_id=None,
                        campaign_id="campaign-1",
                        subject_id="mara",
                        predicate="lives_in",
                        value="Greyhaven",
                        source_document_id="document-1",
                        source_chunk_id="chunk-1",
                    ),
                ),
            ),
        )
    assert import_conn.execute("SELECT COUNT(*) AS n FROM facts").fetchone()["n"] == 0

    report = import_extraction(
        import_conn,
        ProposedExtraction(
            job_id="job-1",
            slice_index=0,
            extractor_version=next(iter(KNOWN_EXTRACTOR_VERSIONS)),
            entities=(
                ProposedEntity(
                    entity_id="mara",
                    owner_scope="campaign",
                    setting_id=None,
                    campaign_id="campaign-1",
                    overrides_id=None,
                    entity_type="npc",
                    name="Mara",
                    system_state={},
                    metadata={},
                ),
            ),
            facts=(
                ProposedFact(
                    fact_id="fact-1",
                    fact_scope="campaign",
                    setting_id=None,
                    campaign_id="campaign-1",
                    subject_id="mara",
                    predicate="lives_in",
                    value="Greyhaven",
                    source_document_id="document-1",
                    source_chunk_id="chunk-1",
                ),
            ),
        ),
    )
    assert "fact-1" in report.accepted_ids
    row = import_conn.execute(
        "SELECT canon_state, knowledge_state FROM facts WHERE fact_id = ?",
        ("fact-1",),
    ).fetchone()
    assert row["canon_state"] == CanonState.PROPOSED.value
    assert row["knowledge_state"] == KnowledgeState.UNREVEALED.value


def test_importer_rejects_one_proposal_while_sibling_still_imports(
    import_conn,
) -> None:
    report = import_extraction(
        import_conn,
        ProposedExtraction(
            job_id="job-1",
            slice_index=0,
            extractor_version=next(iter(KNOWN_EXTRACTOR_VERSIONS)),
            entities=(
                ProposedEntity(
                    entity_id="mara",
                    owner_scope="campaign",
                    setting_id=None,
                    campaign_id="campaign-1",
                    overrides_id=None,
                    entity_type="npc",
                    name="Mara",
                    system_state={},
                    metadata={},
                ),
                ProposedEntity(
                    entity_id="fragment",
                    owner_scope="campaign",
                    setting_id=None,
                    campaign_id="campaign-1",
                    overrides_id=None,
                    entity_type="npc",
                    name="lives in the...",
                    system_state={},
                    metadata={},
                ),
            ),
            facts=(
                ProposedFact(
                    fact_id="fact-ok",
                    fact_scope="campaign",
                    setting_id=None,
                    campaign_id="campaign-1",
                    subject_id="mara",
                    predicate="lives_in",
                    value="Greyhaven",
                    source_document_id="document-1",
                    source_chunk_id="chunk-1",
                ),
                ProposedFact(
                    fact_id="fact-refused",
                    fact_scope="campaign",
                    setting_id=None,
                    campaign_id="campaign-1",
                    subject_id="unknown",
                    predicate="is",
                    value="placeholder",
                    source_document_id="document-1",
                    source_chunk_id="chunk-1",
                ),
            ),
        ),
    )

    assert report.accepted_ids == ("mara", "fact-ok")
    reasons = {item.proposal_id: item.reason for item in report.rejected}
    assert set(reasons) == {"fragment", "fact-refused"}
    assert reasons["fragment"]
    assert reasons["fact-refused"]
    assert "fragment" in reasons["fragment"].lower() or "name" in reasons["fragment"].lower()
    assert "subject" in reasons["fact-refused"].lower()

    rows = import_conn.execute(
        "SELECT fact_id FROM facts ORDER BY fact_id"
    ).fetchall()
    assert [row["fact_id"] for row in rows] == ["fact-ok"]
    entity = import_conn.execute(
        "SELECT entity_id FROM entities WHERE entity_id = ?",
        ("mara",),
    ).fetchone()
    assert entity is not None
    assert (
        import_conn.execute(
            "SELECT 1 FROM entities WHERE entity_id = ?",
            ("fragment",),
        ).fetchone()
        is None
    )


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
