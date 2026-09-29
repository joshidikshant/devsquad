CREATE TABLE profile_templates (
  template_id TEXT PRIMARY KEY,
  alias TEXT NOT NULL,
  update_mode TEXT NOT NULL CHECK(update_mode IN ('reviewed', 'guarded_auto')),
  policy_id TEXT NOT NULL,
  policy_version INTEGER NOT NULL CHECK(policy_version >= 1),
  payload_json TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL
    CHECK(length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
  recorded_at TEXT NOT NULL
);

CREATE INDEX profile_templates_alias_policy
ON profile_templates(alias, policy_id, policy_version, recorded_at);

CREATE TABLE concrete_profiles (
  profile_id TEXT PRIMARY KEY,
  profile_json TEXT NOT NULL,
  profile_sha256 TEXT NOT NULL
    CHECK(length(profile_sha256) = 64 AND profile_sha256 NOT GLOB '*[^0-9a-f]*'),
  recorded_at TEXT NOT NULL
);

CREATE TABLE qualification_runs (
  qualification_id TEXT PRIMARY KEY,
  alias TEXT NOT NULL,
  template_id TEXT NOT NULL REFERENCES profile_templates(template_id),
  profile_id TEXT NOT NULL REFERENCES concrete_profiles(profile_id),
  experiment_id TEXT REFERENCES experiments(experiment_id),
  evaluation_sha256 TEXT,
  verdict TEXT NOT NULL CHECK(verdict IN ('qualified', 'rejected', 'incomplete')),
  payload_json TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL
    CHECK(length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
  gate_failures_json TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  CHECK(
    (experiment_id IS NULL AND evaluation_sha256 IS NULL)
    OR
    (experiment_id IS NOT NULL AND length(evaluation_sha256) = 64
      AND evaluation_sha256 NOT GLOB '*[^0-9a-f]*')
  )
);

CREATE INDEX qualification_runs_alias_history
ON qualification_runs(alias, recorded_at, qualification_id);

CREATE TABLE profile_bindings (
  alias TEXT PRIMARY KEY,
  template_id TEXT NOT NULL REFERENCES profile_templates(template_id),
  profile_id TEXT NOT NULL REFERENCES concrete_profiles(profile_id),
  qualification_id TEXT REFERENCES qualification_runs(qualification_id),
  version INTEGER NOT NULL CHECK(version >= 1),
  updated_at TEXT NOT NULL
);

CREATE TABLE profile_binding_versions (
  alias TEXT NOT NULL,
  version INTEGER NOT NULL CHECK(version >= 1),
  template_id TEXT NOT NULL REFERENCES profile_templates(template_id),
  profile_id TEXT NOT NULL REFERENCES concrete_profiles(profile_id),
  qualification_id TEXT REFERENCES qualification_runs(qualification_id),
  decision_id TEXT,
  recorded_at TEXT NOT NULL,
  PRIMARY KEY(alias, version)
);

CREATE TABLE binding_decisions (
  decision_id TEXT PRIMARY KEY,
  alias TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('promote', 'rollback')),
  actor TEXT NOT NULL CHECK(actor IN ('human', 'guarded_auto')),
  from_version INTEGER NOT NULL CHECK(from_version >= 1),
  to_version INTEGER NOT NULL CHECK(to_version > from_version),
  from_profile_id TEXT NOT NULL REFERENCES concrete_profiles(profile_id),
  to_profile_id TEXT NOT NULL REFERENCES concrete_profiles(profile_id),
  qualification_id TEXT REFERENCES qualification_runs(qualification_id),
  request_sha256 TEXT NOT NULL
    CHECK(length(request_sha256) = 64 AND request_sha256 NOT GLOB '*[^0-9a-f]*'),
  receipt_json TEXT NOT NULL,
  receipt_sha256 TEXT NOT NULL
    CHECK(length(receipt_sha256) = 64 AND receipt_sha256 NOT GLOB '*[^0-9a-f]*'),
  recorded_at TEXT NOT NULL
);

CREATE INDEX binding_decisions_alias_history
ON binding_decisions(alias, to_version, decision_id);
