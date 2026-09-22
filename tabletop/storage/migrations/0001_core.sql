CREATE TABLE settings (
  setting_id   TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  created_at   TEXT NOT NULL
);

CREATE TABLE campaigns (
  campaign_id  TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  system_id    TEXT NOT NULL,
  setting_id   TEXT REFERENCES settings(setting_id) ON DELETE RESTRICT,
  created_at   TEXT NOT NULL,
  system_state TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE sessions (
  session_id   TEXT PRIMARY KEY,
  campaign_id  TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  started_at   TEXT NOT NULL,
  ended_at     TEXT,
  participants TEXT NOT NULL DEFAULT '[]',
  summary      TEXT
);

CREATE TABLE scenes (
  scene_id     TEXT PRIMARY KEY,
  campaign_id  TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  session_id   TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
  name         TEXT NOT NULL,
  opened_at    TEXT NOT NULL,
  closed_at    TEXT
);

CREATE TABLE entities (
  entity_id    TEXT NOT NULL,
  owner_scope  TEXT NOT NULL CHECK (owner_scope IN ('setting', 'campaign')),
  setting_id   TEXT REFERENCES settings(setting_id) ON DELETE CASCADE,
  campaign_id  TEXT REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  overrides_id TEXT,
  entity_type  TEXT,
  name         TEXT NOT NULL,
  system_state TEXT NOT NULL DEFAULT '{}',
  metadata     TEXT NOT NULL DEFAULT '{}',
  CHECK (
    (owner_scope = 'setting'  AND setting_id  IS NOT NULL AND campaign_id IS NULL)
    OR
    (owner_scope = 'campaign' AND campaign_id IS NOT NULL)
  )
);

CREATE UNIQUE INDEX uq_entities_setting ON entities(setting_id, entity_id)
  WHERE owner_scope = 'setting';
CREATE UNIQUE INDEX uq_entities_campaign ON entities(campaign_id, entity_id)
  WHERE owner_scope = 'campaign';

CREATE INDEX idx_scenes_campaign ON scenes(campaign_id);
CREATE INDEX idx_entities_campaign_type ON entities(campaign_id, entity_type);
CREATE INDEX idx_entities_setting_type ON entities(setting_id, entity_type);
