ALTER TABLE runs ADD COLUMN package_path TEXT;
ALTER TABLE runs ADD COLUMN package_digest TEXT;
ALTER TABLE runs ADD COLUMN supersedes_run_id TEXT REFERENCES runs(id);
