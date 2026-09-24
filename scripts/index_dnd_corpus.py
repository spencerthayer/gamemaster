#!/usr/bin/env python3
"""Index the generated merged D&D corpus into a gamemaster database."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.dnd5e.content import index_corpus
from tabletop.retrieval.lexical import LexicalRetriever
from tabletop.storage.sqlite import connect, migrate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--documents",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".." / "dnd5e" / "data" / "generated" / "retrieval_documents.jsonl",
    )
    parser.add_argument("--content-pack", default="dnd5e-merged")
    parser.add_argument("--visibility", default="GM")
    args = parser.parse_args()
    connection = connect(args.database)
    try:
        migrate(connection)
        count = index_corpus(
            LexicalRetriever(connection),
            args.documents,
            content_pack_id=args.content_pack,
            system_id="dnd5e",
            visibility=args.visibility,
        )
    finally:
        connection.close()
    print(f"indexed {count} documents into {args.database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
