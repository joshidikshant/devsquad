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
