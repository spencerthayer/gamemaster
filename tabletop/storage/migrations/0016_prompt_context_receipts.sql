CREATE TABLE prompt_context_receipts (
  receipt_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  campaign_id TEXT,
  workspace TEXT NOT NULL,
  context_budget INTEGER NOT NULL,
  considered_count INTEGER NOT NULL,
  selected_count INTEGER NOT NULL,
  compacted_count INTEGER NOT NULL,
  dropped_count INTEGER NOT NULL,
  estimated_tokens INTEGER NOT NULL,
  source_kinds TEXT NOT NULL,
  context_sha256 TEXT NOT NULL
);
