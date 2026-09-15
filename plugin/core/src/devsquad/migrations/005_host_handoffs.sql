CREATE TABLE handoffs (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  sequence INTEGER NOT NULL CHECK(sequence > 0),
  packet_json TEXT NOT NULL,
  packet_sha256 TEXT NOT NULL
    CHECK(length(packet_sha256) = 64 AND packet_sha256 NOT GLOB '*[^0-9a-f]*'),
  status TEXT NOT NULL CHECK(status IN ('open', 'submitted', 'consumed', 'cancelled')),
  created_run_version INTEGER NOT NULL CHECK(created_run_version > 0),
  submitted_run_version INTEGER,
  created_at TEXT NOT NULL,
  closed_at TEXT,
  UNIQUE(run_id, sequence)
);

CREATE UNIQUE INDEX one_pending_handoff_per_run
ON handoffs(run_id)
WHERE status IN ('open', 'submitted');

ALTER TABLE claims ADD COLUMN handoff_id TEXT REFERENCES handoffs(id);
ALTER TABLE claims ADD COLUMN lease_expires_at TEXT;
ALTER TABLE claims ADD COLUMN renewed_at TEXT;

CREATE UNIQUE INDEX one_current_claim_per_handoff
ON claims(handoff_id)
WHERE handoff_id IS NOT NULL;

CREATE TABLE handoff_submissions (
  id TEXT PRIMARY KEY,
  handoff_id TEXT NOT NULL REFERENCES handoffs(id),
  submission_id TEXT NOT NULL,
  submission_hash TEXT NOT NULL
    CHECK(length(submission_hash) = 64 AND submission_hash NOT GLOB '*[^0-9a-f]*'),
  owner_id TEXT NOT NULL,
  fencing_token INTEGER NOT NULL CHECK(fencing_token > 0),
  disposition TEXT NOT NULL CHECK(disposition IN ('accept', 'revise', 'reject')),
  decision_json TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK(outcome IN ('recorded', 'rejected')),
  rejection_code TEXT,
  recorded_run_version INTEGER NOT NULL CHECK(recorded_run_version > 0),
  created_at TEXT NOT NULL,
  CHECK(
    (outcome = 'recorded' AND rejection_code IS NULL)
    OR (outcome = 'rejected' AND rejection_code IS NOT NULL)
  ),
  UNIQUE(handoff_id, submission_id, submission_hash)
);

CREATE UNIQUE INDEX one_recorded_submission_per_handoff
ON handoff_submissions(handoff_id)
WHERE outcome = 'recorded';

CREATE INDEX handoff_submission_id_lookup
ON handoff_submissions(handoff_id, submission_id);
