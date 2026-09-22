CREATE TABLE events (
  campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE RESTRICT,
  sequence    INTEGER NOT NULL,
  event_type  TEXT NOT NULL,
  session_id  TEXT,
  scene_id    TEXT,
  actor_id    TEXT,
  target_id   TEXT,
  payload     TEXT NOT NULL DEFAULT '{}',
  occurred_at TEXT NOT NULL,
  PRIMARY KEY (campaign_id, sequence)
);

CREATE TRIGGER events_are_immutable_update
BEFORE UPDATE ON events
BEGIN
  SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TRIGGER events_are_immutable_delete
BEFORE DELETE ON events
BEGIN
  SELECT RAISE(ABORT, 'events are append-only');
END;
