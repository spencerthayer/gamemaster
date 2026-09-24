"""Index generated D&D corpus documents into the scoped system retriever."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from tabletop.retrieval.lexical import LexicalRetriever
from tabletop.retrieval.models import RetrievalNamespace


def iter_documents(path: Path) -> Iterable[dict[str, object]]:
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                yield json.loads(line)


def index_corpus(
    retriever: LexicalRetriever,
    documents_path: Path,
    *,
    content_pack_id: str,
    system_id: str = "dnd5e",
    visibility: str = "GM",
) -> int:
    """Index one generated JSONL corpus; repeat runs replace stable chunk IDs."""
    count = 0
    for document in iter_documents(documents_path):
        document_id = str(document["retrieval_document_id"])
        retriever.index(
            RetrievalNamespace.SYSTEM,
            chunk_id=document_id,
            document_id=document_id,
            document_title=str(document.get("title", document_id)),
            text="\n".join(
                part
                for part in (
                    str(document.get("title", "")),
                    str(document.get("body", "")),
                )
                if part
            ),
            section=(str(document.get("document_kind", "record")),),
            source_path=",".join(document.get("source_labels", [])),
            content_pack_id=content_pack_id,
            system_id=system_id,
            visibility=visibility,
        )
        count += 1
    return count
