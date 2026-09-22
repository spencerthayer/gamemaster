CREATE TABLE rulings (
  ruling_id           TEXT PRIMARY KEY,
  campaign_id         TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  system_id           TEXT NOT NULL,
  question            TEXT NOT NULL,
  decision            TEXT NOT NULL,
  scope               TEXT NOT NULL,
  source_references   TEXT NOT NULL DEFAULT '[]',
  session_id          TEXT REFERENCES sessions(session_id) ON DELETE RESTRICT,
  created_at          TEXT NOT NULL,
  supersedes          TEXT REFERENCES rulings(ruling_id) ON DELETE RESTRICT,
  canon_state         TEXT NOT NULL DEFAULT 'proposed'
                      CHECK (canon_state IN ('proposed', 'confirmed')),
  knowledge_state     TEXT NOT NULL DEFAULT 'unrevealed'
                      CHECK (knowledge_state IN ('unrevealed', 'known')),
  originating_action  TEXT,
  originating_context TEXT,
  CHECK (length(trim(question)) > 0),
  CHECK (length(trim(decision)) > 0),
  CHECK (length(trim(scope)) > 0),
  CHECK (supersedes IS NULL OR supersedes <> ruling_id),
  CHECK (canon_state <> 'proposed' OR knowledge_state <> 'known')
);

CREATE INDEX idx_rulings_campaign_system
  ON rulings(campaign_id, system_id, created_at);
CREATE INDEX idx_rulings_canon
  ON rulings(campaign_id, canon_state);
CREATE INDEX idx_rulings_supersedes
  ON rulings(supersedes);
