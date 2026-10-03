"""Saved-run v2 evaluation through real offline workers and public completion."""

from datetime import datetime, timezone
import json
import tempfile
from pathlib import Path
import unittest

from experiment_runtime_fixture import ExperimentRuntimeFixture
from test_experiment_provenance import digest
from test_learning import experimental_final

from devsquad.contracts import ContractError
from devsquad.store import canonical_json


class ExperimentSavedRunsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="devsquad-saved-experiment-")
        self.addCleanup(self.temporary.cleanup)
        self.fixture = ExperimentRuntimeFixture(Path(self.temporary.name))
        self.addCleanup(self.fixture.close)

    def test_disjoint_public_runs_supply_v2_evaluation_without_submitted_provenance(self):
        self.fixture.run_all()
        self.assertEqual(len(set(self.fixture.runs.values())), 4)
        store = self.fixture.store()
        try:
            rows = store.connection.execute("SELECT id,status,role,profile_id FROM attempts").fetchall()
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(row["status"] == "finished" and row["role"] == "reviewer" for row in rows))
            self.assertEqual({row["profile_id"] for row in rows}, {"profile-a", "profile-b"})
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM experiment_assignments").fetchone()[0], 4)
        finally:
            store.close()
        result = self.fixture.service.policy_evaluate(self.fixture.spec)
        evaluation = result["evaluation"]
        self.assertEqual(evaluation["schema_version"], 2)
        self.assertEqual(evaluation["verdict"], "promotion_proposal")
        self.assertEqual(evaluation["metrics"]["evaluation"]["available_pairs"], 1)
        self.assertEqual(evaluation["metrics"]["held_out"]["available_pairs"], 1)
        self.assertEqual(len(evaluation["evidence_sha256"]), 64)
        replay = self.fixture.service.policy_evaluate(self.fixture.spec)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay, {**result, "replayed": True})

    def test_public_issue_delivery_pairs_bind_baseline_and_all_real_attempts(self):
        fixture = ExperimentRuntimeFixture(self.fixture.root / "delivery", workflow="issue-delivery")
        self.addCleanup(fixture.close)
        source_before = fixture.git("status", "--porcelain")
        head_before = fixture.git("rev-parse", "HEAD")
        fixture.run_all()
        result = fixture.service.policy_evaluate(fixture.spec)
        self.assertEqual(result["evaluation"]["verdict"], "promotion_proposal")
        self.assertTrue(result["eligibility"]["eligible"])
        self.assertEqual(fixture.spec["variable"]["role"], "implementer")
        store = fixture.store()
        try:
            for run_id in fixture.runs.values():
                attempts = store.attempts_for_run(run_id)
                self.assertEqual([row["role"] for row in attempts], ["implementer", "reviewer"])
                self.assertTrue(all(row["status"] == "finished" for row in attempts))
                snapshot = json.loads(store.run(run_id)["mutable_snapshot"])
                self.assertNotEqual(snapshot["workspace"]["target_oid"], snapshot["target_oid"])
        finally:
            store.close()
        self.assertEqual(fixture.git("status", "--porcelain"), source_before)
        self.assertEqual(fixture.git("rev-parse", "HEAD"), head_before)

    def test_project_symlink_alias_preserves_the_predeclared_spec_hash(self):
        alias = self.fixture.root / "project-alias"
        alias.symlink_to(self.fixture.repo, target_is_directory=True)
        self.fixture.spec["project_path"] = str(alias)
        frozen_hash = digest(self.fixture.spec)
        self.fixture.run_all()
        result = self.fixture.service.policy_evaluate(self.fixture.spec)
        self.assertEqual(result["evaluation"]["verdict"], "promotion_proposal")
        self.assertEqual(result["experiment"]["project_path"], str(alias))
        self.assertEqual(result["evaluation"]["spec_sha256"], frozen_hash)
        store = self.fixture.store()
        try:
            declared = store.connection.execute(
                "SELECT spec_json,spec_sha256 FROM experiment_specs WHERE experiment_id=?",
                (self.fixture.spec["experiment_id"],),
            ).fetchone()
            saved = store.connection.execute(
                "SELECT spec_json,spec_sha256 FROM experiments WHERE experiment_id=?",
                (self.fixture.spec["experiment_id"],),
            ).fetchone()
            self.assertEqual(tuple(saved), tuple(declared))
            self.assertEqual(saved["spec_sha256"], frozen_hash)
        finally:
            store.close()

    def test_real_terminal_worker_failure_is_retained_as_failed_exposure(self):
        fixture = ExperimentRuntimeFixture(self.fixture.root / "terminal-failure", fail_candidate=True)
        self.addCleanup(fixture.close)
        fixture.run_all()
        store = fixture.store()
        try:
            for case_id in ("eval-1", "hold-1"):
                run_id = fixture.runs[(case_id, "candidate")]
                self.assertEqual(store.run(run_id)["state"], "failed")
                attempt = store.connection.execute(
                    "SELECT * FROM attempts WHERE run_id=?", (run_id,),
                ).fetchone()
                self.assertEqual(attempt["status"], "finished")
                metadata = json.loads(attempt["output_metadata"])
                self.assertNotIn("failure", metadata)
                self.assertEqual(metadata["stdout"]["captured_bytes"], 0)
                self.assertGreater(metadata["stderr"]["captured_bytes"], 0)
        finally:
            store.close()
        evaluation = fixture.service.policy_evaluate(fixture.spec)["evaluation"]
        self.assertEqual(evaluation["verdict"], "no_change")
        for split in ("evaluation", "held_out"):
            self.assertEqual(evaluation["metrics"][split]["available_pairs"], 1)
            self.assertEqual(evaluation["metrics"][split]["candidate_success_rate"], 0.0)
        self.assertTrue(all(case["candidate_verdict"] == "failed" for case in evaluation["cases"]))

    def test_failed_receipt_requires_matching_terminal_attempt_even_with_valid_hash(self):
        fixture = ExperimentRuntimeFixture(self.fixture.root / "failure-receipt", fail_candidate=True)
        self.addCleanup(fixture.close)
        run_id = fixture.run_arm("eval-1", "candidate")
        store = fixture.store()
        try:
            artifact = dict(store.connection.execute(
                "SELECT * FROM artifacts WHERE run_id=? AND name='result-receipt.json'", (run_id,),
            ).fetchone())
            path = Path(artifact["path"])
            original = path.read_bytes()
            receipt = json.loads(original)
            changed_profile = json.loads(original)
            changed_profile["attempts"][0]["selected_profile"]["profile"]["model_id"] = "wrong-model"
            changed_status = json.loads(original)
            changed_status["attempts"][0]["status"] = "succeeded"
            mutations = [
                {**receipt, "attempts": None},
                {**receipt, "attempts": []},
                {**receipt, "state": "succeeded"},
                {**receipt, "run_id": "another-run"},
                changed_profile, changed_status,
            ]
            # Corrupt only negative evidence; each real failed run and its
            # opaque native streams were produced through the public runtime.
            for changed in mutations:
                with self.subTest(receipt=changed):
                    content = canonical_json(changed).encode()
                    path.write_bytes(content)
                    store.connection.execute(
                        "UPDATE artifacts SET sha256=?,byte_size=? WHERE id=?",
                        (digest(changed), len(content), artifact["id"]),
                    )
                    try:
                        with self.assertRaises(ContractError):
                            fixture.service.policy_evaluate(fixture.spec)
                    finally:
                        path.write_bytes(original)
                        store.connection.execute(
                            "UPDATE artifacts SET sha256=?,byte_size=? WHERE id=?",
                            (artifact["sha256"], artifact["byte_size"], artifact["id"]),
                        )
        finally:
            store.close()

    def test_success_cannot_hide_missing_review_behind_failure_metadata(self):
        run_id = self.fixture.run_arm("eval-1", "candidate")
        store = self.fixture.store()
        try:
            attempt = store.connection.execute(
                "SELECT id,output_metadata FROM attempts WHERE run_id=?", (run_id,),
            ).fetchone()
            artifact_name = f"review-attempt-{attempt['id']}.json"
            metadata = json.loads(attempt["output_metadata"])
            metadata["failure"] = {"code": "CLI_ERROR"}
            store.connection.execute(
                "UPDATE artifacts SET name='hidden-review.json' WHERE run_id=? AND name=?",
                (run_id, artifact_name),
            )
            store.connection.execute(
                "UPDATE attempts SET output_metadata=? WHERE id=?",
                (canonical_json(metadata), attempt["id"]),
            )
            with self.assertRaisesRegex(ContractError, "failed reviewer receipt"):
                self.fixture.service.policy_evaluate(self.fixture.spec)
        finally:
            store.close()

    def test_reader_uses_prelaunch_snapshot_not_later_mutable_snapshot(self):
        self.fixture.run_all()
        run_id = self.fixture.runs[("hold-1", "candidate")]
        store = self.fixture.store()
        try:
            original = store.run(run_id)["mutable_snapshot"]
            changed = json.loads(original)
            changed["task"]["goal"] = "Mutable continuation no longer describes the original trial."
            changed["routing"]["roles"]["reviewer"]["selected"]["profile"]["model_id"] = "changed-model"
            changed["experiment_assignment"]["spec_sha256"] = "0" * 64
            store.connection.execute(
                "UPDATE runs SET mutable_snapshot=? WHERE id=?", (canonical_json(changed), run_id),
            )
            try:
                result = self.fixture.service.policy_evaluate(self.fixture.spec)
                self.assertEqual(result["evaluation"]["verdict"], "promotion_proposal")
            finally:
                store.connection.execute("UPDATE runs SET mutable_snapshot=? WHERE id=?", (original, run_id))
            replay = self.fixture.service.policy_evaluate(self.fixture.spec)
            self.assertEqual(replay, {**result, "replayed": True})
        finally:
            store.close()

    def test_success_after_real_wrong_profile_fallback_cannot_credit_declared_arm(self):
        fixture = ExperimentRuntimeFixture(self.fixture.root / "fallback", with_fallback=True)
        self.addCleanup(fixture.close)
        run_id = fixture.run_arm("eval-1", "candidate")
        self.assertEqual(fixture.service.status(run_id)["state"], "succeeded")
        store = fixture.store()
        try:
            attempts = store.connection.execute(
                "SELECT profile_id,profile_index,output_metadata FROM attempts WHERE run_id=? ORDER BY profile_index",
                (run_id,),
            ).fetchall()
            self.assertEqual([row["profile_id"] for row in attempts], ["candidate-fixture-fail", "profile-fallback"])
            self.assertEqual([row["profile_index"] for row in attempts], [0, 1])
            self.assertIsNotNone(json.loads(attempts[0]["output_metadata"])["failure"])
            self.assertEqual(len(store.outcomes_for_run(run_id)), 1)
            with self.assertRaisesRegex(ContractError, "declared arm"):
                fixture.service.policy_evaluate(fixture.spec)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM experiments").fetchone()[0], 0)
        finally:
            store.close()

    def test_missing_partner_is_visible_without_an_invented_pair(self):
        self.fixture.run_all(skip=("hold-1", "control"))
        evaluation = self.fixture.service.policy_evaluate(self.fixture.spec)["evaluation"]
        self.assertEqual(evaluation["verdict"], "no_change")
        self.assertEqual(evaluation["metrics"]["evaluation"]["available_pairs"], 1)
        self.assertEqual(evaluation["metrics"]["held_out"]["available_pairs"], 0)
        self.assertIn("insufficient_held_out_pairs", evaluation["reasons"])
        row = next(row for row in evaluation["cases"] if row["case_id"] == "hold-1")
        self.assertEqual(row["status"], "missing")
        self.assertEqual(row["missing"], ["control"])

    def test_public_prelaunch_cancel_has_no_trial_exposure(self):
        self.fixture.run_all(no_attempt=("hold-1", "candidate"))
        cancelled_run = self.fixture.runs[("hold-1", "candidate")]
        store = self.fixture.store()
        try:
            self.assertEqual(store.run(cancelled_run)["state"], "cancelled")
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE run_id=?", (cancelled_run,),
            ).fetchone()[0], 0)
            self.assertEqual(len(store.outcomes_for_run(cancelled_run)), 1)
        finally:
            store.close()
        evaluation = self.fixture.service.policy_evaluate(self.fixture.spec)["evaluation"]
        self.assertEqual(evaluation["verdict"], "no_change")
        self.assertEqual(evaluation["metrics"]["evaluation"]["available_pairs"], 1)
        self.assertEqual(evaluation["metrics"]["held_out"]["available_pairs"], 0)
        row = next(row for row in evaluation["cases"] if row["case_id"] == "hold-1")
        self.assertEqual(row["status"], "missing")
        self.assertIn("candidate", row["missing"])

    def test_saved_tampering_is_rejected_even_when_partner_is_missing(self):
        self.fixture.run_all(skip=("hold-1", "control"))
        run_id = self.fixture.runs[("hold-1", "candidate")]
        store = self.fixture.store()
        try:
            attempt = dict(store.connection.execute(
                "SELECT * FROM attempts WHERE run_id=?", (run_id,),
            ).fetchone())
            assignment = dict(store.connection.execute(
                "SELECT * FROM experiment_assignments WHERE run_id=?", (run_id,),
            ).fetchone())
            changed_assignment = json.loads(assignment["assignment_json"])
            changed_assignment["profile_id"] = "profile-a"
            changed_snapshot = json.loads(assignment["snapshot_json"])
            changed_snapshot["task"]["goal"] = "A changed input after preparation."
            mutations = [
                ("actual_profile", "attempts", "id", attempt["id"],
                 {"profile_id": "profile-a"}, {"profile_id": attempt["profile_id"]}),
                ("actual_index", "attempts", "id", attempt["id"],
                 {"profile_index": 1}, {"profile_index": attempt["profile_index"]}),
                ("actual_package", "attempts", "id", attempt["id"],
                 {"package_digest": "0" * 64}, {"package_digest": attempt["package_digest"]}),
                ("saved_assignment", "experiment_assignments", "run_id", run_id,
                 {"assignment_json": canonical_json(changed_assignment), "assignment_sha256": digest(changed_assignment)},
                 {"assignment_json": assignment["assignment_json"], "assignment_sha256": assignment["assignment_sha256"]}),
                ("paired_input", "experiment_assignments", "run_id", run_id,
                 {"snapshot_json": canonical_json(changed_snapshot)}, {"snapshot_json": assignment["snapshot_json"]}),
            ]
            # SQL is intentionally limited to these negative corruptions. The
            # positive runs above were prepared/executed/completed publicly.
            for name, table, key, identity, changed, original in mutations:
                with self.subTest(mutation=name):
                    columns = ",".join(f"{column}=?" for column in changed)
                    statement = f"UPDATE {table} SET {columns} WHERE {key}=?"
                    store.connection.execute(statement, [*changed.values(), identity])
                    try:
                        with self.assertRaises(ContractError):
                            self.fixture.service.policy_evaluate(self.fixture.spec)
                        self.assertEqual(store.connection.execute(
                            "SELECT COUNT(*) FROM experiments WHERE experiment_id=?",
                            (self.fixture.spec["experiment_id"],),
                        ).fetchone()[0], 0)
                    finally:
                        store.connection.execute(statement, [*original.values(), identity])
        finally:
            store.close()

    def test_late_correction_invalidates_replay_without_rewriting_historical_evaluation(self):
        self.fixture.run_all()
        first = self.fixture.service.policy_evaluate(self.fixture.spec)
        self.assertEqual(first["evaluation"]["verdict"], "promotion_proposal")
        store = self.fixture.store()
        try:
            original = dict(store.connection.execute(
                "SELECT spec_json,spec_sha256,evaluation_json,evaluation_sha256,recorded_at "
                "FROM experiments WHERE experiment_id=?", (self.fixture.spec["experiment_id"],),
            ).fetchone())
        finally:
            store.close()
        correction = experimental_final("escaped-candidate-hold-1", "succeeded")
        correction.update({
            "kind": "late_correction", "verdict": "escaped_defect",
            "corrects_outcome_id": "candidate-hold-1",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "summary": "An escaped defect was found after the saved evaluation.",
        })
        run_id = self.fixture.runs[("hold-1", "candidate")]
        self.fixture.service.outcome_add(run_id, correction)
        with self.assertRaises(ContractError):
            self.fixture.service.policy_evaluate(self.fixture.spec)
        store = self.fixture.store()
        try:
            saved = dict(store.connection.execute(
                "SELECT spec_json,spec_sha256,evaluation_json,evaluation_sha256,recorded_at "
                "FROM experiments WHERE experiment_id=?", (self.fixture.spec["experiment_id"],),
            ).fetchone())
            self.assertEqual(saved, original)
            self.assertEqual(len(store.outcomes_for_run(run_id)), 2)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
