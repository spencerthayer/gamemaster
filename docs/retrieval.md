# Retrieval

Retrieval is a lookup path for finding relevant material. It is never the
source of campaign truth. Semantic and lexical searches can both miss relevant
records, and semantic recall is probabilistic. Canon must come from the
authoritative campaign state and event history, not from whether a retrieval
query happened to return a record.

## Retriever contract

`Retriever` defines one operation:

```python
search(
    query: str,
    filters: RetrievalFilters,
    limit: int,
) -> tuple[RetrievedChunk, ...]
```

The caller supplies a query, a required namespace plus optional metadata
filters, and a positive result limit. Implementations return ranked chunks.
Each `RetrievedChunk` includes its text, backend-specific score, namespace,
and a `SourceReference`.

`RetrievalFilters` can restrict results by content pack, game system, and
visibility. Scores rank results within one backend invocation. They are not a
shared confidence scale across lexical and semantic backends.

The cascade has a related contract. `CascadeRetriever.search` returns a
`CascadeResult`, not only chunks. The result records the tier that answered in
`answered_by` and every bypassed tier, with its reason, in `skipped_tiers`.

## Namespaces

Every search targets exactly one isolated corpus:

- `system`: game rules and system reference material
- `setting`: reusable setting lore and world material
- `adventure`: published or prepared adventure material
- `campaign`: campaign-specific state and history
- `rulings`: table rulings and adjudication records
- `character`: player character material
- `npc`: non-player character material

Setting and campaign remain separate because they have different scope and
change boundaries. Setting material describes the reusable world baseline.
Campaign material records what is specific to one played campaign, including
changes that may diverge from that baseline. Merging them would allow a
campaign-specific development to appear as general setting lore, or baseline
lore to be mistaken for current campaign state.

The namespace boundary narrows lookup. It does not grant authority to a
retrieved chunk. In particular, a hit from the `campaign` namespace is still a
retrieval result, not the canonical campaign record.

## Observable degrade ladder

Retrieval uses an explicit cascade:

1. `semantic`: used when a vector backend and embedder are available and the
   stored embedding model and dimension match the active embedder.
2. `lexical`: SQLite FTS5 search, used when semantic retrieval is unavailable
   or is rejected because the embedding model or dimension does not match.
3. `unavailable`: returned with no chunks when neither semantic nor FTS5
   lexical retrieval can answer.

The cascade is not score fusion. It returns as soon as one tier safely
answers, including when that tier finds no chunks. A semantic answer therefore
does not also run lexical search merely because its result is empty.

The selected tier is observable through `CascadeResult.answered_by`.
`skipped_tiers` names each earlier tier that could not run and gives the
reason, such as `embedder unavailable`, an embedding identity mismatch, or
`FTS5 unavailable`. A semantic result has no skipped tiers. A lexical result
names the skipped semantic tier. An unavailable result names both skipped
tiers.

## Embedding model and dimension invariant

The vector store pins two parts of embedding identity: the model name and
vector dimension. The embedder must declare a non-empty model name and a
positive dimension. Every vector returned by it must have that declared
dimension.

Indexing rejects a model or dimension that differs from records already in the
store. Searching validates candidate records against the active embedder and
also verifies that each stored vector length matches its stored dimension.
Model mismatches raise `EmbeddingModelError`; dimension mismatches raise
`EmbeddingDimensionError`. The cascade catches those errors and degrades to
lexical retrieval when available.

The current vector backend stores embeddings in SQLite and computes cosine
similarity in Python over candidates from one namespace. It is intended for
small corpora.

## Refetchable source references

Every retrieved chunk carries a `SourceReference` with citation fields such as
document title, section, page, and source path. It also carries a refetch
recipe:

- `refetch_tool`: currently `get_document_chunk`
- `refetch_args`: currently the chunk ID

This recipe lets the system reload the full record instead of treating the
text included in a retrieval result as the only copy. The lexical backend's
`fetch` operation uses the chunk ID to search the namespace tables and rebuild
the retrieved chunk.

Task 42 compaction stubs must preserve this property. When compaction replaces
a full tool result with a stub, the stub keeps the tool name and arguments
needed to reconstruct that result. For a retrieved source, that means retaining
the refetch tool and its arguments rather than only a human-readable citation
or summary.
