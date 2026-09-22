-- One FTS5 virtual table per retrieval namespace.
-- Table names are fixed literals. Application code maps RetrievalNamespace
-- to these names through a validated enum and never interpolates a caller
-- string into SQL.

CREATE VIRTUAL TABLE fts_system USING fts5(
  chunk_id UNINDEXED,
  document_id UNINDEXED,
  document_title UNINDEXED,
  heading_path UNINDEXED,
  page UNINDEXED,
  source_path UNINDEXED,
  content_pack_id UNINDEXED,
  system_id UNINDEXED,
  visibility UNINDEXED,
  text
);

CREATE VIRTUAL TABLE fts_setting USING fts5(
  chunk_id UNINDEXED,
  document_id UNINDEXED,
  document_title UNINDEXED,
  heading_path UNINDEXED,
  page UNINDEXED,
  source_path UNINDEXED,
  content_pack_id UNINDEXED,
  system_id UNINDEXED,
  visibility UNINDEXED,
  text
);

CREATE VIRTUAL TABLE fts_adventure USING fts5(
  chunk_id UNINDEXED,
  document_id UNINDEXED,
  document_title UNINDEXED,
  heading_path UNINDEXED,
  page UNINDEXED,
  source_path UNINDEXED,
  content_pack_id UNINDEXED,
  system_id UNINDEXED,
  visibility UNINDEXED,
  text
);

CREATE VIRTUAL TABLE fts_campaign USING fts5(
  chunk_id UNINDEXED,
  document_id UNINDEXED,
  document_title UNINDEXED,
  heading_path UNINDEXED,
  page UNINDEXED,
  source_path UNINDEXED,
  content_pack_id UNINDEXED,
  system_id UNINDEXED,
  visibility UNINDEXED,
  text
);

CREATE VIRTUAL TABLE fts_rulings USING fts5(
  chunk_id UNINDEXED,
  document_id UNINDEXED,
  document_title UNINDEXED,
  heading_path UNINDEXED,
  page UNINDEXED,
  source_path UNINDEXED,
  content_pack_id UNINDEXED,
  system_id UNINDEXED,
  visibility UNINDEXED,
  text
);

CREATE VIRTUAL TABLE fts_character USING fts5(
  chunk_id UNINDEXED,
  document_id UNINDEXED,
  document_title UNINDEXED,
  heading_path UNINDEXED,
  page UNINDEXED,
  source_path UNINDEXED,
  content_pack_id UNINDEXED,
  system_id UNINDEXED,
  visibility UNINDEXED,
  text
);

CREATE VIRTUAL TABLE fts_npc USING fts5(
  chunk_id UNINDEXED,
  document_id UNINDEXED,
  document_title UNINDEXED,
  heading_path UNINDEXED,
  page UNINDEXED,
  source_path UNINDEXED,
  content_pack_id UNINDEXED,
  system_id UNINDEXED,
  visibility UNINDEXED,
  text
);
