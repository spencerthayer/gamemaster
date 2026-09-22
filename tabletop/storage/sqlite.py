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
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


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
        row = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE filename = ?",
            (filename,),
        ).fetchone()

        if row is not None:
            if row["checksum"] != checksum:
                raise StorageError(
                    f"migration {filename!r} was modified after it was applied"
                )
            continue

        sql = path.read_text()
        with transaction(conn):
            for statement in sql.split(";"):
                stripped = statement.strip()
                if stripped:
                    conn.execute(stripped)
            conn.execute(
                "INSERT INTO schema_migrations (filename, checksum, applied_at) "
                "VALUES (?, ?, ?)",
                (filename, checksum, datetime.now(timezone.utc).isoformat()),
            )
        applied.append(filename)

    return tuple(applied)
