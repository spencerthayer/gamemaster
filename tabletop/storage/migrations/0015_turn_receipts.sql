CREATE TABLE turn_receipts (
  turn_id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  system_id TEXT NOT NULL,
  status TEXT NOT NULL,
  event_sequence INTEGER,
  retrieval_tier TEXT,
  latency_ms INTEGER NOT NULL,
  provider TEXT,
  model TEXT,
  tokens_in INTEGER,
  tokens_out INTEGER
);
