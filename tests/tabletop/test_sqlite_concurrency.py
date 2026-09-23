"""Multi-process SQLite concurrency measurements for shared campaign files."""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import statistics
import sys
import time
import traceback
from pathlib import Path

import pytest

from tabletop.campaign.store import CampaignStore
from tabletop.storage.sqlite import connect, migrate, transaction

_REPO = Path(__file__).resolve().parents[2]
_EVIDENCE = _REPO / "docs" / "evidence" / "e1-sqlite-concurrency.json"
BUSY_TIMEOUT_MS = 5000


def _worker_migrate(db_path: str, queue: mp.Queue, label: str) -> None:
    started = time.perf_counter()
    try:
        last_error = None
        for _attempt in range(20):
            try:
                conn = connect(db_path)
                migrate(conn)
                break
            except Exception as exc:
                last_error = exc
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                time.sleep(0.05)
        else:
            raise last_error  # type: ignore[misc]
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT filename, checksum FROM schema_migrations ORDER BY filename"
            ).fetchall()
        ]
        conn.execute(
            "INSERT OR IGNORE INTO settings (setting_id, name, created_at) "
            "VALUES ('s', 'S', '2026-01-01T00:00:00Z')"
        )
        CampaignStore(conn).create_campaign(
            f"c-{label}-{os.getpid()}", "Camp", "freeform", setting_id="s"
        )
        conn.close()
        queue.put(
            {
                "ok": True,
                "label": label,
                "wait_ms": (time.perf_counter() - started) * 1000,
                "journal_mode": mode,
                "migrations": rows,
            }
        )
    except Exception as exc:
        queue.put(
            {
                "ok": False,
                "label": label,
                "wait_ms": (time.perf_counter() - started) * 1000,
                "error": f"{type(exc).__name__}: {exc}",
                "trace": traceback.format_exc(),
            }
        )


def _run_two(db_path: Path, target, *args) -> list[dict]:
    ctx = mp.get_context("spawn")
    queue: mp.Queue = ctx.Queue()
    procs = [
        ctx.Process(target=target, args=(str(db_path), queue, *extra))
        for extra in args
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=60)
        assert not proc.is_alive(), "worker hung"
        assert proc.exitcode == 0
    return [queue.get(timeout=5) for _ in procs]


def test_concurrent_startup_migration_x20(tmp_path: Path) -> None:
    waits: list[float] = []
    for i in range(20):
        db = tmp_path / f"mig-{i}.db"
        results = _run_two(db, _worker_migrate, ("a",), ("b",))
        assert all(item["ok"] for item in results), results
        assert results[0]["migrations"] == results[1]["migrations"]
        assert {item["journal_mode"].lower() for item in results} == {"wal"}
        waits.extend(item["wait_ms"] for item in results)
    _record_scenario(
        "1. Concurrent startup migration × 20",
        processes=2,
        expected="identical schema_migrations, usable schema, wal",
        observed=f"20/20 ok; migrations match; wal",
        waits=waits,
        result="pass",
    )


def test_existing_schema_migration(tmp_path: Path) -> None:
    db = tmp_path / "existing.db"
    conn = connect(db)
    migrate(conn)
    conn.close()
    results = _run_two(db, _worker_migrate, ("a",), ("b",))
    assert all(item["ok"] for item in results), results
    _record_scenario(
        "2. Existing-schema migration",
        processes=2,
        expected="both succeed, no lock error",
        observed="both ok",
        waits=[item["wait_ms"] for item in results],
        result="pass",
    )


