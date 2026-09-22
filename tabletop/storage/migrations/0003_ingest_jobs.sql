CREATE TABLE ingest_jobs (
  job_id                 TEXT PRIMARY KEY,
  document_hash          TEXT NOT NULL,
  parser_version         TEXT NOT NULL,
  slice_strategy_version TEXT NOT NULL,
  status                 TEXT NOT NULL
                           CHECK (status IN ('pending','running','completed','failed')),
  total_slices           INTEGER NOT NULL DEFAULT 0,
  completed_slices       INTEGER NOT NULL DEFAULT 0,
  failed_slices          INTEGER NOT NULL DEFAULT 0,
  estimated_cost         REAL,
  actual_cost            REAL,
  started_at             TEXT NOT NULL,
  updated_at             TEXT NOT NULL,
  UNIQUE (document_hash, parser_version, slice_strategy_version)
);

CREATE TABLE ingest_slices (
  job_id       TEXT NOT NULL REFERENCES ingest_jobs(job_id) ON DELETE CASCADE,
  slice_index  INTEGER NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('pending','completed','failed')),
  error        TEXT,
  updated_at   TEXT NOT NULL,
  PRIMARY KEY (job_id, slice_index)
);
