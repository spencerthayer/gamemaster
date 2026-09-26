-- Mechanical parameters a generation used, with their provenance.
--
-- 0023 shipped and recorded its checksum, so this is a separate migration
-- rather than an edit to it. `migrate` refuses a modified applied migration,
-- and rightly so: silently changing applied history is how a database stops
-- matching the file that supposedly created it.
--
-- Provenance is stored separately from the value so a later reader can tell
-- what the model proposed from what the rules or a GM established.

ALTER TABLE generation_receipts
ADD COLUMN parameters TEXT NOT NULL DEFAULT '[]';
