CREATE TABLE experiments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id TEXT NOT NULL UNIQUE,
  project_id TEXT REFERENCES projects(id),
  project_path TEXT NOT NULL,
  spec_json TEXT NOT NULL,
  spec_sha256 TEXT NOT NULL
    CHECK(length(spec_sha256) = 64 AND spec_sha256 NOT GLOB '*[^0-9a-f]*'),
  evaluation_json TEXT NOT NULL,
  evaluation_sha256 TEXT NOT NULL
    CHECK(length(evaluation_sha256) = 64 AND evaluation_sha256 NOT GLOB '*[^0-9a-f]*'),
  verdict TEXT NOT NULL CHECK(verdict IN ('no_change', 'promotion_proposal')),
  recorded_at TEXT NOT NULL
);

CREATE INDEX experiments_project_history
ON experiments(project_id, recorded_at, id);
