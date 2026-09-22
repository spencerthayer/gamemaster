CREATE TABLE relationships (
  relationship_id   TEXT PRIMARY KEY,
  owner_scope       TEXT NOT NULL CHECK (owner_scope IN ('setting', 'campaign')),
  setting_id        TEXT REFERENCES settings(setting_id) ON DELETE CASCADE,
  campaign_id       TEXT REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  source_id         TEXT NOT NULL,
  relationship_type TEXT NOT NULL,
  target_id         TEXT NOT NULL,
  metadata          TEXT NOT NULL DEFAULT '{}',
  visibility        TEXT NOT NULL DEFAULT 'GM',
  valid_from        TEXT,
  valid_until       TEXT,
  CHECK (
    (owner_scope = 'setting'  AND setting_id  IS NOT NULL AND campaign_id IS NULL)
    OR
    (owner_scope = 'campaign' AND campaign_id IS NOT NULL)
  )
);

CREATE UNIQUE INDEX uq_rel_setting
  ON relationships(setting_id, source_id, relationship_type, target_id, valid_from)
  WHERE owner_scope = 'setting';
CREATE UNIQUE INDEX uq_rel_campaign
  ON relationships(campaign_id, source_id, relationship_type, target_id, valid_from)
  WHERE owner_scope = 'campaign';

CREATE INDEX idx_relationships_campaign_source
  ON relationships(campaign_id, source_id);
CREATE INDEX idx_relationships_campaign_target
  ON relationships(campaign_id, target_id);
CREATE INDEX idx_relationships_setting_source
  ON relationships(setting_id, source_id);
