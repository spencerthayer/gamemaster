# Retrieval benchmark

Measured by `tests/tabletop/test_retrieval_benchmark.py` on a 200-chunk
in-memory corpus. Lexical search uses SQLite FTS5. Vector search uses the
existing Python cosine scan. Both searches must finish in under 2 seconds.

This run does not replace either backend. A later plan can change the vector
store only if a larger corpus shows this design is the bottleneck.
