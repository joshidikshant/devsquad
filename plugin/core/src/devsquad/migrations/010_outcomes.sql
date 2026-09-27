CREATE TABLE outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  outcome_id TEXT NOT NULL UNIQUE,
  run_id TEXT NOT NULL REFERENCES runs(id),
  kind TEXT NOT NULL CHECK(kind IN ('final', 'late_correction')),
  verdict TEXT NOT NULL CHECK(verdict IN ('succeeded', 'failed', 'cancelled', 'escaped_defect', 'corrected')),
  selection_mode TEXT NOT NULL CHECK(selection_mode IN ('automatic', 'pinned', 'experimental')),
  observed_at TEXT NOT NULL,
  corrects_outcome_id TEXT REFERENCES outcomes(outcome_id),
  payload_json TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL
    CHECK(length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
  recorded_at TEXT NOT NULL,
  CHECK(
    (kind = 'final' AND verdict IN ('succeeded', 'failed', 'cancelled') AND corrects_outcome_id IS NULL)
    OR
    (kind = 'late_correction' AND verdict IN ('escaped_defect', 'corrected') AND corrects_outcome_id IS NOT NULL)
  )
);

CREATE UNIQUE INDEX one_final_outcome_per_run
ON outcomes(run_id)
WHERE kind = 'final';

CREATE INDEX outcomes_run_history
ON outcomes(run_id, observed_at, id);
