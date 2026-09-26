-- Durable turn jobs and per-action effect claims.
--
-- The turn is the orchestration identity: one human message, its context,
-- its generations, its action effects, and its delivery all hang off it.
-- Receipts existed before this and are diagnostic; a turn job is state.

CREATE TABLE turn_jobs (
  turn_id                 TEXT PRIMARY KEY,
  campaign_id             TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  session_id              TEXT,
  scene_id                TEXT,
  status                  TEXT NOT NULL CHECK (status IN (
                           'received', 'interpreting', 'awaiting_player',
                           'awaiting_gm', 'resolving', 'narrating',
                           'completed', 'delivery_pending', 'delivered',
                           'failed', 'cancelled')),
  -- Channel ingress identity. Nullable for a turn with no remote origin.
  channel                 TEXT,
  conversation_id         TEXT,
  external_message_id     TEXT,
  -- A turn resumed after a question was answered points at its parent.
  resumes_turn_id         TEXT,
  principal_id            TEXT,
  input_text              TEXT NOT NULL,
  disposition             TEXT,
  failure_reason          TEXT,
  -- Lease ownership for recovery after a crash.
  lease_owner             TEXT,
  lease_expires_at        TEXT,
  created_at              TEXT NOT NULL,
  updated_at              TEXT NOT NULL
);

-- One native message identity produces one turn. This is what makes
-- duplicate ingress from a channel retry idempotent rather than a second
-- turn with a second set of authoritative effects.
CREATE UNIQUE INDEX uq_turn_jobs_ingress
  ON turn_jobs(campaign_id, channel, conversation_id, external_message_id)
  WHERE channel IS NOT NULL AND external_message_id IS NOT NULL;

CREATE INDEX idx_turn_jobs_campaign_status ON turn_jobs(campaign_id, status);
CREATE INDEX idx_turn_jobs_resumes ON turn_jobs(resumes_turn_id)
  WHERE resumes_turn_id IS NOT NULL;

-- Per-action effect claims. A claim is the ledger entry proving an action's
-- authoritative effect was committed exactly once.
CREATE TABLE turn_action_effects (
  turn_id       TEXT NOT NULL REFERENCES turn_jobs(turn_id) ON DELETE CASCADE,
  ordinal       INTEGER NOT NULL,
  action_type   TEXT,
  status        TEXT NOT NULL CHECK (status IN (
                  'claimed', 'committed', 'not_applicable')),
  event_sequence INTEGER,
  claimed_at    TEXT NOT NULL,
  committed_at  TEXT,
  PRIMARY KEY (turn_id, ordinal)
);

CREATE INDEX idx_turn_action_effects_status
  ON turn_action_effects(turn_id, status);
