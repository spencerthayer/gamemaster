-- Content catalog: installed packs, and campaign attachment with roles.
--
-- A pack is installed globally; a campaign attaches it. Installation is not
-- activation, and a document's bytes are shared by every campaign that uses
-- them: one content hash can back many attachments, so nothing is duplicated.
--
-- Authority is a typed role on the attachment, never a free-form label.
-- A higher retrieval score must not be able to grant authority.

CREATE TABLE installed_packs (
  pack_id        TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  pack_type      TEXT NOT NULL CHECK (pack_type IN ('rules', 'setting', 'adventure')),
  system_id      TEXT,
  version        TEXT,
  -- Digest of the pack's manifest, so a changed pack is detectable without
  -- re-reading its documents.
  manifest_hash  TEXT NOT NULL,
  source_path    TEXT NOT NULL,
  installed_at   TEXT NOT NULL
);

CREATE UNIQUE INDEX uq_installed_packs_manifest
  ON installed_packs(manifest_hash);

CREATE TABLE installed_pack_documents (
  pack_id      TEXT NOT NULL REFERENCES installed_packs(pack_id) ON DELETE CASCADE,
  document_id  TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
  ordinal      INTEGER NOT NULL,
  role         TEXT NOT NULL CHECK (role IN (
                 'rules', 'setting', 'adventure', 'character', 'notes', 'reference')),
  PRIMARY KEY (pack_id, document_id)
);

-- Campaign attachment. Role and visibility live here, not on the document:
-- the same document can be rules for one campaign and notes for another.
CREATE TABLE campaign_content_packs (
  campaign_id  TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  pack_id      TEXT NOT NULL REFERENCES installed_packs(pack_id) ON DELETE CASCADE,
  role         TEXT NOT NULL CHECK (role IN (
                 'rules', 'setting', 'adventure', 'character', 'notes', 'reference')),
  enabled      INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  attached_at  TEXT NOT NULL,
  PRIMARY KEY (campaign_id, pack_id)
);

CREATE TABLE campaign_documents (
  campaign_id  TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  document_id  TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
  role         TEXT NOT NULL CHECK (role IN (
                 'rules', 'setting', 'adventure', 'character', 'notes', 'reference')),
  enabled      INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  gm_only      INTEGER NOT NULL DEFAULT 0 CHECK (gm_only IN (0, 1)),
  attached_at  TEXT NOT NULL,
  PRIMARY KEY (campaign_id, document_id)
);

CREATE INDEX idx_campaign_documents_role ON campaign_documents(campaign_id, role);
CREATE INDEX idx_campaign_content_packs_role ON campaign_content_packs(campaign_id, role);

-- One document can be attached to many campaigns without duplicating bytes.
-- Detaching is a delete, never a purge: the installed bytes stay.
CREATE TRIGGER campaign_documents_no_self_reference
BEFORE INSERT ON campaign_documents
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'campaign_documents references a missing campaign')
  WHERE NOT EXISTS (
    SELECT 1 FROM campaigns WHERE campaign_id = NEW.campaign_id
  );
END;

CREATE TRIGGER campaign_content_packs_no_self_reference
BEFORE INSERT ON campaign_content_packs
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'campaign_content_packs references a missing campaign')
  WHERE NOT EXISTS (
    SELECT 1 FROM campaigns WHERE campaign_id = NEW.campaign_id
  );
END;
