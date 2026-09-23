-- Import staging batches and items (proposals only until reviewed apply).

CREATE TABLE import_batches (
  import_id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  format_id TEXT NOT NULL,
  source_label TEXT NOT NULL,
  created_at TEXT NOT NULL,
  report_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE import_items (
  item_id TEXT PRIMARY KEY,
  import_id TEXT NOT NULL REFERENCES import_batches(import_id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  proposed_key TEXT,
  payload_json TEXT NOT NULL,
  provenance_json TEXT NOT NULL DEFAULT '{}',
  review_json TEXT NOT NULL DEFAULT '{}',
  review_state TEXT NOT NULL DEFAULT 'pending_review'
    CHECK (review_state IN (
      'pending_review', 'applied', 'rejected', 'unapplyable'
    )),
  applied_target_id TEXT,
  error_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX idx_import_items_batch ON import_items(import_id, review_state);
CREATE INDEX idx_import_items_campaign ON import_items(campaign_id, review_state);
