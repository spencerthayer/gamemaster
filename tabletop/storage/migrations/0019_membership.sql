-- Campaign membership: participants, channel principals, character controls.

CREATE TABLE participants (
  participant_id TEXT NOT NULL,
  campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('gm', 'player')),
  created_at TEXT NOT NULL,
  PRIMARY KEY (campaign_id, participant_id)
);

CREATE UNIQUE INDEX ux_participants_one_gm
  ON participants(campaign_id) WHERE role = 'gm';

CREATE TABLE participant_principals (
  principal_id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  participant_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  external_id TEXT NOT NULL,
  UNIQUE (campaign_id, channel, external_id),
  UNIQUE (campaign_id, participant_id, channel),
  FOREIGN KEY (campaign_id, participant_id)
    REFERENCES participants(campaign_id, participant_id) ON DELETE CASCADE
);

CREATE TABLE character_controls (
  control_id TEXT NOT NULL,
  campaign_id TEXT NOT NULL,
  participant_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  control TEXT NOT NULL CHECK (control IN ('owner', 'shared', 'gm', 'temporary')),
  created_at TEXT NOT NULL,
  ended_at TEXT,
  PRIMARY KEY (campaign_id, control_id),
  FOREIGN KEY (campaign_id, participant_id)
    REFERENCES participants(campaign_id, participant_id) ON DELETE CASCADE
);

CREATE TRIGGER participant_principals_same_campaign
BEFORE INSERT ON participant_principals
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'participant_principals campaign mismatch')
  WHERE NOT EXISTS (
    SELECT 1 FROM participants
    WHERE campaign_id = NEW.campaign_id
      AND participant_id = NEW.participant_id
  );
END;

CREATE TRIGGER character_controls_same_campaign
BEFORE INSERT ON character_controls
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'character_controls campaign mismatch')
  WHERE NOT EXISTS (
    SELECT 1 FROM participants
    WHERE campaign_id = NEW.campaign_id
      AND participant_id = NEW.participant_id
  )
  OR NOT EXISTS (
    SELECT 1 FROM entities
    WHERE owner_scope = 'campaign'
      AND campaign_id = NEW.campaign_id
      AND entity_id = NEW.entity_id
  );
END;
