CREATE UNIQUE INDEX uq_sessions_one_open_per_campaign
  ON sessions(campaign_id)
  WHERE ended_at IS NULL;
