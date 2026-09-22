CREATE TABLE documents (
  document_id     TEXT PRIMARY KEY,
  content_hash    TEXT NOT NULL UNIQUE,
  source_path     TEXT NOT NULL,
  title           TEXT NOT NULL,
  document_shape  TEXT NOT NULL
                  CHECK (document_shape IN (
                    'structured_rules',
                    'prose',
                    'reference_table',
                    'unsupported'
                  )),
  content_pack_id TEXT,
  system_id       TEXT,
  visibility      TEXT NOT NULL,
  ingested_at     TEXT NOT NULL
);

CREATE TABLE document_chunks (
  chunk_id        TEXT PRIMARY KEY,
  document_id     TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
  ordinal         INTEGER NOT NULL CHECK (ordinal >= 0),
  heading_path    TEXT NOT NULL,
  page            INTEGER,
  text            TEXT NOT NULL,
  content_hash    TEXT NOT NULL,
  content_pack_id TEXT,
  system_id       TEXT,
  visibility      TEXT NOT NULL,
  UNIQUE (document_id, ordinal)
);

CREATE INDEX idx_document_chunks_document
  ON document_chunks(document_id);
CREATE INDEX idx_document_chunks_content_hash
  ON document_chunks(content_hash);
