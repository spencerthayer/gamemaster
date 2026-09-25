import json

from tabletop.retrieval.lexical import LexicalRetriever
from tabletop.retrieval.models import RetrievalFilters, RetrievalNamespace
from tabletop.storage.sqlite import connect, migrate
from systems.dnd5e.content import index_corpus


def test_generated_dnd_corpus_is_indexed_with_scoped_metadata(tmp_path):
    connection = connect(tmp_path / "retrieval.db")
    migrate(connection)
    retriever = LexicalRetriever(connection)
    documents = tmp_path / "retrieval.jsonl"
    documents.write_text(json.dumps({
        "retrieval_document_id": "ret_fireball",
        "document_kind": "spell",
        "title": "Fireball",
        "body": "A bright bead of fire.",
        "source_labels": ["srd-2024/Spell.json"],
    }) + "\n")

    assert index_corpus(retriever, documents, content_pack_id="dnd5e-merged") == 1
    results = retriever.search(
        "fireball",
        RetrievalFilters(
            namespace=RetrievalNamespace.SYSTEM,
            content_pack_id="dnd5e-merged",
            system_id="dnd5e",
        ),
        10,
    )
    assert [result.source.chunk_id for result in results] == ["ret_fireball"]
    assert results[0].source.source_path == "srd-2024/Spell.json"
    connection.close()


def test_other_system_is_not_returned_by_scoped_search(tmp_path):
    connection = connect(tmp_path / "retrieval.db")
    migrate(connection)
    retriever = LexicalRetriever(connection)
    documents = tmp_path / "retrieval.jsonl"
    documents.write_text(json.dumps({
        "retrieval_document_id": "ret_fireball",
        "document_kind": "spell",
        "title": "Fireball",
        "body": "A bright bead of fire.",
        "source_labels": [],
    }) + "\n")
    index_corpus(retriever, documents, content_pack_id="other", system_id="other")
    assert retriever.search("fireball", RetrievalFilters(namespace=RetrievalNamespace.SYSTEM, system_id="dnd5e"), 10) == ()
    connection.close()
