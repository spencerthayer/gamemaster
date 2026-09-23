-- Campaign plugin release version copied from the manifest at create time.
ALTER TABLE campaigns ADD COLUMN system_version TEXT NULL;
