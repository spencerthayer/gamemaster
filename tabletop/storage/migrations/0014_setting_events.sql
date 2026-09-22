CREATE TABLE setting_events (
  setting_id TEXT NOT NULL REFERENCES settings(setting_id) ON DELETE RESTRICT,
  sequence INTEGER NOT NULL,
  event_schema_version INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  occurred_at TEXT NOT NULL,
  PRIMARY KEY (setting_id, sequence)
);

CREATE TRIGGER setting_events_are_immutable_update
BEFORE UPDATE ON setting_events
BEGIN
  SELECT RAISE(ABORT, 'setting events are append-only');
END;

CREATE TRIGGER setting_events_are_immutable_delete
BEFORE DELETE ON setting_events
BEGIN
  SELECT RAISE(ABORT, 'setting events are append-only');
END;
