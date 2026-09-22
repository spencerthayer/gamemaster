"""Lexical retrieval over SQLite FTS5.

One FTS5 virtual table per ``RetrievalNamespace``. Table names come from a
validated enum map in this module. Caller-supplied namespace strings are never
interpolated into SQL.
"""

from __future__ import annotations

import json
import re
import sqlite3
from types import MappingProxyType
from typing import Mapping

from tabletop.api.errors import StorageError
from tabletop.api.visibility import VisibilityScopeError, can_see, parse_scope
from tabletop.retrieval.models import (
    RetrievalFilters,
    RetrievalNamespace,
    RetrievedChunk,
    SourceReference,
)

_FTS5_COMPILE_OPTION = "ENABLE_FTS5"

# Validated enum -> fixed table name. Never build this from a raw string.
_FTS_TABLES: Mapping[RetrievalNamespace, str] = MappingProxyType(
    {
        RetrievalNamespace.SYSTEM: "fts_system",
        RetrievalNamespace.SETTING: "fts_setting",
        RetrievalNamespace.ADVENTURE: "fts_adventure",
        RetrievalNamespace.CAMPAIGN: "fts_campaign",
        RetrievalNamespace.RULINGS: "fts_rulings",
        RetrievalNamespace.CHARACTER: "fts_character",
        RetrievalNamespace.NPC: "fts_npc",
    }
)

_FTS5_TOKEN_SPLIT = re.compile(r"\s+")


