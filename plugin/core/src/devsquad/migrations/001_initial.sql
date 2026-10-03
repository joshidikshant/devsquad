CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE projects (
  id TEXT PRIMARY KEY,
  git_common_dir TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE runs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  submitted_request TEXT NOT NULL,
  mutable_snapshot TEXT,
  state TEXT NOT NULL,
  phase TEXT,
  version INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(project_id, idempotency_key)
);

CREATE TABLE claims (
  run_id TEXT PRIMARY KEY REFERENCES runs(id),
  kind TEXT NOT NULL,
  fencing_token INTEGER NOT NULL,
  owner_id TEXT NOT NULL,
  active INTEGER NOT NULL CHECK(active IN (0, 1)),
  claimed_at TEXT NOT NULL
);

CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES runs(id),
  run_version INTEGER NOT NULL,
  type TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, run_version)
);

CREATE TABLE artifacts (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  name TEXT NOT NULL,
  path TEXT NOT NULL UNIQUE,
  sha256 TEXT NOT NULL,
  byte_size INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, name)
);

CREATE INDEX events_run_cursor ON events(run_id, id);
