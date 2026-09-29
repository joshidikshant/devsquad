CREATE TABLE decision_cache (
  cache_key TEXT PRIMARY KEY
    CHECK(length(cache_key) = 64 AND cache_key NOT GLOB '*[^0-9a-f]*'),
  request_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN (
    'reserved', 'running', 'succeeded', 'abstained', 'invalid',
    'unavailable', 'indeterminate', 'cancelled'
  )),
  response_json TEXT,
  response_sha256 TEXT
    CHECK(response_sha256 IS NULL OR (
      length(response_sha256) = 64
      AND response_sha256 NOT GLOB '*[^0-9a-f]*'
    )),
  billable_calls INTEGER NOT NULL DEFAULT 0 CHECK(billable_calls IN (0, 1)),
  usage_json TEXT,
  owner_id TEXT NOT NULL,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK(
    (status IN ('reserved', 'running') AND response_json IS NULL)
    OR status NOT IN ('reserved', 'running')
  )
);

CREATE TABLE run_decision_observations (
  run_id TEXT NOT NULL REFERENCES runs(id),
  purpose_id TEXT NOT NULL,
  cache_key TEXT NOT NULL REFERENCES decision_cache(cache_key),
  mode TEXT NOT NULL CHECK(mode IN ('shadow', 'advisory')),
  applied INTEGER NOT NULL DEFAULT 0 CHECK(applied IN (0, 1)),
  effect_json TEXT,
  recorded_at TEXT NOT NULL,
  PRIMARY KEY(run_id, purpose_id)
);

CREATE INDEX decision_cache_status
ON decision_cache(status, updated_at, cache_key);

CREATE INDEX run_decision_observations_cache
ON run_decision_observations(cache_key, run_id);
