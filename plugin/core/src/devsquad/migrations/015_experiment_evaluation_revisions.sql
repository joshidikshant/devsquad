-- Never overwrite the original evaluation. Explicit reviews pin a predecessor
-- and reuse the original frozen spec/assignments, not new independent samples.
CREATE TABLE experiment_evaluation_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  revision_id TEXT NOT NULL UNIQUE,
  experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
  previous_evaluation_sha256 TEXT NOT NULL
    CHECK(length(previous_evaluation_sha256) = 64 AND previous_evaluation_sha256 NOT GLOB '*[^0-9a-f]*'),
  evaluation_json TEXT NOT NULL,
  evaluation_sha256 TEXT NOT NULL
    CHECK(length(evaluation_sha256) = 64 AND evaluation_sha256 NOT GLOB '*[^0-9a-f]*'),
  verdict TEXT NOT NULL CHECK(verdict IN ('no_change', 'promotion_proposal')),
  recorded_at TEXT NOT NULL,
  UNIQUE(experiment_id, evaluation_sha256)
);

CREATE INDEX experiment_evaluation_review_history
ON experiment_evaluation_revisions(experiment_id, id);
