-- Generation receipts and a durable delivery outbox, both linked to a turn.
--
-- `turn_receipts` (0015) predates the turn and is diagnostic. These tables
-- are turn-linked operational evidence: one row per model generation, and one
-- row per output segment that has to reach a channel.
--
-- Neither table is campaign canon. They record what happened operationally so
-- a turn can be explained and a delivery retried without regenerating.

CREATE TABLE generation_receipts (
  receipt_id     TEXT PRIMARY KEY,
  turn_id        TEXT NOT NULL REFERENCES turn_jobs(turn_id) ON DELETE CASCADE,
  ordinal        INTEGER NOT NULL,
  created_at     TEXT NOT NULL,
  provider       TEXT,
  model          TEXT,
  tokens_in      INTEGER,
  tokens_out     INTEGER,
  latency_ms     INTEGER,
  prompt_sha256  TEXT,
  response_sha256 TEXT,
  output_segments TEXT NOT NULL,
  UNIQUE (turn_id, ordinal)
);

-- One delivery per turn, route, and segment. `delivery_id` is stable across
-- retries, so a transport that supports idempotency can deduplicate on it.
CREATE TABLE turn_deliveries (
  delivery_id   TEXT NOT NULL,
  turn_id       TEXT NOT NULL REFERENCES turn_jobs(turn_id) ON DELETE CASCADE,
  channel       TEXT NOT NULL,
  conversation_id TEXT,
  segment       INTEGER NOT NULL,
  text          TEXT NOT NULL,
  status        TEXT NOT NULL CHECK (status IN (
                  'pending', 'sending', 'delivered', 'ambiguous', 'failed')),
  attempts      INTEGER NOT NULL DEFAULT 0,
  -- Transport-level idempotency key, when the transport supplies one.
  client_seq    INTEGER,
  remote_ack    TEXT,
  last_error    TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (turn_id, delivery_id)
);

CREATE INDEX idx_turn_deliveries_status ON turn_deliveries(status);
CREATE INDEX idx_turn_deliveries_turn ON turn_deliveries(turn_id, segment);

-- Legacy note: rows already in `turn_receipts` (0015) carry no turn identity,
-- so they cannot be linked to a turn without inventing one. They are left in
-- place as pre-turn diagnostic history. A turn's operational evidence lives
-- only in `generation_receipts` and `turn_deliveries` above.
