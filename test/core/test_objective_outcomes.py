"""Public terminal-origin regressions: no manually imported final outcomes."""
import copy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "plugin/core/src"), str(ROOT / "test/core")]

from devsquad.store import ConflictError, Store
import test_review_runtime as review_fixtures


class ObjectiveOutcomeTest(unittest.TestCase):
    def setUp(self):
        self.fixture = review_fixtures.DurableBranchReviewTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.service = self.fixture.service
        self.fixture.task["checks"][0]["argv"] = [sys.executable, "-c", "print('verified')"]

    def outcome(self, run_id):
        store = self.service._store()
        try:
            rows = store.outcomes_for_run(run_id)
            self.assertEqual(len(rows), 1, "public terminal run must project exactly one final outcome")
            return rows[0]["outcome"]
        finally:
            store.close()

    def accept(self, run_id, waiting):
        claimed = self.service.handoff_claim(run_id, waiting["version"], "objective-fixture-host")
        decision = self.fixture.decision(claimed["handoff"]["packet"], "objective-accept", "accept", "Verified objective fixture evidence.")
        return self.service.handoff_complete(run_id, claimed["claim"], decision)

    def test_preparation_failure_projects_missing_evidence_truthfully(self):
        task = copy.deepcopy(self.fixture.task)
        task["project"]["target_ref"] = "nonexistent-objective-target"
        started = self.service.start(task, "objective-preparation-failure")
        self.assertEqual(started["state"], "failed")
        outcome = self.outcome(started["run_id"])
        self.assertEqual(outcome["verdict"], "failed")
        self.assertEqual(outcome["contributions"], [])
        self.assertTrue(all(c["status"] == "unknown" and not c["evidence_refs"] for c in outcome["criteria"]))

    def test_prelaunch_cancel_has_no_completed_exposure(self):
        with mock.patch.object(self.service, "_spawn_daemon", return_value=0):
            started = self.service.start(self.fixture.task, "objective-prelaunch-cancel", _internal_review_fixture=self.fixture.fixture)
        self.service.cancel(started["run_id"])
        outcome = self.outcome(started["run_id"])
        self.assertEqual(outcome["verdict"], "cancelled")
        self.assertEqual(outcome["contributions"], [])
        self.assertEqual(self.outcome(started["run_id"]), outcome)

    def test_worker_failure_is_not_independent_success(self):
        self.fixture.task["checks"][0]["cwd"] = "missing-check-directory"
        started = self.service.start(self.fixture.task, "objective-worker-failure", _internal_review_fixture=self.fixture.fixture)
        self.fixture.wait_state(started["run_id"], {"failed"})
        outcome = self.outcome(started["run_id"])
        self.assertEqual(outcome["verdict"], "failed")
        self.assertTrue(outcome["contributions"])
        self.assertTrue(all(c["result"] == "failed" and not c["independent_success"] for c in outcome["contributions"]))

    def test_host_completion_and_late_correction_are_append_only(self):
        run_id, waiting = self.fixture.start_waiting("objective-host")
        self.assertEqual(self.accept(run_id, waiting)["state"], "succeeded")
        final = self.outcome(run_id)
        self.assertEqual(final["verdict"], "succeeded")
        self.assertTrue(final["contributions"])
        correction = {**final, "outcome_id": "objective-late-correction", "kind": "late_correction", "verdict": "escaped_defect",
                      "corrects_outcome_id": final["outcome_id"], "observed_at": datetime.now(timezone.utc).isoformat(), "summary": "Explicit later escaped-defect evidence."}
        self.service.outcome_add(run_id, correction)
        self.service.result(run_id)
        store = self.service._store()
        try:
            rows = store.outcomes_for_run(run_id)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["outcome"], final)
        finally:
            store.close()

    def test_headless_completion_projects_without_manual_import(self):
        self.fixture.configure_fixture_headless()
        started = self.service.start(self.fixture.task, "objective-headless", _internal_review_fixture=self.fixture.fixture,
                                     _internal_lead_fixture={"disposition": "accept", "reason": "Verified fixture evidence."})
        completed = self.fixture.wait_state(started["run_id"], {"succeeded", "failed"})
        self.assertEqual(completed["state"], "succeeded")
        outcome = self.outcome(started["run_id"])
        self.assertEqual({c["role"] for c in outcome["contributions"]}, {"reviewer", "lead"})

    def test_projection_crash_replays_one_outcome_after_terminal_commit(self):
        run_id, waiting = self.fixture.start_waiting("objective-crash")
        with mock.patch.object(Store, "project_final_outcome", create=True, side_effect=RuntimeError("projection crash")):
            with self.assertRaisesRegex(RuntimeError, "projection crash"):
                self.accept(run_id, waiting)
        self.assertEqual(self.service.status(run_id)["state"], "succeeded")
        final = self.outcome(run_id)
        self.service.result(run_id)
        self.assertEqual(self.outcome(run_id), final)

    def test_repaired_failed_attempt_never_gets_independent_credit(self):
        self.fixture.configure_reviewer_fallback()
        run_id, waiting = self.fixture.start_waiting("objective-repaired")
        self.accept(run_id, waiting)
        contributions = self.outcome(run_id)["contributions"]
        self.assertEqual([c["result"] for c in contributions], ["failed", "repair"])
        self.assertTrue(all(not c["independent_success"] for c in contributions))

    def test_report_repairs_a_projection_crash_without_manual_outcome_import(self):
        run_id, waiting = self.fixture.start_waiting("objective-report-crash")
        with mock.patch.object(Store, "project_final_outcome", side_effect=RuntimeError("projection crash")):
            with self.assertRaisesRegex(RuntimeError, "projection crash"):
                self.accept(run_id, waiting)
        report = self.service.learning_report(self.fixture.repo)
        self.assertEqual(report["sample_size"], 1)
        self.assertEqual(report["missingness"]["terminal_runs_without_final_outcome"], 0)
        final = self.outcome(run_id)
        with self.assertRaisesRegex(ConflictError, "objective projections"):
            self.service.outcome_add(run_id, {**final, "outcome_id": "manual-replacement-final"})

    def test_corrupt_pending_projection_does_not_block_other_runs(self):
        run_id, waiting = self.fixture.start_waiting("objective-corrupt-pending")
        with mock.patch.object(Store, "project_final_outcome", side_effect=RuntimeError("projection crash")):
            with self.assertRaises(RuntimeError):
                self.accept(run_id, waiting)
        store = self.service._store()
        try:
            artifact = next(a for a in store.artifacts_for_run(run_id) if a["name"] == "receipt.json")
            Path(artifact["path"]).write_bytes(b"corrupt fixture receipt")
        finally:
            store.close()
        with self.assertRaisesRegex(ConflictError, "integrity"):
            self.service.status(run_id)
        good_id, good_waiting = self.fixture.start_waiting("objective-unrelated-good")
        self.assertEqual(self.accept(good_id, good_waiting)["state"], "succeeded")
        self.assertEqual(self.service.status(good_id)["state"], "succeeded")
        self.assertEqual(self.outcome(good_id)["verdict"], "succeeded")

    def test_proposal_repairs_pending_outcome_before_its_consistent_read(self):
        run_id, waiting = self.fixture.start_waiting("objective-proposal-crash")
        with mock.patch.object(Store, "project_final_outcome", side_effect=RuntimeError("projection crash")):
            with self.assertRaises(RuntimeError):
                self.accept(run_id, waiting)
        proposed = self.service.learning_propose(self.fixture.repo)
        self.assertEqual(proposed["proposal"]["sample_sizes"]["final_outcomes"], 1)
        self.assertEqual(self.outcome(run_id)["verdict"], "succeeded")

    def test_generic_worker_timeout_preserves_native_exit_and_projects_failure(self):
        task = copy.deepcopy(self.fixture.task)
        task["budget"]["wall_seconds"] = 2
        started = self.service.start(task, "objective-generic-timeout", _internal_fake_delay=4)
        self.fixture.wait_state(started["run_id"], {"failed"})
        final = self.outcome(started["run_id"])
        self.assertEqual(final["verdict"], "failed")
        self.assertEqual([c["result"] for c in final["contributions"]], ["failed"])


if __name__ == "__main__":
    unittest.main()
