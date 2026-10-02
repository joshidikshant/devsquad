"""Public opt-in trials: declaration, shared budgets and lifecycle chain."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from experiment_runtime_fixture import ExperimentRuntimeFixture
import test_lifecycle as lifecycle_fixtures
from devsquad.contracts import ContractError


class PublicTrialTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="devsquad-public-trial-")
        self.addCleanup(self.temporary.cleanup)
        self.fixture = ExperimentRuntimeFixture(Path(self.temporary.name))
        self.addCleanup(self.fixture.close)

    def start(self, case="eval-1", arm="candidate", *, fixture=None):
        fixture = fixture or self.fixture
        started = fixture.service.trial_start(
            fixture.spec, case, arm, fixture.task(case, arm), f"public-trial-{case}-{arm}",
            _internal_review_fixture={"verdict": "clean", "summary": "Explicit offline trial review.", "findings": []},
        )
        fixture.runs[(case, arm)] = started["run_id"]
        return started

    def test_opt_in_declaration_is_immutable_and_exact_replay_does_not_launch_again(self):
        with patch.object(self.fixture.service, "_spawn_daemon", return_value=0) as spawn:
            first = self.start()
            replay = self.start()
        self.assertEqual(replay["run_id"], first["run_id"])
        self.assertFalse(replay["created"])
        spawn.assert_called_once()
        original = copy.deepcopy(self.fixture.spec)
        self.fixture.spec["hypothesis"] = "Changed after one arm was frozen."
        changed = self.start(arm="control")
        self.assertEqual(changed["state"], "failed")
        store = self.fixture.store()
        try:
            self.assertEqual(json.loads(store.connection.execute("SELECT spec_json FROM experiment_specs").fetchone()[0]), original)
            self.assertEqual(store.attempts_for_run(changed["run_id"]), [])
            self.assertEqual(len(store.outcomes_for_run(changed["run_id"])), 1)
        finally:
            store.close()

    def test_shared_experiment_budget_is_not_replenished_for_another_arm(self):
        self.fixture.spec["budget"]["max_worker_invocations"] = 1
        self.fixture.run_arm("eval-1", "control")
        second = self.start()
        self.assertEqual(self.fixture.wait(second["run_id"])["state"], "failed")
        store = self.fixture.store()
        try:
            self.assertEqual(store.attempts_for_run(second["run_id"]), [])
            self.assertEqual(store.outcomes_for_run(second["run_id"])[0]["outcome"]["contributions"], [])
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)
        finally:
            store.close()
        evaluation = self.fixture.service.policy_evaluate(self.fixture.spec)["evaluation"]
        self.assertEqual(evaluation["metrics"]["evaluation"]["available_pairs"], 0)

    def test_concurrent_arms_cannot_overbook_the_same_trial_budget(self):
        self.fixture.spec["budget"]["max_worker_invocations"] = 1
        with patch.object(self.fixture.service, "_spawn_daemon", return_value=0):
            runs = [self.start(arm=arm)["run_id"] for arm in ("control", "candidate")]
        barrier, errors = threading.Barrier(2), []
        def resume(run_id):
            try:
                barrier.wait(timeout=5)
                self.fixture.service.resume(run_id)
            except Exception as exc:
                errors.append(exc)
        workers = [threading.Thread(target=resume, args=(run_id,)) for run_id in runs]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        states = [self.fixture.wait(run_id)["state"] for run_id in runs]
        self.assertCountEqual(states, ["awaiting_host", "failed"])
        store = self.fixture.store()
        try:
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)
        finally:
            store.close()

    def test_fallback_cannot_spend_a_second_slot_after_the_trial_budget(self):
        fixture = ExperimentRuntimeFixture(self.fixture.root / "fallback", with_fallback=True)
        self.addCleanup(fixture.close)
        fixture.spec["budget"]["max_worker_invocations"] = 1
        started = self.start(fixture=fixture)
        self.assertEqual(fixture.wait(started["run_id"])["state"], "failed")
        store = fixture.store()
        try:
            attempts = store.attempts_for_run(started["run_id"])
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0]["profile_index"], 0)
            final = store.outcomes_for_run(started["run_id"])[0]["outcome"]
            self.assertEqual([c["result"] for c in final["contributions"]], ["failed"])
        finally:
            store.close()

    def test_experiment_deadline_blocks_later_launch_without_invented_exposure(self):
        self.fixture.spec["budget"]["wall_seconds"] = 1
        with patch.object(self.fixture.service, "_spawn_daemon", return_value=0):
            started = self.start()
        time.sleep(1.05)
        self.fixture.service.resume(started["run_id"])
        self.assertEqual(self.fixture.wait(started["run_id"])["state"], "failed")
        store = self.fixture.store()
        try:
            self.assertEqual(store.attempts_for_run(started["run_id"]), [])
            self.assertEqual(store.outcomes_for_run(started["run_id"])[0]["outcome"]["contributions"], [])
        finally:
            store.close()

    def test_experiment_deadline_also_stops_an_already_running_worker(self):
        fixture = ExperimentRuntimeFixture(self.fixture.root / "active-deadline", workflow="issue-delivery")
        self.addCleanup(fixture.close)
        fixture.spec["budget"]["wall_seconds"] = 5
        before = time.monotonic()
        started = fixture.service.trial_start(
            fixture.spec, "eval-1", "candidate", fixture.task("eval-1", "candidate"), "public-active-deadline",
            _internal_implementation_fixture={"writes": [{"path": "README", "content": "fixed eval-1\n"}], "delay_seconds": 10},
            _internal_review_fixture={"verdict": "clean", "summary": "Active deadline fixture.", "findings": []},
        )
        fixture.runs[("eval-1", "candidate")] = started["run_id"]
        self.assertEqual(fixture.wait(started["run_id"])["state"], "failed")
        self.assertLess(time.monotonic() - before, 8, "worker must not run for its separate 120-second task budget")
        store = fixture.store()
        try:
            attempts = store.attempts_for_run(started["run_id"])
            self.assertEqual(len(attempts), 1)
            self.assertIsNotNone(attempts[0]["pid"], "this must exercise active work, not a prelaunch failure")
            final = store.outcomes_for_run(started["run_id"])[0]["outcome"]
            self.assertEqual([c["result"] for c in final["contributions"]], ["failed"])
        finally:
            store.close()

    def test_public_controller_rejects_delivery_reviewer_and_unbounded_requests(self):
        task = self.fixture.task("eval-1", "control")
        task.update(workflow="issue-delivery")
        task["scope"]["write_paths"] = ["README"]
        with self.assertRaisesRegex(ContractError, "frozen review"):
            self.fixture.service.trial_start(self.fixture.spec, "eval-1", "control", task, "invalid-delivery-reviewer")
        experiment = copy.deepcopy(self.fixture.spec)
        experiment["budget"]["wall_seconds"] = 3601
        with self.assertRaisesRegex(ContractError, "bounded controller"):
            self.fixture.service.trial_start(experiment, "eval-1", "control", self.fixture.task("eval-1", "control"), "unbounded-trial")

    def test_public_outcomes_evaluate_qualify_promote_new_run_and_roll_back(self):
        service = self.fixture.service
        service.profile_binding_bootstrap({"template": lifecycle_fixtures.lifecycle_template(update_mode="reviewed"),
                                           "profile": self.fixture.profiles["control"], "version": 7})
        self.fixture.run_all()
        self.assertEqual(service.learning_report(self.fixture.repo)["sample_size"], 4)
        evaluation = service.policy_evaluate(self.fixture.spec)
        self.assertTrue(evaluation["eligibility"]["eligible"])
        helper = lifecycle_fixtures.ProfileLifecycleTest()
        helper.candidate = self.fixture.profiles["candidate"]
        qualification = helper.qualification(evaluation)
        self.assertEqual(service.profile_qualification_add(qualification)["gate_failures"], [])
        promoted = service.profile_binding_change(helper.promotion("public-chain-promote"))
        self.assertEqual(promoted["receipt"]["to"]["binding_version"], 8)
        # Normal automatic routing of a NEW run observes the promoted alias;
        # completed experimental runs retain their exact prelaunch bindings.
        task = self.fixture.task("hold-1", "candidate")
        task["routing"].pop("overrides")
        with patch.object(service, "_spawn_daemon", return_value=0):
            automatic = service.start(task, "public-promoted-new-run",
                                      _internal_review_fixture={"verdict": "clean", "summary": "New-run binding verification.", "findings": []})
        try:
            store = self.fixture.store()
            try:
                snapshot = json.loads(store.run(automatic["run_id"])["mutable_snapshot"])
                self.assertEqual(snapshot["routing"]["roles"]["reviewer"]["selected"]["profile_id"], "profile-b")
                self.assertEqual(snapshot["routing"]["roles"]["reviewer"]["selected"]["binding"]["version"], 8)
                old = json.loads(store.run(self.fixture.runs[("eval-1", "control")])["mutable_snapshot"])
                self.assertEqual(old["routing"]["roles"]["reviewer"]["selected"]["profile_id"], "profile-a")
            finally:
                store.close()
        finally:
            service.cancel(automatic["run_id"])
        regression = ExperimentRuntimeFixture(self.fixture.root / "regression", service=service, repo=self.fixture.repo,
                                              experiment_id="public-post-promotion-regression", candidate_succeeds=False)
        self.addCleanup(regression.close)
        regression.run_all()
        evaluated = service.policy_evaluate(regression.spec)
        self.assertEqual(evaluated["evaluation"]["verdict"], "no_change")
        rollback = {"schema_version": 1, "decision_id": "public-chain-rollback", "action": "rollback", "alias": "review.deep",
                    "expected_binding_version": 8, "qualification_id": None,
                    "rollback_target": {"profile_id": "profile-a", "binding_version": 7},
                    "experiment_id": regression.spec["experiment_id"], "evaluation_sha256": evaluated["evaluation_sha256"],
                    "actor": "human", "reason": "Predeclared evaluation and held-out regression favor the prior incumbent.",
                    "evidence_refs": ["evaluation.json"]}
        reverted = service.profile_binding_change(rollback)
        self.assertEqual(reverted["receipt"]["to"]["binding_version"], 9)
        self.assertEqual(service.profile_binding_status("review.deep")["binding"]["profile_id"], "profile-a")


if __name__ == "__main__":
    unittest.main()