def load_visible_chunk(
    connection: sqlite3.Connection,
    chunk_id: str,
    *,
    viewpoint,
) -> dict[str, object] | None:
    """Return one chunk when ``viewpoint`` can see its visibility scope.

    Chunks are library records, not campaign rows. A hidden chunk is
    indistinguishable from a missing one.
    """

    row = connection.execute(
        "SELECT c.chunk_id, c.document_id, c.text, c.heading_path, c.page, "
        "c.visibility, d.title, d.source_path "
        "FROM document_chunks AS c "
        "JOIN documents AS d ON d.document_id = c.document_id "
        "WHERE c.chunk_id = ?",
        (chunk_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        visible = can_see(viewpoint, parse_scope(str(row["visibility"])))
    except VisibilityScopeError:
        return None
    if not visible:
        return None
    return {
        "chunk_id": row["chunk_id"],
        "document_id": row["document_id"],
        "text": row["text"],
        "section": str(row["heading_path"]),
        "page": row["page"],
        "document_title": row["title"],
        "source_path": row["source_path"],
        "visibility": row["visibility"],
    }


def escape_fts5_query(query: str) -> str:
    """Escape user input so FTS5 treats tokens as literals, not operators."""
    tokens = [token for token in _FTS5_TOKEN_SPLIT.split(query.strip()) if token]
    if not tokens:
        return '""'
    return " ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def _sqlite_compile_options(connection: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        row[0] for row in connection.execute("PRAGMA compile_options").fetchall()
    )


def _require_fts5(connection: sqlite3.Connection) -> None:
    if _FTS5_COMPILE_OPTION not in _sqlite_compile_options(connection):
        raise StorageError(
            f"SQLite was built without FTS5 "
            f"(missing compile option {_FTS5_COMPILE_OPTION})"
        )


def _table_for(namespace: RetrievalNamespace) -> str:
    try:
        return _FTS_TABLES[namespace]
    except KeyError as exc:
        raise ValueError(f"unknown retrieval namespace: {namespace!r}") from exc


def _format_section(section: str | tuple[str, ...] | list[str]) -> str:
    if isinstance(section, str):
        return section
    return " > ".join(section)


def _heading_path_json(section: str | tuple[str, ...] | list[str]) -> str:
    if isinstance(section, str):
        parts = [part.strip() for part in section.split(">") if part.strip()]
        return json.dumps(parts)
    return json.dumps(list(section))


def _page_to_storage(page: int | None) -> str:
    if page is None:
        return ""
    return str(page)


def _page_from_storage(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


class LexicalRetriever:
    """FTS5-backed ``Retriever`` with one corpus table per namespace."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        _require_fts5(connection)
        self._connection = connection

    def index(
        self,
        namespace: RetrievalNamespace,
        *,
        chunk_id: str,
        document_id: str,
        document_title: str,
        section: str | tuple[str, ...] | list[str],
        text: str,
        page: int | None = None,
        source_path: str = "",
        content_pack_id: str | None = None,
        system_id: str | None = None,
        visibility: str = "GM",
    ) -> None:
        """Insert or replace one chunk in the namespace FTS table."""
        if not isinstance(namespace, RetrievalNamespace):
            raise TypeError(
                f"namespace must be RetrievalNamespace, got {type(namespace).__name__}"
            )
        table = _table_for(namespace)
        self._connection.execute(
            f"DELETE FROM {table} WHERE chunk_id = ?",
            (chunk_id,),
        )
        self._connection.execute(
            f"INSERT INTO {table} "
            "(chunk_id, document_id, document_title, heading_path, page, "
            "source_path, content_pack_id, system_id, visibility, text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chunk_id,
                document_id,
                document_title,
                _heading_path_json(section),
                _page_to_storage(page),
                source_path,
                content_pack_id or "",
                system_id or "",
                visibility,
                text,
            ),
        )

    def fetch(self, source: SourceReference) -> RetrievedChunk | None:
        """Reload a chunk by source reference across all namespace tables."""
        chunk_id = source.refetch_args["chunk_id"]
        for namespace, table in _FTS_TABLES.items():
            row = self._connection.execute(
                f"SELECT chunk_id, document_id, document_title, heading_path, "
                f"page, source_path, text FROM {table} WHERE chunk_id = ?",
                (chunk_id,),
            ).fetchone()
            if row is None:
                continue
            return RetrievedChunk(
                text=row["text"],
                score=0.0,
                namespace=namespace,
                source=self._source_from_row(row),
            )
        return None

    def search(
        self,
        query: str,
        filters: RetrievalFilters,
        limit: int,
    ) -> tuple[RetrievedChunk, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if not isinstance(filters, RetrievalFilters):
            raise TypeError(
                f"filters must be RetrievalFilters, got {type(filters).__name__}"
            )

        table = _table_for(filters.namespace)
        match_query = escape_fts5_query(query)
        sql = (
            f"SELECT chunk_id, document_id, document_title, heading_path, page, "
            f"source_path, content_pack_id, system_id, visibility, text, "
            f"(-bm25({table})) AS score "
            f"FROM {table} "
            f"WHERE {table} MATCH ? "
            f"AND (? IS NULL OR content_pack_id = ?) "
            f"AND (? IS NULL OR system_id = ?) "
            f"AND (? IS NULL OR visibility = ?) "
            f"ORDER BY score DESC "
            f"LIMIT ?"
        )
        rows = self._connection.execute(
            sql,
            (
                match_query,
                filters.content_pack_id,
                filters.content_pack_id or "",
                filters.system_id,
                filters.system_id or "",
                filters.visibility,
                filters.visibility or "",
                limit,
            ),
        ).fetchall()

        return tuple(
            RetrievedChunk(
                text=row["text"],
                score=float(row["score"]),
                namespace=filters.namespace,
                source=self._source_from_row(row),
            )
            for row in rows
        )

    @staticmethod
    def _source_from_row(row: sqlite3.Row) -> SourceReference:
        heading_raw = row["heading_path"]
        try:
            parts = json.loads(heading_raw)
            section = (
                _format_section(parts) if isinstance(parts, list) else str(heading_raw)
            )
        except (TypeError, json.JSONDecodeError):
            section = str(heading_raw)
        return SourceReference(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            document_title=row["document_title"],
            section=section,
            page=_page_from_storage(row["page"]),
            source_path=row["source_path"],
            refetch_tool="get-chunk",
        )
