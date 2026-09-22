ALTER TABLE sessions
  ADD COLUMN checklist_step INTEGER NOT NULL DEFAULT 0
  CHECK (checklist_step BETWEEN 0 AND 4);

UPDATE sessions
SET checklist_step = 4
WHERE ended_at IS NOT NULL;
