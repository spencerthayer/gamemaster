-- Campaign pause, so a GM can stop new player processing without losing turns.
--
-- Pausing does not discard queued turns. They stay in `turn_jobs` and remain
-- inspectable; resume continues them in order. A paused campaign may still be
-- read and inspected by the GM.

CREATE TABLE campaign_pause (
  campaign_id  TEXT PRIMARY KEY REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  paused       INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1)),
  reason       TEXT,
  paused_at    TEXT,
  resumed_at   TEXT,
  updated_at   TEXT NOT NULL
);

-- One row per campaign, always present after this migration for new campaigns.
-- A campaign with no row is not paused.
CREATE INDEX idx_turn_jobs_paused_queue ON turn_jobs(campaign_id, created_at)
  WHERE status NOT IN ('delivered', 'failed', 'cancelled');
