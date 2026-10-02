-- New public runs request objective projection at admission. Legacy final
-- outcomes and missing legacy history are not rewritten or retroactively armed.
CREATE TABLE objective_outcome_jobs (
  run_id TEXT PRIMARY KEY REFERENCES runs(id),
  requested_at TEXT NOT NULL,
  completed_outcome_id TEXT REFERENCES outcomes(outcome_id)
);
CREATE INDEX pending_objective_outcomes ON objective_outcome_jobs(completed_outcome_id, run_id);
