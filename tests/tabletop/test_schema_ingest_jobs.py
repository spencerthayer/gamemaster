"""Tests for migration 0003 resumable ingest jobs schema."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.storage.sqlite import connect, migrate

_INGEST_TABLES = ("ingest_jobs", "ingest_slices")


def _seed_ingest_job(
    conn: sqlite3.Connection,
    *,
    job_id: str = "job-1",
    document_hash: str = "abc123",
    parser_version: str = "parser-v1",
    slice_strategy_version: str = "slice-v1",
    status: str = "pending",
    total_slices: int = 10,
    completed_slices: int = 3,
    failed_slices: int = 1,
) -> str:
    conn.execute(
        "INSERT INTO ingest_jobs "
        "(job_id, document_hash, parser_version, slice_strategy_version, "
        "status, total_slices, completed_slices, failed_slices, "
        "started_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            job_id,
            document_hash,
            parser_version,
            slice_strategy_version,
            status,
            total_slices,
            completed_slices,
            failed_slices,
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    return job_id


@pytest.fixture
def migrated_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "schema.db")
    migrate(conn)
    yield conn
    conn.close()


def test_migrate_creates_ingest_tables(migrated_conn: sqlite3.Connection) -> None:
    rows = migrated_conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    table_names = {row["name"] for row in rows}
    for table in _INGEST_TABLES:
        assert table in table_names


def test_resume_identity_is_unique(migrated_conn: sqlite3.Connection) -> None:
    _seed_ingest_job(
        migrated_conn,
        job_id="job-1",
        document_hash="doc-hash",
        parser_version="parser-v1",
        slice_strategy_version="slice-v1",
    )
    with pytest.raises(sqlite3.IntegrityError):
        _seed_ingest_job(
            migrated_conn,
            job_id="job-2",
            document_hash="doc-hash",
            parser_version="parser-v1",
            slice_strategy_version="slice-v1",
        )


def test_job_status_rejects_invalid_values(
    migrated_conn: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        migrated_conn.execute(
            "INSERT INTO ingest_jobs "
            "(job_id, document_hash, parser_version, slice_strategy_version, "
            "status, started_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "job-bad-status",
                "doc-hash",
                "parser-v1",
                "slice-v1",
                "cancelled",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )


def test_slice_cannot_exist_without_job(migrated_conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        migrated_conn.execute(
            "INSERT INTO ingest_slices "
            "(job_id, slice_index, status, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (
                "missing-job",
                0,
                "pending",
                "2026-01-01T00:00:00+00:00",
            ),
        )


def test_completed_and_failed_slices_readable_per_job(
    migrated_conn: sqlite3.Connection,
) -> None:
    job_id = _seed_ingest_job(
        migrated_conn,
        completed_slices=7,
        failed_slices=2,
    )
    row = migrated_conn.execute(
        "SELECT completed_slices, failed_slices FROM ingest_jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert row["completed_slices"] == 7
    assert row["failed_slices"] == 2
