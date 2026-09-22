CREATE TABLE facts (
  fact_id            TEXT PRIMARY KEY,
  fact_scope         TEXT NOT NULL CHECK (fact_scope IN ('setting', 'campaign')),
  setting_id         TEXT REFERENCES settings(setting_id) ON DELETE CASCADE,
  campaign_id        TEXT REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  subject_id         TEXT,
  predicate          TEXT NOT NULL,
  value              TEXT NOT NULL,
  canon_state        TEXT NOT NULL DEFAULT 'proposed'
                       CHECK (canon_state IN ('proposed', 'confirmed')),
  knowledge_state    TEXT NOT NULL DEFAULT 'unrevealed'
                       CHECK (knowledge_state IN ('unrevealed', 'known')),
  visibility         TEXT NOT NULL DEFAULT 'GM',
  valid_from         TEXT,
  valid_until        TEXT,
  source_document_id TEXT,
  source_chunk_id    TEXT,
  import_job_id      TEXT,
  extraction_method  TEXT,
  source_ownership   TEXT NOT NULL DEFAULT 'attached'
                       CHECK (source_ownership IN ('attached', 'detached')),
  created_at         TEXT NOT NULL,
  CHECK (
    (fact_scope = 'setting'  AND setting_id  IS NOT NULL AND campaign_id IS NULL)
    OR
    (fact_scope = 'campaign' AND campaign_id IS NOT NULL)
  )
);

CREATE INDEX idx_facts_campaign_subject ON facts(campaign_id, subject_id);
CREATE INDEX idx_facts_setting_subject ON facts(setting_id, subject_id);
CREATE INDEX idx_facts_provenance ON facts(source_document_id, import_job_id);
CREATE INDEX idx_facts_canon_knowledge ON facts(canon_state, knowledge_state);