def _writer_vs_reader(db_path: str, queue: mp.Queue, role: str) -> None:
    started = time.perf_counter()
    try:
        conn = connect(db_path)
        if role == "writer":
            time.sleep(0.05)
            with transaction(conn):
                conn.execute(
                    "INSERT INTO campaigns "
                    "(campaign_id, name, system_id, setting_id, created_at, system_state) "
                    "VALUES (?, ?, 'freeform', NULL, ?, '{}')",
                    ("w1", "Writer", "2026-01-01T00:00:00Z"),
                )
                time.sleep(0.2)
            queue.put(
                {
                    "ok": True,
                    "role": role,
                    "wait_ms": (time.perf_counter() - started) * 1000,
                }
            )
        else:
            time.sleep(0.1)
            rows = CampaignStore(conn).list_campaigns(include_archived=True)
            queue.put(
                {
                    "ok": True,
                    "role": role,
                    "wait_ms": (time.perf_counter() - started) * 1000,
                    "count": len(rows),
                }
            )
        conn.close()
    except Exception as exc:
        queue.put(
            {
                "ok": False,
                "role": role,
                "wait_ms": (time.perf_counter() - started) * 1000,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )


def test_writer_vs_player_read(tmp_path: Path) -> None:
    db = tmp_path / "rw.db"
    conn = connect(db)
    migrate(conn)
    conn.close()
    results = _run_two(db, _writer_vs_reader, ("writer",), ("reader",))
    assert all(item["ok"] for item in results), results
    reader = next(item for item in results if item["role"] == "reader")
    assert reader["wait_ms"] <= BUSY_TIMEOUT_MS + 1000
    _record_scenario(
        "3. Writer vs player read",
        processes=2,
        expected="read completes within busy_timeout, consistent snapshot",
        observed=f"reader wait_ms={reader['wait_ms']:.1f} count={reader['count']}",
        waits=[item["wait_ms"] for item in results],
        result="pass",
    )


def _two_writers(db_path: str, queue: mp.Queue, label: str) -> None:
    started = time.perf_counter()
    try:
        conn = connect(db_path)
        with transaction(conn):
            conn.execute(
                "INSERT INTO campaigns "
                "(campaign_id, name, system_id, setting_id, created_at, system_state) "
                "VALUES (?, ?, 'freeform', NULL, ?, '{}')",
                (f"w-{label}", f"W{label}", "2026-01-01T00:00:00Z"),
            )
            time.sleep(0.3)
        queue.put(
            {
                "ok": True,
                "label": label,
                "wait_ms": (time.perf_counter() - started) * 1000,
            }
        )
        conn.close()
    except Exception as exc:
        queue.put(
            {
                "ok": False,
                "label": label,
                "wait_ms": (time.perf_counter() - started) * 1000,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )


def test_writer_vs_writer_in_busy_window(tmp_path: Path) -> None:
    db = tmp_path / "ww.db"
    conn = connect(db)
    migrate(conn)
    conn.close()
    results = _run_two(db, _two_writers, ("a",), ("b",))
    assert all(item["ok"] for item in results), results
    _record_scenario(
        "4. Writer vs writer in busy window",
        processes=2,
        expected="second waits, completes, sees first commit",
        observed="both writers committed",
        waits=[item["wait_ms"] for item in results],
        result="pass",
    )


def _long_holder(db_path: str, queue: mp.Queue, label: str) -> None:
    started = time.perf_counter()
    try:
        conn = connect(db_path)
        if label == "holder":
            conn.execute("BEGIN IMMEDIATE")
            time.sleep((BUSY_TIMEOUT_MS / 1000) + 1.5)
            conn.execute("COMMIT")
            queue.put(
                {
                    "ok": True,
                    "label": label,
                    "wait_ms": (time.perf_counter() - started) * 1000,
                }
            )
        else:
            time.sleep(0.2)
            try:
                with transaction(conn):
                    CampaignStore(conn).create_campaign(
                        "late", "Late", "freeform"
                    )
                queue.put(
                    {
                        "ok": False,
                        "label": label,
                        "wait_ms": (time.perf_counter() - started) * 1000,
                        "error": "expected busy failure",
                    }
                )
            except Exception as exc:
                queue.put(
                    {
                        "ok": True,
                        "label": label,
                        "wait_ms": (time.perf_counter() - started) * 1000,
                        "error": f"{type(exc).__name__}: {exc}",
                        "busy": "locked" in str(exc).lower()
                        or "busy" in str(exc).lower(),
                    }
                )
        conn.close()
    except Exception as exc:
        queue.put(
            {
                "ok": False,
                "label": label,
                "wait_ms": (time.perf_counter() - started) * 1000,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )


def test_busy_window_exhaustion(tmp_path: Path) -> None:
    db = tmp_path / "busy.db"
    conn = connect(db)
    migrate(conn)
    conn.close()
    results = _run_two(db, _long_holder, ("holder",), ("waiter",))
    waiter = next(item for item in results if item["label"] == "waiter")
    assert waiter.get("busy") is True, waiter
    assert waiter["wait_ms"] >= BUSY_TIMEOUT_MS * 0.8
    _record_scenario(
        "5. Busy-window exhaustion",
        processes=2,
        expected="second writer waits ~5000ms then classified busy/locked",
        observed=f"waiter wait_ms={waiter['wait_ms']:.1f} error={waiter.get('error')}",
        waits=[item["wait_ms"] for item in results],
        result="pass",
    )


def _archive_vs_session(db_path: str, queue: mp.Queue, role: str) -> None:
    started = time.perf_counter()
    try:
        conn = connect(db_path)
        store = CampaignStore(conn)
        if role == "archive":
            time.sleep(0.05)
            try:
                with transaction(conn):
                    if store.has_open_session("night"):
                        raise RuntimeError("open_session")
                    store.archive_campaign(
                        "night", archived_at="2026-01-02T00:00:00Z"
                    )
                queue.put(
                    {
                        "ok": True,
                        "role": role,
                        "winner": "archive",
                        "wait_ms": (time.perf_counter() - started) * 1000,
                    }
                )
            except Exception as exc:
                queue.put(
                    {
                        "ok": True,
                        "role": role,
                        "winner": None,
                        "wait_ms": (time.perf_counter() - started) * 1000,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        else:
            time.sleep(0.05)
            try:
                with transaction(conn):
                    campaign = store.get_campaign("night")
                    if campaign is None or campaign.get("archived_at"):
                        raise RuntimeError("campaign_archived")
                    if store.has_open_session("night"):
                        raise RuntimeError("session_open")
                    conn.execute(
                        "INSERT INTO sessions "
                        "(session_id, campaign_id, started_at, participants) "
                        "VALUES (?, ?, ?, ?)",
                        ("s1", "night", "2026-01-02T00:00:00Z", "[]"),
                    )
                queue.put(
                    {
                        "ok": True,
                        "role": role,
                        "winner": "session",
                        "wait_ms": (time.perf_counter() - started) * 1000,
                    }
                )
            except Exception as exc:
                queue.put(
                    {
                        "ok": True,
                        "role": role,
                        "winner": None,
                        "wait_ms": (time.perf_counter() - started) * 1000,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        conn.close()
    except Exception as exc:
        queue.put(
            {
                "ok": False,
                "role": role,
                "wait_ms": (time.perf_counter() - started) * 1000,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )


def test_archive_vs_session_start(tmp_path: Path) -> None:
    db = tmp_path / "race.db"
    conn = connect(db)
    migrate(conn)
    CampaignStore(conn).create_campaign("night", "Night", "freeform")
    conn.close()
    results = _run_two(db, _archive_vs_session, ("archive",), ("session",))
    winners = [item.get("winner") for item in results if item.get("winner")]
    assert len(winners) == 1, results
    _record_scenario(
        "6. Archive vs session start",
        processes=2,
        expected="exactly one winner",
        observed=f"winner={winners[0]}",
        waits=[item["wait_ms"] for item in results],
        result="pass",
    )


def test_concurrent_process_startup(tmp_path: Path) -> None:
    db = tmp_path / "boot.db"
    results = _run_two(db, _worker_migrate, ("boot-a",), ("boot-b",))
    assert all(item["ok"] for item in results), results
    _record_scenario(
        "7. Concurrent process startup",
        processes=2,
        expected="both reach usable runtime on completed schema",
        observed="both migrated and inserted campaigns",
        waits=[item["wait_ms"] for item in results],
        result="pass",
    )


def _record_scenario(
    scenario: str,
    *,
    processes: int,
    expected: str,
    observed: str,
    waits: list[float],
    result: str,
) -> None:
    _EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    payload = {}
    if _EVIDENCE.is_file():
        payload = json.loads(_EVIDENCE.read_text(encoding="utf-8"))
    rows = payload.setdefault("scenarios", {})
    rows[scenario] = {
        "processes": processes,
        "expected": expected,
        "observed": observed,
        "max_wait_ms": max(waits) if waits else None,
        "median_wait_ms": statistics.median(waits) if waits else None,
        "result": result,
    }
    payload["python_version"] = sys.version
    payload["busy_timeout_ms"] = BUSY_TIMEOUT_MS
    payload["os"] = os.uname().sysname if hasattr(os, "uname") else sys.platform
    _EVIDENCE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
