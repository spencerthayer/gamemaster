"""Durable, content-addressed ingestion job execution."""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tabletop.storage.sqlite import transaction

PARSER_VERSION = "1"
# Changing slicing behavior requires bumping SLICE_STRATEGY_VERSION.
SLICE_STRATEGY_VERSION = "1"

SliceStrategy = Callable[[bytes], Sequence[bytes]]
SliceExtractor = Callable[[int, bytes], object]


@dataclass(frozen=True)
class JobEstimate:
    """Cost estimate produced without running extraction."""

    document_hash: str
    slice_count: int
    estimated_cost: float


@dataclass(frozen=True)
class SliceFailure:
    """A failed extraction slice and its persisted error."""

    slice_index: int
    error: str


@dataclass(frozen=True)
class IngestJobReport:
    """Result of one start or resume attempt."""

    job_id: str
    document_hash: str
    status: str
    completed_slices: tuple[int, ...]
    skipped_slices: tuple[int, ...]
    failures: tuple[SliceFailure, ...]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _fixed_size_slices(content: bytes, slice_size: int) -> tuple[bytes, ...]:
    if slice_size <= 0:
        raise ValueError("slice_size must be positive")
    return tuple(
        content[offset : offset + slice_size]
        for offset in range(0, len(content), slice_size)
    )


def _prepare_slices(
    content: bytes,
    *,
    slice_size: int,
    slice_strategy: SliceStrategy | None,
) -> tuple[bytes, ...]:
    if slice_strategy is None:
        return _fixed_size_slices(content, slice_size)
    slices = tuple(slice_strategy(content))
    if any(not isinstance(item, bytes) for item in slices):
        raise TypeError("slice_strategy must return a sequence of bytes")
    return slices


def estimate_job(
    path: Path | str,
    *,
    slice_size: int = 64 * 1024,
    cost_per_slice: float = 0.0,
    slice_strategy: SliceStrategy | None = None,
) -> JobEstimate:
    """Estimate slice count and cost without invoking an extractor."""

    if cost_per_slice < 0:
        raise ValueError("cost_per_slice cannot be negative")
    content = Path(path).read_bytes()
    slices = _prepare_slices(
        content,
        slice_size=slice_size,
        slice_strategy=slice_strategy,
    )
    return JobEstimate(
        document_hash=_content_hash(content),
        slice_count=len(slices),
        estimated_cost=len(slices) * cost_per_slice,
    )


def _start_job(
    conn: sqlite3.Connection,
    *,
    document_hash: str,
    parser_version: str,
    slice_strategy_version: str,
    total_slices: int,
    estimated_cost: float,
) -> str:
    now = _utc_now()
    with transaction(conn):
        existing = conn.execute(
            "SELECT job_id, total_slices FROM ingest_jobs "
            "WHERE document_hash = ? AND parser_version = ? "
            "AND slice_strategy_version = ?",
            (document_hash, parser_version, slice_strategy_version),
        ).fetchone()
        if existing is not None:
            if existing["total_slices"] != total_slices:
                raise ValueError(
                    "slice count changed without a slice strategy version bump"
                )
            job_id = str(existing["job_id"])
            conn.execute(
                "UPDATE ingest_jobs SET status = 'running', "
                "estimated_cost = ?, updated_at = ? WHERE job_id = ?",
                (estimated_cost, now, job_id),
            )
            return job_id

        job_id = f"ingest-{uuid.uuid4()}"
        conn.execute(
            "INSERT INTO ingest_jobs "
            "(job_id, document_hash, parser_version, slice_strategy_version, "
            "status, total_slices, estimated_cost, started_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?)",
            (
                job_id,
                document_hash,
                parser_version,
                slice_strategy_version,
                total_slices,
                estimated_cost,
                now,
                now,
            ),
        )
        conn.executemany(
            "INSERT INTO ingest_slices "
            "(job_id, slice_index, status, updated_at) "
            "VALUES (?, ?, 'pending', ?)",
            ((job_id, index, now) for index in range(total_slices)),
        )
    return job_id


def _record_slice_result(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    slice_index: int,
    status: str,
    error: str | None,
) -> None:
    now = _utc_now()
    with transaction(conn):
        conn.execute(
            "UPDATE ingest_slices SET status = ?, error = ?, updated_at = ? "
            "WHERE job_id = ? AND slice_index = ?",
            (status, error, now, job_id, slice_index),
        )
        counts = conn.execute(
            "SELECT "
            "SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed, "
            "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed "
            "FROM ingest_slices WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        conn.execute(
            "UPDATE ingest_jobs SET completed_slices = ?, failed_slices = ?, "
            "updated_at = ? WHERE job_id = ?",
            (counts["completed"] or 0, counts["failed"] or 0, now, job_id),
        )


def _finish_job(conn: sqlite3.Connection, job_id: str) -> str:
    row = conn.execute(
        "SELECT failed_slices FROM ingest_jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    status = "failed" if row["failed_slices"] else "completed"
    with transaction(conn):
        conn.execute(
            "UPDATE ingest_jobs SET status = ?, updated_at = ? WHERE job_id = ?",
            (status, _utc_now(), job_id),
        )
    return status


def start_or_resume_job(
    conn: sqlite3.Connection,
    path: Path | str,
    extract_slice: SliceExtractor,
    *,
    slice_size: int = 64 * 1024,
    cost_per_slice: float = 0.0,
    parser_version: str | None = None,
    slice_strategy_version: str | None = None,
    slice_strategy: SliceStrategy | None = None,
) -> IngestJobReport:
    """Run pending slices for the matching content and version identity."""

    if cost_per_slice < 0:
        raise ValueError("cost_per_slice cannot be negative")
    parser_version = PARSER_VERSION if parser_version is None else parser_version
    slice_strategy_version = (
        SLICE_STRATEGY_VERSION
        if slice_strategy_version is None
        else slice_strategy_version
    )
    if not parser_version:
        raise ValueError("parser_version cannot be empty")
    if not slice_strategy_version:
        raise ValueError("slice_strategy_version cannot be empty")
    content = Path(path).read_bytes()
    slices = _prepare_slices(
        content,
        slice_size=slice_size,
        slice_strategy=slice_strategy,
    )
    estimate = JobEstimate(
        document_hash=_content_hash(content),
        slice_count=len(slices),
        estimated_cost=len(slices) * cost_per_slice,
    )
    job_id = _start_job(
        conn,
        document_hash=estimate.document_hash,
        parser_version=parser_version,
        slice_strategy_version=slice_strategy_version,
        total_slices=estimate.slice_count,
        estimated_cost=estimate.estimated_cost,
    )
    completed_before = {
        int(row["slice_index"])
        for row in conn.execute(
            "SELECT slice_index FROM ingest_slices "
            "WHERE job_id = ? AND status = 'completed'",
            (job_id,),
        )
    }
    completed: list[int] = []
    failures: list[SliceFailure] = []

    for index, slice_content in enumerate(slices):
        if index in completed_before:
            continue
        try:
            extract_slice(index, slice_content)
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            _record_slice_result(
                conn,
                job_id=job_id,
                slice_index=index,
                status="failed",
                error=error,
            )
            failures.append(SliceFailure(slice_index=index, error=error))
        else:
            _record_slice_result(
                conn,
                job_id=job_id,
                slice_index=index,
                status="completed",
                error=None,
            )
            completed.append(index)

    status = _finish_job(conn, job_id)
    return IngestJobReport(
        job_id=job_id,
        document_hash=estimate.document_hash,
        status=status,
        completed_slices=tuple(completed),
        skipped_slices=tuple(sorted(completed_before)),
        failures=tuple(failures),
    )
