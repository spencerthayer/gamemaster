"""Durable, content-addressed ingestion job tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tabletop.documents.jobs import estimate_job, start_or_resume_job
from tabletop.storage.sqlite import connect, migrate


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "jobs.db")
    migrate(connection)
    yield connection
    connection.close()


def test_job_identity_includes_content_and_versions(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    source = tmp_path / "rules.txt"
    source.write_bytes(b"abcdefgh")
    extractor = lambda _index, _content: None

    first = start_or_resume_job(
        conn,
        source,
        extractor,
        slice_size=4,
        parser_version="parser-1",
        slice_strategy_version="slices-1",
    )
    resumed = start_or_resume_job(
        conn,
        source,
        extractor,
        slice_size=4,
        parser_version="parser-1",
        slice_strategy_version="slices-1",
    )

    source.write_bytes(b"ijklmnop")
    changed_content = start_or_resume_job(
        conn,
        source,
        extractor,
        slice_size=4,
        parser_version="parser-1",
        slice_strategy_version="slices-1",
    )
    changed_parser = start_or_resume_job(
        conn,
        source,
        extractor,
        slice_size=4,
        parser_version="parser-2",
        slice_strategy_version="slices-1",
    )
    changed_slicing = start_or_resume_job(
        conn,
        source,
        extractor,
        slice_size=4,
        parser_version="parser-2",
        slice_strategy_version="slices-2",
    )

    assert resumed.job_id == first.job_id
    assert resumed.skipped_slices == (0, 1)
    assert changed_content.job_id != first.job_id
    assert changed_parser.job_id != changed_content.job_id
    assert changed_slicing.job_id != changed_parser.job_id
    assert conn.execute("SELECT COUNT(*) FROM ingest_jobs").fetchone()[0] == 4


def test_failed_slice_is_stored_and_reported(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    source = tmp_path / "rules.txt"
    source.write_bytes(b"abcdefghijkl")

    def extract(index: int, _content: bytes) -> None:
        if index == 1:
            raise ValueError("provider rejected slice")

    report = start_or_resume_job(conn, source, extract, slice_size=4)
    failed = conn.execute(
        "SELECT status, error FROM ingest_slices "
        "WHERE job_id = ? AND slice_index = 1",
        (report.job_id,),
    ).fetchone()
    job = conn.execute(
        "SELECT status, completed_slices, failed_slices FROM ingest_jobs "
        "WHERE job_id = ?",
        (report.job_id,),
    ).fetchone()

    assert [(failure.slice_index, failure.error) for failure in report.failures] == [
        (1, "provider rejected slice")
    ]
    assert failed["status"] == "failed"
    assert failed["error"] == "provider rejected slice"
    assert dict(job) == {
        "status": "failed",
        "completed_slices": 2,
        "failed_slices": 1,
    }


def test_interruption_preserves_completed_slices_and_resume_skips_them(
    conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    source = tmp_path / "rules.txt"
    source.write_bytes(b"abcdefghijkl")
    first_calls: list[int] = []

    def interrupt(index: int, _content: bytes) -> None:
        first_calls.append(index)
        if index == 1:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        start_or_resume_job(conn, source, interrupt, slice_size=4)

    completed = conn.execute(
        "SELECT slice_index FROM ingest_slices WHERE status = 'completed'"
    ).fetchall()
    job_status = conn.execute("SELECT status FROM ingest_jobs").fetchone()["status"]
    assert [row["slice_index"] for row in completed] == [0]
    assert job_status == "running"

    resumed_calls: list[int] = []
    report = start_or_resume_job(
        conn,
        source,
        lambda index, _content: resumed_calls.append(index),
        slice_size=4,
    )

    assert first_calls == [0, 1]
    assert resumed_calls == [1, 2]
    assert report.skipped_slices == (0,)
    assert report.completed_slices == (1, 2)


def test_estimate_reports_slices_and_cost_without_extracting(tmp_path: Path) -> None:
    source = tmp_path / "rules.txt"
    source.write_bytes(b"abcdefghij")
    extraction_calls: list[int] = []

    estimate = estimate_job(source, slice_size=4, cost_per_slice=0.25)

    assert estimate.slice_count == 3
    assert estimate.estimated_cost == pytest.approx(0.75)
    assert extraction_calls == []
