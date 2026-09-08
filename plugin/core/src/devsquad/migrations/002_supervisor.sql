ALTER TABLE runs ADD COLUMN worktree_path TEXT;

CREATE TABLE supervisor_claims (
  run_id TEXT PRIMARY KEY REFERENCES runs(id),
  owner_id TEXT NOT NULL,
  fencing_token INTEGER NOT NULL,
  package_digest TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL,
  active INTEGER NOT NULL CHECK(active IN (0, 1))
);

CREATE TABLE attempts (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  worktree_path TEXT NOT NULL,
  attempt_token TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,
  pid INTEGER,
  pgid INTEGER,
  process_start_id TEXT,
  heartbeat_at TEXT NOT NULL,
  package_digest TEXT NOT NULL,
  stdout_artifact_id TEXT REFERENCES artifacts(id),
  stderr_artifact_id TEXT REFERENCES artifacts(id),
  output_metadata TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE UNIQUE INDEX one_active_writer_per_worktree
ON attempts(worktree_path)
WHERE status IN ('reserved', 'running', 'cancelling', 'ownership_ambiguous');

CREATE INDEX attempts_run ON attempts(run_id, created_at);
