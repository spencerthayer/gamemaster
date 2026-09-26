-- Authoritative scene lifecycle: open/closed status, presence, and campaign clock.
--
-- ``scenes`` was created by 0001 with only ``opened_at``/``closed_at`` and
-- gained plugin-owned ``system_state`` in 0004. This migration rebuilds the
-- table with the explicit lifecycle shape, backfilling existing rows, and adds
-- presence plus the single campaign-clock row.
--
-- Mutable narrative state deliberately stays out of these tables. It lives in
-- events, facts, relationships, and other existing campaign records.

PRAGMA foreign_keys = OFF;

-- ``location_entity_id`` is a soft reference, not a foreign key: ``entities``
-- is unique per (owner scope, owner id) and never by ``entity_id`` alone, so
-- SQLite cannot express it as one. The triggers below enforce the real
-- same-campaign invariant and clear the reference when an entity is deleted.
CREATE TABLE scenes_lifecycle (
  scene_id             TEXT PRIMARY KEY,
  campaign_id          TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  session_id           TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
  name                 TEXT NOT NULL,
  status               TEXT NOT NULL CHECK (status IN ('open', 'closed')),
  location_entity_id   TEXT,
  in_world_started_at  TEXT,
  in_world_ended_at    TEXT,
  started_at           TEXT NOT NULL,
  ended_at             TEXT,
  system_state         TEXT NOT NULL DEFAULT '{}',
  CHECK (ended_at IS NULL OR ended_at >= started_at),
  CHECK (in_world_ended_at IS NULL
         OR in_world_started_at IS NULL
         OR in_world_ended_at >= in_world_started_at)
);

INSERT INTO scenes_lifecycle (
  scene_id, campaign_id, session_id, name, status,
  in_world_started_at, in_world_ended_at, started_at, ended_at, system_state
)
SELECT
  scene_id,
  campaign_id,
  session_id,
  name,
  CASE WHEN closed_at IS NULL THEN 'open' ELSE 'closed' END,
  NULL,
  NULL,
  opened_at,
  closed_at,
  system_state
FROM scenes;

DROP INDEX idx_scenes_campaign;
DROP TABLE scenes;

CREATE TABLE scenes (
  scene_id             TEXT PRIMARY KEY,
  campaign_id          TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  session_id           TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
  name                 TEXT NOT NULL,
  status               TEXT NOT NULL CHECK (status IN ('open', 'closed')),
  location_entity_id   TEXT,
  in_world_started_at  TEXT,
  in_world_ended_at    TEXT,
  started_at           TEXT NOT NULL,
  ended_at             TEXT,
  system_state         TEXT NOT NULL DEFAULT '{}',
  CHECK (ended_at IS NULL OR ended_at >= started_at),
  CHECK (in_world_ended_at IS NULL
         OR in_world_started_at IS NULL
         OR in_world_ended_at >= in_world_started_at)
);

INSERT INTO scenes (
  scene_id, campaign_id, session_id, name, status,
  in_world_started_at, in_world_ended_at, started_at, ended_at, system_state
)
SELECT
  scene_id, campaign_id, session_id, name, status,
  in_world_started_at, in_world_ended_at, started_at, ended_at, system_state
FROM scenes_lifecycle;

DROP TABLE scenes_lifecycle;

CREATE INDEX idx_scenes_campaign ON scenes(campaign_id);
CREATE INDEX idx_scenes_session ON scenes(session_id);

-- At most one open scene per campaign. One campaign already has at most one
-- open session (0013), so this is the same "one live game" invariant. Scenes
-- backfilled as open by the lifecycle rebuild keep their prior meaning; a
-- pre-existing campaign with two open scenes fails the migration loudly
-- rather than having one silently closed.
CREATE UNIQUE INDEX uq_scenes_one_open_per_campaign
  ON scenes(campaign_id)
  WHERE status = 'open';

-- A scene belongs to the campaign of its session and of its location entity.
CREATE TRIGGER scenes_same_campaign_session
BEFORE INSERT ON scenes
FOR EACH ROW
WHEN NEW.session_id IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'scenes session mismatch')
  WHERE NOT EXISTS (
    SELECT 1 FROM sessions
    WHERE session_id = NEW.session_id
      AND campaign_id = NEW.campaign_id
  );
END;

CREATE TRIGGER scenes_same_campaign_location
BEFORE INSERT ON scenes
FOR EACH ROW
WHEN NEW.location_entity_id IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'scenes location mismatch')
  WHERE NOT EXISTS (
    SELECT 1 FROM entities
    WHERE entity_id = NEW.location_entity_id
      AND owner_scope = 'campaign'
      AND campaign_id = NEW.campaign_id
  );
END;

CREATE TRIGGER scenes_clear_deleted_location
AFTER DELETE ON entities
FOR EACH ROW
BEGIN
  UPDATE scenes SET location_entity_id = NULL
  WHERE location_entity_id = OLD.entity_id
    AND campaign_id = OLD.campaign_id;
END;

-- Scene presence. Visibility is deliberately absent: who may learn about a
-- present entity is viewpoint policy, not a fact about presence.
CREATE TABLE scene_members (
  scene_id      TEXT NOT NULL REFERENCES scenes(scene_id) ON DELETE CASCADE,
  entity_id     TEXT NOT NULL,
  presence_type TEXT NOT NULL CHECK (presence_type IN ('pc', 'npc', 'summon', 'prop')),
  entered_at    TEXT NOT NULL,
  exited_at     TEXT,
  PRIMARY KEY (scene_id, entity_id),
  CHECK (exited_at IS NULL OR exited_at >= entered_at)
);

CREATE INDEX idx_scene_members_scene ON scene_members(scene_id);
CREATE INDEX idx_scene_members_entity ON scene_members(entity_id);

-- Presence belongs to a scene in the same campaign as the entity.
CREATE TRIGGER scene_members_same_campaign
BEFORE INSERT ON scene_members
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'scene_members entity mismatch')
  WHERE NOT EXISTS (
    SELECT 1 FROM entities
    WHERE entity_id = NEW.entity_id
      AND owner_scope = 'campaign'
      AND campaign_id = (SELECT campaign_id FROM scenes WHERE scene_id = NEW.scene_id)
  );
END;

-- The campaign-wide in-world clock. One row per campaign.
CREATE TABLE campaign_clock (
  campaign_id      TEXT PRIMARY KEY REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  in_world_label   TEXT,
  in_world_minutes INTEGER,
  updated_at       TEXT NOT NULL,
  CHECK (in_world_minutes IS NULL OR in_world_minutes >= 0)
);

PRAGMA foreign_keys = ON;
