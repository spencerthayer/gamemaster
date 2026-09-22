"""Read-only report of whether event history can rebuild stored state.

This module is an operator tool. It is not a workspace skill.
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class FidelityReport:
    """One campaign or setting history summary."""

    scope: str
    owner_id: str
    total_events: int
    by_type: dict[str, int]
    by_generation: dict[int, int]
    generation_zero: int
    complete_reconstruction: bool
    blocking_types: tuple[str, ...]


def report_database(conn: sqlite3.Connection) -> tuple[FidelityReport, ...]:
    """Return one report per campaign and per setting that has events."""

    reports: list[FidelityReport] = []
    campaign_ids = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT campaign_id FROM events ORDER BY campaign_id"
        )
    ]
    for campaign_id in campaign_ids:
        reports.append(
            _report(
                conn,
                scope="campaign",
                owner_id=campaign_id,
                table="events",
                owner_column="campaign_id",
            )
        )
    setting_ids = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT setting_id FROM setting_events ORDER BY setting_id"
        )
    ]
    for setting_id in setting_ids:
        reports.append(
            _report(
                conn,
                scope="setting",
                owner_id=setting_id,
                table="setting_events",
                owner_column="setting_id",
            )
        )
    return tuple(reports)


def _report(
    conn: sqlite3.Connection,
    *,
    scope: str,
    owner_id: str,
    table: str,
    owner_column: str,
) -> FidelityReport:
    total = int(
        conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {owner_column} = ?",
            (owner_id,),
        ).fetchone()[0]
    )
    by_type = {
        row[0]: int(row[1])
        for row in conn.execute(
            f"SELECT event_type, COUNT(*) FROM {table} "
            f"WHERE {owner_column} = ? GROUP BY event_type ORDER BY event_type",
            (owner_id,),
        )
    }
    by_generation = {
        int(row[0]): int(row[1])
        for row in conn.execute(
            f"SELECT event_schema_version, COUNT(*) FROM {table} "
            f"WHERE {owner_column} = ? GROUP BY event_schema_version "
            "ORDER BY event_schema_version",
            (owner_id,),
        )
    }
    generation_zero = by_generation.get(0, 0)
    blocking = tuple(
        row[0]
        for row in conn.execute(
            f"SELECT DISTINCT event_type FROM {table} "
            f"WHERE {owner_column} = ? AND event_schema_version = 0 "
            "ORDER BY event_type",
            (owner_id,),
        )
    )
    return FidelityReport(
        scope=scope,
        owner_id=owner_id,
        total_events=total,
        by_type=by_type,
        by_generation=by_generation,
        generation_zero=generation_zero,
        complete_reconstruction=generation_zero == 0,
        blocking_types=blocking,
    )


def format_report(reports: tuple[FidelityReport, ...]) -> str:
    lines: list[str] = []
    for report in reports:
        lines.append(f"{report.scope} {report.owner_id}")
        lines.append(f"total {report.total_events}")
        lines.append(f"by_type {report.by_type}")
        lines.append(f"by_generation {report.by_generation}")
        lines.append(f"generation_zero {report.generation_zero}")
        lines.append(
            "complete_reconstruction "
            + ("yes" if report.complete_reconstruction else "no")
        )
        lines.append(f"blocking_types {list(report.blocking_types)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report event replay fidelity")
    parser.add_argument("database")
    args = parser.parse_args(argv)
    conn = sqlite3.connect(args.database)
    conn.row_factory = sqlite3.Row
    try:
        print(format_report(report_database(conn)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
