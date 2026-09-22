"""Tests for SQLite connection, transaction, and migration plumbing."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from tabletop.api.errors import StorageError
from tabletop.storage.sqlite import connect, migrate, transaction


def test_connect_configures_sqlite_pragmas(tmp_path: Path) -> None:
    conn = connect(tmp_path / "test.db")
    try:
        assert conn.row_factory is sqlite3.Row
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_transaction_rolls_back_on_exception(tmp_path: Path) -> None:
    conn = connect(tmp_path / "test.db")
    try:
        with pytest.raises(RuntimeError, match="boom"):
            with transaction(conn):
                conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
                conn.execute("INSERT INTO t (id) VALUES (1)")
                raise RuntimeError("boom")

        with pytest.raises(sqlite3.OperationalError):
            conn.execute("SELECT 1 FROM t")
    finally:
        conn.close()


def test_migrate_applies_migrations_and_records_them(tmp_path: Path) -> None:
    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    (mig_dir / "001_first.sql").write_text(
        "CREATE TABLE first_table (id INTEGER PRIMARY KEY);"
    )
    (mig_dir / "002_second.sql").write_text(
        "CREATE TABLE second_table (id INTEGER PRIMARY KEY);"
    )

    conn = connect(tmp_path / "test.db")
    try:
        applied = migrate(conn, directory=mig_dir)
        assert applied == ("001_first.sql", "002_second.sql")

        rows = conn.execute(
            "SELECT filename, checksum FROM schema_migrations ORDER BY filename"
        ).fetchall()
        assert [row["filename"] for row in rows] == ["001_first.sql", "002_second.sql"]
        for row in rows:
            expected = hashlib.sha256(
                (mig_dir / row["filename"]).read_bytes()
            ).hexdigest()
            assert row["checksum"] == expected

        conn.execute("SELECT 1 FROM first_table")
        conn.execute("SELECT 1 FROM second_table")
    finally:
        conn.close()


def test_migrate_second_call_applies_nothing(tmp_path: Path) -> None:
    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    (mig_dir / "001_only.sql").write_text(
        "CREATE TABLE only_table (id INTEGER PRIMARY KEY);"
    )

    conn = connect(tmp_path / "test.db")
    try:
        assert migrate(conn, directory=mig_dir) == ("001_only.sql",)
        assert migrate(conn, directory=mig_dir) == ()
    finally:
        conn.close()


def test_migrate_checksum_mismatch_raises(tmp_path: Path) -> None:
    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    migration = mig_dir / "001_first.sql"
    migration.write_text("CREATE TABLE first_table (id INTEGER PRIMARY KEY);")

    conn = connect(tmp_path / "test.db")
    try:
        migrate(conn, directory=mig_dir)
        migration.write_text("CREATE TABLE changed (id INTEGER PRIMARY KEY);")
        with pytest.raises(StorageError):
            migrate(conn, directory=mig_dir)
    finally:
        conn.close()
