"""New lifecycle authority is bound to current public saved-run evidence."""

import copy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from experiment_runtime_fixture import ExperimentRuntimeFixture
from test_learning import experimental_final
import test_lifecycle as lifecycle_fixtures
from devsquad.contracts import ContractError
from devsquad.store import ConflictError


class ExperimentEligibilityTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="devsquad-eligibility-")
        self.addCleanup(self.temporary.cleanup)
        self.fixture = ExperimentRuntimeFixture(Path(self.temporary.name))
        self.addCleanup(self.fixture.close)
        self.fixture.run_all()
        self.evaluation = self.fixture.service.policy_evaluate(self.fixture.spec)
        self.store = self.fixture.store()
        self.addCleanup(self.store.close)
        self.template = lifecycle_fixtures.lifecycle_template()
        self.store.bootstrap_profile_binding(self.template, self.fixture.profiles["control"], version=7)
        helper = lifecycle_fixtures.ProfileLifecycleTest()
        helper.candidate = self.fixture.profiles["candidate"]
        self.qualification = helper.qualification(self.evaluation)
        self.qualification["budget"].update(max_worker_invocations=4, worker_invocations=4)

    def correct(self):
        correction = experimental_final("escaped-held-out", "succeeded")
        correction.update(
            kind="late_correction", verdict="escaped_defect",
            corrects_outcome_id="candidate-hold-1",
            observed_at=datetime.now(timezone.utc).isoformat(),
            summary="A real later escaped-defect observation invalidates eligibility.",
        )
        self.fixture.service.outcome_add(self.fixture.runs[("hold-1", "candidate")], correction)

    def test_correction_after_evaluation_blocks_new_qualification_atomically(self):
        self.correct()
        with self.assertRaisesRegex(ContractError, "current|stale|changed"):
            self.store.record_profile_qualification(self.qualification)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM qualification_runs").fetchone()[0], 0)
        self.assertEqual(self.store.profile_binding("review.deep")["version"], 7)

    def test_correction_after_qualification_blocks_replay_and_new_promotion(self):
        self.store.record_profile_qualification(self.qualification)
        self.correct()
        with self.assertRaisesRegex(ContractError, "current|stale|changed"):
            self.store.record_profile_qualification(self.qualification)
        with self.assertRaisesRegex(ContractError, "current|stale|changed"):
            self.store.change_profile_binding(lifecycle_fixtures.ProfileLifecycleTest.promotion("stale-promotion"))
        self.assertEqual(self.store.profile_binding("review.deep")["version"], 7)
        self.assertEqual(self.store.profile_binding_decisions("review.deep"), [])

    def test_completed_decision_replay_preserves_historical_receipt_after_correction(self):
        self.store.record_profile_qualification(self.qualification)
        request = lifecycle_fixtures.ProfileLifecycleTest.promotion("completed-promotion")
        first = self.store.change_profile_binding(request)
        self.correct()
        replay = self.store.change_profile_binding(request)
        self.assertEqual(replay, {**first, "replayed": True})
        self.assertEqual(self.store.profile_binding("review.deep")["version"], 8)
        self.assertEqual(len(self.store.profile_binding_decisions("review.deep")), 1)

    def test_same_profile_id_with_different_fingerprint_cannot_qualify(self):
        changed = copy.deepcopy(self.qualification)
        changed["candidate_profile"]["required_tools"] = ["read", "web"]
        with self.assertRaisesRegex(ContractError, "fingerprint|candidate"):
            self.store.record_profile_qualification(changed)

    def test_task_class_claim_must_match_actual_frozen_tasks(self):
        template = copy.deepcopy(self.template)
        template["template_id"] = "template-multiple-classes"
        template["allowed_task_classes"].append("untested-task-class")
        self.store.register_profile_template(template)
        changed = copy.deepcopy(self.qualification)
        changed.update(template_id=template["template_id"], task_class="untested-task-class")
        with self.assertRaisesRegex(ContractError, "task.class|context"):
            self.store.record_profile_qualification(changed)

    def test_explicit_revision_keeps_original_bytes_and_reuses_original_assignments(self):
        original = dict(self.store.connection.execute(
            "SELECT * FROM experiments WHERE experiment_id=?", (self.fixture.spec["experiment_id"],),
        ).fetchone())
        self.correct()
        revision = self.fixture.service.policy_evaluate(
            self.fixture.spec, revision_id="review-after-escape",
            previous_evaluation_sha256=self.evaluation["evaluation_sha256"],
        )
        self.assertEqual(revision["evaluation"]["verdict"], "no_change")
        self.assertNotEqual(revision["evaluation_sha256"], self.evaluation["evaluation_sha256"])
        self.assertEqual(revision["revision_id"], "review-after-escape")
        self.assertEqual(revision["previous_evaluation_sha256"], self.evaluation["evaluation_sha256"])
        self.assertTrue(revision["eligibility"]["eligible"])
        replay = self.fixture.service.policy_evaluate(
            self.fixture.spec, revision_id="review-after-escape",
            previous_evaluation_sha256=self.evaluation["evaluation_sha256"],
        )
        self.assertEqual(replay, {**revision, "replayed": True})
        saved = dict(self.store.connection.execute(
            "SELECT * FROM experiments WHERE experiment_id=?", (self.fixture.spec["experiment_id"],),
        ).fetchone())
        self.assertEqual(saved, original)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM experiment_assignments").fetchone()[0], 4)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM experiment_evaluation_revisions").fetchone()[0], 1)
        with self.assertRaises(ConflictError):
            self.fixture.service.policy_evaluate(
                self.fixture.spec, revision_id="stale-predecessor-review",
                previous_evaluation_sha256=self.evaluation["evaluation_sha256"],
            )
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM experiment_evaluation_revisions").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
