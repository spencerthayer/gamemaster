-- Soft-archive marker for ADR 0010. Rows and events are retained.
ALTER TABLE campaigns ADD COLUMN archived_at TEXT NULL;
