"""Argparse front door for the operator CLI."""

from __future__ import annotations

import argparse
import os
from typing import Sequence

DATABASE_PATH_ENV_VAR = "TABLETOP_DATABASE_PATH"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gamemaster",
        description="Operator CLI for Gamemaster campaign lifecycle.",
    )
    subparsers = parser.add_subparsers(dest="command")

    campaign = subparsers.add_parser(
        "campaign",
        help="Create, list, inspect, select, and manage campaigns.",
    )
    campaign.add_subparsers(dest="campaign_command")

    system = subparsers.add_parser(
        "system",
        help="List and inspect installed game-system plugins.",
    )
    system.add_subparsers(dest="system_command")

    return parser


def require_database_path(environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    value = env.get(DATABASE_PATH_ENV_VAR)
    if not value:
        raise SystemExit(
            f"{DATABASE_PATH_ENV_VAR} is required for this command"
        )
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command is None:
        parser.print_help()
        return 0
    # Subcommands are filled in by later tasks. Unknown top-level names are
    # rejected by argparse before this point.
    parser.error(f"command {args.command!r} is not implemented yet")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
