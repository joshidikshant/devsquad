CREATE TABLE pool_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  observation_id TEXT NOT NULL UNIQUE,
  pool_id TEXT NOT NULL,
  window_id TEXT NOT NULL,
  applies_to_json TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  source TEXT NOT NULL CHECK(source IN ('native_reported', 'manual_reported', 'estimated')),
  used REAL,
  limit_value REAL,
  unit TEXT NOT NULL CHECK(unit IN ('percent', 'requests', 'tokens', 'provider-native-string')),
  resets_at TEXT,
  confidence TEXT NOT NULL CHECK(confidence IN ('confirmed', 'reported', 'estimated')),
  recorded_at TEXT NOT NULL,
  CHECK(used IS NULL OR used >= 0),
  CHECK(limit_value IS NULL OR limit_value >= 0)
);

CREATE INDEX pool_observations_lookup
ON pool_observations(pool_id, window_id, observed_at);

CREATE TABLE pool_reservations (
  id TEXT PRIMARY KEY,
  pool_id TEXT NOT NULL,
  run_id TEXT NOT NULL REFERENCES runs(id),
  attempt_id TEXT REFERENCES attempts(id),
  purpose TEXT NOT NULL CHECK(purpose IN ('attempt', 'qualification', 'classifier')),
  profile_id TEXT,
  reserved_at TEXT NOT NULL,
  reconciled_at TEXT,
  reconcile_reason TEXT,
  UNIQUE(attempt_id),
  CHECK(
    (reconciled_at IS NULL AND reconcile_reason IS NULL)
    OR (reconciled_at IS NOT NULL AND reconcile_reason IS NOT NULL)
  )
);

CREATE INDEX pool_reservations_active
ON pool_reservations(pool_id, reserved_at)
WHERE reconciled_at IS NULL;
