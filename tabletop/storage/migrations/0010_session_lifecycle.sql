ALTER TABLE sessions ADD COLUMN transcript_reference TEXT;
ALTER TABLE sessions ADD COLUMN event_start_sequence INTEGER;
ALTER TABLE sessions ADD COLUMN event_end_sequence INTEGER;
ALTER TABLE sessions ADD COLUMN important_facts TEXT NOT NULL DEFAULT '[]';
ALTER TABLE sessions ADD COLUMN open_threads TEXT NOT NULL DEFAULT '[]';

CREATE INDEX idx_sessions_open_campaign
  ON sessions(campaign_id, started_at)
  WHERE ended_at IS NULL;
