"""SQLite connection management.

Connection lifecycle, pragmas, transaction boundaries, and schema access for
the campaign store, event log, and retrieval mirrors. SQLite over external
infrastructure. Phase 11 of the execution plan fills this in.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from tabletop.api.errors import StorageError

_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _strip_sql_comments(sql: str) -> str:
    result: list[str] = []
    index = 0
    length = len(sql)
    in_single_quote = False
    in_double_quote = False

    while index < length:
        char = sql[index]

        if in_single_quote:
            result.append(char)
            if char == "'":
                if index + 1 < length and sql[index + 1] == "'":
                    result.append(sql[index + 1])
                    index += 2
                    continue
                in_single_quote = False
            index += 1
            continue

        if in_double_quote:
            result.append(char)
            if char == '"':
                in_double_quote = False
            index += 1
            continue

        if char == "'":
            in_single_quote = True
            result.append(char)
            index += 1
            continue

        if char == '"':
            in_double_quote = True
            result.append(char)
            index += 1
            continue

        if char == "-" and index + 1 < length and sql[index + 1] == "-":
            while index < length and sql[index] != "\n":
                index += 1
            continue

        if char == "/" and index + 1 < length and sql[index + 1] == "*":
            index += 2
            while index + 1 < length and not (sql[index] == "*" and sql[index + 1] == "/"):
                index += 1
            index = min(index + 2, length)
            continue

        result.append(char)
        index += 1

    return "".join(result)


def _strip_leading_sql_noise(sql: str) -> str:
    index = 0
    length = len(sql)
    while index < length:
        while index < length and sql[index] in " \t\r\n":
            index += 1
        if index >= length:
            break
        if sql[index : index + 2] == "--":
            while index < length and sql[index] != "\n":
                index += 1
            continue
        if sql[index : index + 2] == "/*":
            end = sql.find("*/", index + 2)
            if end == -1:
                break
            index = end + 2
            continue
        break
    return sql[index:]


def _is_sql_insignificant(sql: str) -> bool:
    return not _strip_sql_comments(sql).strip()


def _execute_migration_sql(conn: sqlite3.Connection, sql: str) -> None:
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        while not _is_sql_insignificant(buffer):
            buffer = _strip_leading_sql_noise(buffer)
            if _is_sql_insignificant(buffer):
                break
            if not sqlite3.complete_statement(buffer):
                break
            semicolon_index = buffer.find(";")
            while semicolon_index >= 0:
                statement = buffer[: semicolon_index + 1]
                if sqlite3.complete_statement(statement):
                    stripped = statement.strip()
                    if stripped:
                        conn.execute(stripped)
                    buffer = _strip_leading_sql_noise(buffer[semicolon_index + 1 :])
                    break
                semicolon_index = buffer.find(";", semicolon_index + 1)
            else:
                break
    remainder = _strip_leading_sql_noise(buffer)
    if _is_sql_insignificant(remainder):
        return
    if not sqlite3.complete_statement(remainder):
        raise StorageError("migration SQL ends with an incomplete statement")
    conn.execute(remainder)


def migrate(
    conn: sqlite3.Connection,
    directory: Path | None = None,
) -> tuple[str, ...]:
    if directory is None:
        directory = Path(__file__).parent / "migrations"

    conn.execute(_SCHEMA_MIGRATIONS_DDL)

    applied: list[str] = []
    for path in sorted(directory.glob("*.sql")):
        filename = path.name
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        sql = path.read_text()
        did_apply = False
        with transaction(conn):
            row = conn.execute(
                "SELECT checksum FROM schema_migrations WHERE filename = ?",
                (filename,),
            ).fetchone()

            if row is not None:
                if row["checksum"] != checksum:
                    raise StorageError(
                        f"migration {filename!r} was modified after it was applied"
                    )
            else:
                _execute_migration_sql(conn, sql)
                conn.execute(
                    "INSERT INTO schema_migrations (filename, checksum, applied_at) "
                    "VALUES (?, ?, ?)",
                    (filename, checksum, datetime.now(timezone.utc).isoformat()),
                )
                did_apply = True
        if did_apply:
            applied.append(filename)

    return tuple(applied)
