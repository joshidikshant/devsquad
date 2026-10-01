-- Predeclared experiments and arms are frozen by the run preparation fence,
-- before attempt reservation. Existing evaluations remain immutable history.
CREATE TABLE experiment_specs (
  experiment_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  spec_json TEXT NOT NULL,
  spec_sha256 TEXT NOT NULL
    CHECK(length(spec_sha256) = 64 AND spec_sha256 NOT GLOB '*[^0-9a-f]*'),
  recorded_at TEXT NOT NULL
);

CREATE TABLE experiment_assignments (
  run_id TEXT PRIMARY KEY REFERENCES runs(id),
  experiment_id TEXT NOT NULL REFERENCES experiment_specs(experiment_id),
  case_id TEXT NOT NULL,
  arm TEXT NOT NULL CHECK(arm IN ('control', 'candidate')),
  outcome_id TEXT NOT NULL,
  assignment_json TEXT NOT NULL,
  assignment_sha256 TEXT NOT NULL
    CHECK(length(assignment_sha256) = 64 AND assignment_sha256 NOT GLOB '*[^0-9a-f]*'),
  snapshot_json TEXT NOT NULL,
  package_digest TEXT NOT NULL
    CHECK(length(package_digest) = 64 AND package_digest NOT GLOB '*[^0-9a-f]*'),
  frozen_run_version INTEGER NOT NULL,
  preparation_fencing_token INTEGER NOT NULL,
  recorded_at TEXT NOT NULL,
  UNIQUE(experiment_id, case_id, arm),
  UNIQUE(experiment_id, outcome_id)
);
