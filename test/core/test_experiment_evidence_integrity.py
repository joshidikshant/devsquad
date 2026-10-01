"""Corrupt only negative copies of otherwise publicly executed arm evidence."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiment_runtime_fixture import ExperimentRuntimeFixture
from devsquad.contracts import ContractError
from devsquad.experiment_evidence import read_experiment_chains
from devsquad.experiment_provenance import assignment_for
from devsquad.store import canonical_json
from test_experiment_provenance import digest


class ExperimentEvidenceIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="devsquad-evidence-integrity-")
        self.addCleanup(self.temporary.cleanup)
        self.fixture = ExperimentRuntimeFixture(Path(self.temporary.name))
        self.addCleanup(self.fixture.close)
        self.run_id = self.fixture.run_arm("eval-1", "candidate")
        self.store = self.fixture.store()
        self.addCleanup(self.store.close)

    def assert_rejected_without_persistence(self):
        with self.assertRaises(ContractError):
            self.fixture.service.policy_evaluate(self.fixture.spec)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM experiments").fetchone()[0], 0)

    def test_fence_event_and_outcome_corruptions_cannot_be_hidden_by_missing_partner(self):
        assignment = dict(self.store.connection.execute(
            "SELECT * FROM experiment_assignments WHERE run_id=?", (self.run_id,),
        ).fetchone())
        run = self.store.run(self.run_id)
        outcome = dict(self.store.connection.execute(
            "SELECT * FROM outcomes WHERE run_id=?", (self.run_id,),
        ).fetchone())
        queued = dict(self.store.connection.execute(
            "SELECT * FROM events WHERE run_id=? AND run_version=?",
            (self.run_id, assignment["frozen_run_version"]),
        ).fetchone())
        claimed = dict(self.store.connection.execute(
            "SELECT * FROM events WHERE run_id=? AND type='supervisor.claimed'", (self.run_id,),
        ).fetchone())
        running = dict(self.store.connection.execute(
            "SELECT * FROM events WHERE run_id=? AND type='run.running'", (self.run_id,),
        ).fetchone())
        changed_launch = json.loads(running["payload"])
        changed_launch["pid"] += 1
        mutations = [
            ("fence_version", "experiment_assignments", "run_id", self.run_id,
             "frozen_run_version", 1, assignment["frozen_run_version"]),
            ("fence_token", "experiment_assignments", "run_id", self.run_id,
             "preparation_fencing_token", 999, assignment["preparation_fencing_token"]),
            ("assignment_hash", "experiment_assignments", "run_id", self.run_id,
             "assignment_sha256", "0" * 64, assignment["assignment_sha256"]),
            ("queued_event", "events", "id", queued["id"], "type", "ignored", queued["type"]),
            ("claim_event", "events", "id", claimed["id"], "type", "ignored", claimed["type"]),
            ("launch_event", "events", "id", running["id"], "payload", canonical_json(changed_launch), running["payload"]),
            ("final_hash", "outcomes", "id", outcome["id"], "payload_sha256", "0" * 64, outcome["payload_sha256"]),
            ("final_row_verdict", "outcomes", "id", outcome["id"], "verdict", "failed", outcome["verdict"]),
            ("run_package", "runs", "id", self.run_id, "package_digest", "0" * 64, run["package_digest"]),
            ("run_terminal", "runs", "id", self.run_id, "state", "failed", run["state"]),
        ]
        for name, table, key, identity, column, changed, original in mutations:
            with self.subTest(mutation=name):
                statement = f"UPDATE {table} SET {column}=? WHERE {key}=?"
                self.store.connection.execute(statement, (changed, identity))
                try:
                    self.assert_rejected_without_persistence()
                finally:
                    self.store.connection.execute(statement, (original, identity))
        self.assertEqual(self.fixture.service.policy_evaluate(self.fixture.spec)["evaluation"]["verdict"], "no_change")

    def test_saved_output_bytes_must_match_the_durable_capture(self):
        artifact = self.store.connection.execute(
            "SELECT f.path FROM attempts a JOIN artifacts f ON f.id=a.stdout_artifact_id WHERE a.run_id=?",
            (self.run_id,),
        ).fetchone()
        path = Path(artifact["path"])
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"corrupt")
            self.assert_rejected_without_persistence()
        finally:
            path.write_bytes(original)

    def test_imported_profile_drift_is_rejected_even_with_a_valid_artifact_hash(self):
        row = dict(self.store.connection.execute(
            "SELECT * FROM artifacts WHERE run_id=? AND name LIKE 'review-attempt-%.json'",
            (self.run_id,),
        ).fetchone())
        path = Path(row["path"])
        original = path.read_bytes()
        changed = json.loads(original)
        selected = changed["selected_profile"]
        selected["profile"]["model_id"] = "a-different-executed-model"
        selected["profile_sha256"] = digest(selected["profile"])
        content = (canonical_json(changed) + "\n").encode()
        # Simulate internally hash-consistent imported identity drift. A file
        # checksum alone cannot bind its selected profile to the original arm.
        try:
            path.write_bytes(content)
            self.store.connection.execute(
                "UPDATE artifacts SET sha256=?,byte_size=? WHERE id=?",
                (hashlib.sha256(content).hexdigest(), len(content), row["id"]),
            )
            self.assert_rejected_without_persistence()
        finally:
            path.write_bytes(original)
            self.store.connection.execute(
                "UPDATE artifacts SET sha256=?,byte_size=? WHERE id=?",
                (row["sha256"], row["byte_size"], row["id"]),
            )

    def test_trial_reservation_rejects_changed_inputs_before_any_worker(self):
        resolve = self.fixture.service._resolve_snapshot

        def assigned(*args, **kwargs):
            snapshot = resolve(*args, **kwargs)
            snapshot["experiment_spec"] = copy.deepcopy(self.fixture.spec)
            snapshot["experiment_assignment"] = assignment_for(
                self.fixture.spec, "hold-1", "control", project_common_dir=self.fixture.common,
            )
            return snapshot

        with (patch.object(self.fixture.service, "_resolve_snapshot", side_effect=assigned),
              patch.object(self.fixture.service, "_spawn_daemon", return_value=0)):
            started = self.fixture.service.start(
                self.fixture.task("hold-1", "control"), "reserved-negative",
                _internal_review_fixture={"verdict": "clean", "summary": "Fixture.", "findings": []},
            )
        run_id = started["run_id"]
        self.fixture.runs[("hold-1", "control")] = run_id
        self.assertEqual(started["state"], "queued")
        run = self.store.run(run_id)
        original = json.loads(run["mutable_snapshot"])
        selected = original["routing"]["roles"]["reviewer"]["selected"]
        changed_profile = copy.deepcopy(original)
        slot = changed_profile["routing"]["roles"]["reviewer"]["selected"]
        slot["profile"]["model_id"] = "changed-after-preparation"
        slot["profile_sha256"] = digest(slot["profile"])
        changed_task = copy.deepcopy(original)
        changed_task["task"]["goal"] = "A different task after preparation."
        for changed in (changed_profile, changed_task, []):
            with self.subTest(snapshot_type=type(changed).__name__):
                self.store.connection.execute(
                    "UPDATE runs SET mutable_snapshot=? WHERE id=?", (canonical_json(changed), run_id),
                )
                try:
                    with self.assertRaisesRegex(ContractError, "experiment launch"):
                        self.store.reserve_attempt(
                            run_id, run["version"], "negative-worker", run["package_digest"], "reviewer",
                            account_pool_id=selected["profile"]["account_pool_id"],
                            profile_id=selected["profile_id"], profile_index=0,
                        )
                    self.assertEqual(self.store.connection.execute(
                        "SELECT COUNT(*) FROM attempts WHERE run_id=?", (run_id,),
                    ).fetchone()[0], 0)
                finally:
                    self.store.connection.execute(
                        "UPDATE runs SET mutable_snapshot=? WHERE id=?", (run["mutable_snapshot"], run_id),
                    )

    def test_reader_requires_one_consistent_transaction(self):
        self.assertFalse(self.store.connection.in_transaction)
        with self.assertRaisesRegex(ContractError, "consistent ledger transaction"):
            read_experiment_chains(
                self.store.connection, spec=self.fixture.spec,
                project_id=self.store.run(self.run_id)["project_id"],
                project_common_dir=self.fixture.common, evaluated_at="2026-10-01T00:00:00Z",
            )


if __name__ == "__main__":
    unittest.main()
