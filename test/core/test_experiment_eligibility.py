"""New lifecycle authority is bound to current public saved-run evidence."""

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from experiment_runtime_fixture import ExperimentRuntimeFixture
import test_learning as learning_fixtures
from test_learning import experimental_final
import test_lifecycle as lifecycle_fixtures
from devsquad.catalog import update_last_good
from devsquad.contracts import ContractError
from devsquad.learning import evaluate_experiment
from devsquad.service import Service
from devsquad.store import ConflictError, Store, canonical_json


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

    def correct(self, fixture=None, *, arm="candidate"):
        fixture = fixture or self.fixture
        correction = experimental_final(f"escaped-held-out-{fixture.spec['experiment_id']}-{arm}", "succeeded")
        correction.update(
            kind="late_correction", verdict="escaped_defect",
            corrects_outcome_id=fixture.outcome_id("hold-1", arm),
            observed_at=datetime.now(timezone.utc).isoformat(),
            summary="A real later escaped-defect observation invalidates eligibility.",
        )
        fixture.service.outcome_add(fixture.runs[("hold-1", arm)], correction)

    def test_unmeasured_ratios_cannot_bypass_finite_qualification_gates(self):
        template = copy.deepcopy(self.template)
        template["template_id"] = "finite-measurement-template"
        template["gate"].update(max_latency_ratio=2.0, max_usage_ratio=2.0)
        self.store.register_profile_template(template)
        qualification = copy.deepcopy(self.qualification)
        qualification["template_id"] = template["template_id"]
        for fields in (("latency_ratio",), ("usage_ratio",), ("latency_ratio", "usage_ratio")):
            with self.subTest(invented=fields):
                changed = copy.deepcopy(qualification)
                for field in fields:
                    changed["measured"][field] = 0.0
                with self.assertRaisesRegex(ContractError, "measurements|unmeasured"):
                    self.store.record_profile_qualification(changed)
        with self.assertRaisesRegex(ContractError, "latency_ratio_missing.*usage_ratio_missing"):
            self.store.record_profile_qualification(qualification)

    def test_live_outcomes_require_current_clock_not_module_import_time(self):
        # A full suite may spend more than the permitted skew before this
        # module's first lifecycle case. Keep the real clock fence strict.
        with self.assertRaisesRegex(ContractError, "invalid_saved_run_evidence"):
            self.store.record_profile_qualification(
                self.qualification, now=datetime.now(timezone.utc) - timedelta(minutes=10),
            )
        self.assertEqual(self.store.record_profile_qualification(self.qualification)["gate_failures"], [])

    def next_fixture(self, experiment_id, *, profiles=None, candidate_succeeds=False):
        fixture = ExperimentRuntimeFixture(
            self.fixture.root / experiment_id, service=self.fixture.service, repo=self.fixture.repo,
            experiment_id=experiment_id, profiles=profiles, candidate_succeeds=candidate_succeeds,
        )
        self.addCleanup(fixture.close)
        fixture.run_all()
        return fixture, fixture.service.policy_evaluate(fixture.spec)

    @staticmethod
    def rollback(evaluation, *, target="profile-a", target_version=7, expected=8):
        return {
            "schema_version": 1, "decision_id": "regression-rollback",
            "action": "rollback", "alias": "review.deep", "expected_binding_version": expected,
            "qualification_id": None,
            "rollback_target": {"profile_id": target, "binding_version": target_version},
            "experiment_id": evaluation["experiment"]["experiment_id"],
            "evaluation_sha256": evaluation["evaluation_sha256"], "actor": "human",
            "reason": "The predeclared held-out regression requires rollback.",
            "evidence_refs": ["evaluation.json"],
        }

    def test_stale_regression_cannot_roll_back_until_explicit_current_review(self):
        self.store.record_profile_qualification(self.qualification)
        self.store.change_profile_binding(lifecycle_fixtures.ProfileLifecycleTest.promotion("initial-promotion"))
        fixture, evaluation = self.next_fixture("post-promotion-regression")
        self.assertEqual(evaluation["evaluation"]["verdict"], "no_change")
        self.correct(fixture, arm="control")
        with self.assertRaisesRegex(ContractError, "current|stale"):
            self.store.change_profile_binding(self.rollback(evaluation))
        self.assertEqual(self.store.profile_binding("review.deep")["version"], 8)
        reviewed = fixture.service.policy_evaluate(
            fixture.spec, revision_id="regression-review",
            previous_evaluation_sha256=evaluation["evaluation_sha256"],
        )
        request = self.rollback(reviewed)
        first = self.store.change_profile_binding(request)
        self.assertEqual(first["receipt"]["to"]["binding_version"], 9)
        self.assertEqual(self.store.change_profile_binding(request), {**first, "replayed": True})

    def test_stale_qualified_target_blocks_regression_and_is_skipped_by_catalog_fallback(self):
        self.store.record_profile_qualification(self.qualification)
        self.store.change_profile_binding(lifecycle_fixtures.ProfileLifecycleTest.promotion("promote-b"))
        profiles = {
            "control": self.fixture.profiles["candidate"],
            "candidate": lifecycle_fixtures.profile("profile-c", "model-c"),
        }
        _, evaluation = self.next_fixture("qualify-c", profiles=profiles, candidate_succeeds=True)
        helper = lifecycle_fixtures.ProfileLifecycleTest()
        helper.candidate = profiles["candidate"]
        qualification = helper.qualification(evaluation, qualification_id="qualification-c")
        self.store.record_profile_qualification(qualification)
        request = lifecycle_fixtures.ProfileLifecycleTest.promotion("promote-c", expected=8)
        request["qualification_id"] = "qualification-c"
        self.store.change_profile_binding(request)
        self.correct()
        _, regression = self.next_fixture("c-regression", profiles=profiles)
        with self.assertRaisesRegex(ContractError, "current|stale"):
            self.store.change_profile_binding(self.rollback(regression, target="profile-b", target_version=8, expected=9))
        self.assertEqual(self.store.profile_binding("review.deep")["version"], 9)
        all_profiles = [self.fixture.profiles["control"], *profiles.values()]
        for available, should_block in ((["model-b"], True), (["model-a", "model-b"], False)):
            catalog_path = self.fixture.root / ("blocked-catalog.json" if should_block else "safe-catalog.json")
            update_last_good(catalog_path, harness="fixture", version="1", complete=True,
                             models=[{"id": value["model_id"]} for value in all_profiles], profiles=all_profiles)
            change = update_last_good(catalog_path, harness="fixture", version="1", complete=True,
                                      models=[{"id": value} for value in available], profiles=all_profiles)["catalog_change"]
            fallback = {
                "schema_version": 1, "decision_id": "catalog-blocked" if should_block else "catalog-safe",
                "action": "rollback", "alias": "review.deep", "expected_binding_version": 9,
                "catalog_change": change, "actor": "human", "reason": "The incumbent was removed.",
                "evidence_refs": ["catalog.json"],
            }
            if should_block:
                with self.assertRaisesRegex(ContractError, "no available qualified predecessor"):
                    self.store.fallback_unavailable_profile_binding(fallback)
                self.assertEqual(self.store.profile_binding("review.deep")["version"], 9)
            else:
                result = self.store.fallback_unavailable_profile_binding(fallback)
                self.assertEqual(result["receipt"]["to"]["profile_id"], "profile-a")
                self.assertEqual(result["receipt"]["to"]["binding_version"], 10)

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

    def test_correction_cannot_commit_between_qualification_validation_and_commit(self):
        attempted, completed = threading.Event(), threading.Event()
        failures = []
        original = self.store._qualification_evidence

        def correction():
            attempted.set()
            try:
                self.correct()
            except Exception as exc:
                failures.append(exc)
            finally:
                completed.set()

        worker = threading.Thread(target=correction)

        def checked(*args, **kwargs):
            result = original(*args, **kwargs)
            self.assertTrue(self.store.connection.in_transaction)
            worker.start()
            self.assertTrue(attempted.wait(2))
            # The other connection's write cannot commit while this validated
            # qualification transaction still holds its SQLite writer fence.
            self.assertFalse(completed.wait(0.1))
            return result

        try:
            with patch.object(self.store, "_qualification_evidence", side_effect=checked):
                qualification = self.store.record_profile_qualification(self.qualification)
            self.assertTrue(qualification["eligibility"]["eligible"])
        finally:
            if worker.ident is not None:
                worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(completed.is_set())
        with self.assertRaisesRegex(ContractError, "current|stale"):
            self.store.record_profile_qualification(self.qualification)
        with self.assertRaisesRegex(ContractError, "current|stale"):
            self.store.change_profile_binding(lifecycle_fixtures.ProfileLifecycleTest.promotion("raced-promotion"))
        self.assertEqual(self.store.profile_binding("review.deep")["version"], 7)

    def test_proposal_shows_stale_evidence_without_rewriting_saved_evaluation(self):
        current = self.fixture.service.learning_propose(str(self.fixture.repo))["proposal"]
        self.assertEqual(current["verdict"], "promotion_proposal")
        self.correct()
        stale = self.fixture.service.learning_propose(str(self.fixture.repo))["proposal"]
        self.assertEqual(stale["verdict"], "no_change")
        self.assertIn("saved_evaluation_stale_evidence_changed", stale["reasons"])
        self.assertEqual(stale["evidence"]["experiment"]["evaluation_sha256"], self.evaluation["evaluation_sha256"])
        self.assertFalse(stale["evidence"]["experiment"]["eligibility"]["eligible"])

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
        for arguments in (
            {"revision_id": "missing-predecessor"},
            {"previous_evaluation_sha256": revision["evaluation_sha256"]},
            {"revision_id": "bad-predecessor", "previous_evaluation_sha256": "not-a-hash"},
            {"revision_id": "review-after-escape", "previous_evaluation_sha256": "0" * 64},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ContractError):
                self.fixture.service.policy_evaluate(self.fixture.spec, **arguments)
        self.store.connection.execute(
            "UPDATE experiment_evaluation_revisions SET previous_evaluation_sha256=? WHERE revision_id=?",
            ("0" * 64, "review-after-escape"),
        )
        try:
            with self.assertRaisesRegex(ContractError, "predecessor"):
                self.fixture.service.policy_evaluate(self.fixture.spec)
        finally:
            self.store.connection.execute(
                "UPDATE experiment_evaluation_revisions SET previous_evaluation_sha256=? WHERE revision_id=?",
                (self.evaluation["evaluation_sha256"], "review-after-escape"),
            )
        with self.assertRaises(ConflictError):
            self.fixture.service.policy_evaluate(
                self.fixture.spec, revision_id="stale-predecessor-review",
                previous_evaluation_sha256=self.evaluation["evaluation_sha256"],
            )
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM experiment_evaluation_revisions").fetchone()[0], 1)

    def test_fresh_reviewed_revision_can_qualify_and_pins_new_decision(self):
        correction = experimental_final("reviewed-followup", "succeeded")
        correction.update(
            kind="late_correction", verdict="corrected", corrects_outcome_id="candidate-hold-1",
            observed_at=datetime.now(timezone.utc).isoformat(),
            summary="A bounded follow-up review recorded corrected evidence without an escaped defect.",
        )
        self.fixture.service.outcome_add(self.fixture.runs[("hold-1", "candidate")], correction)
        with self.assertRaisesRegex(ContractError, "current|stale"):
            self.store.record_profile_qualification(self.qualification)
        reviewed = self.fixture.service.policy_evaluate(
            self.fixture.spec, revision_id="reviewed-followup",
            previous_evaluation_sha256=self.evaluation["evaluation_sha256"],
        )
        self.assertEqual(reviewed["evaluation"]["verdict"], "promotion_proposal")
        qualification = copy.deepcopy(self.qualification)
        qualification.update(qualification_id="qualification-reviewed", evaluation_sha256=reviewed["evaluation_sha256"])
        result = self.store.record_profile_qualification(qualification)
        self.assertTrue(result["eligibility"]["eligible"])
        request = lifecycle_fixtures.ProfileLifecycleTest.promotion("reviewed-promotion")
        request["qualification_id"] = qualification["qualification_id"]
        promoted = self.store.change_profile_binding(request)
        self.assertEqual(promoted["receipt"]["qualification"]["evaluation_sha256"], reviewed["evaluation_sha256"])
        self.assertEqual(promoted["receipt"]["to"]["binding_version"], 8)


class HistoricalExperimentEligibilityTest(unittest.TestCase):
    def test_upgraded_reused_outcome_history_remains_readable_but_cannot_authorize_new_decisions(self):
        temporary = tempfile.TemporaryDirectory(prefix="devsquad-historical-eligibility-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        repo = root / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        runtime = root / "runtime"
        runtime.mkdir()
        database = runtime / "state.sqlite3"
        connection = sqlite3.connect(database)
        migrations = Path(__file__).resolve().parents[2] / "plugin/core/src/devsquad/migrations"
        for path in sorted(migrations.glob("*.sql")):
            version = int(path.name.split("_", 1)[0])
            if version <= 13:
                connection.executescript(path.read_text())
                connection.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(?, 'historical')", (version,))
        spec = learning_fixtures.experiment(repo)
        chains = {}
        for case in spec["cases"]:
            for arm, verdict in (("control", "failed"), ("candidate", "succeeded")):
                chains[case[f"{arm}_outcome_id"]] = {
                    "final": experimental_final(case[f"{arm}_outcome_id"], verdict), "late_corrections": [],
                }
        evaluation = evaluate_experiment(spec, chains, evaluated_at=learning_fixtures.NOW.isoformat())
        # Explicit historical negative: the old bug could count two reused
        # outcomes as three pairs. Never fabricate new successful runtime runs.
        for case in spec["cases"]:
            case.update(control_outcome_id="reused-control", candidate_outcome_id="reused-candidate")
        spec_json = canonical_json(spec)
        spec_sha256 = hashlib.sha256(spec_json.encode()).hexdigest()
        evaluation["spec_sha256"] = spec_sha256
        evaluation_json = canonical_json(evaluation)
        evaluation_sha256 = hashlib.sha256(evaluation_json.encode()).hexdigest()
        connection.execute(
            "INSERT INTO experiments(experiment_id,project_path,spec_json,spec_sha256,evaluation_json,evaluation_sha256,verdict,recorded_at) VALUES(?,?,?,?,?,?,?,?)",
            (spec["experiment_id"], str(repo), spec_json, spec_sha256, evaluation_json, evaluation_sha256,
             evaluation["verdict"], evaluation["evaluated_at"]),
        )
        connection.commit()
        connection.close()
        service = Service(runtime)
        store = Store(database, runtime / "artifacts")
        self.addCleanup(store.close)
        original = dict(store.connection.execute("SELECT * FROM experiments").fetchone())
        self.assertEqual(store.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 15)
        report = service.learning_report(str(repo))
        self.assertEqual(report["sample_size"], 0)
        proposal = service.learning_propose(str(repo))["proposal"]
        self.assertEqual(proposal["verdict"], "no_change")
        self.assertIn("legacy_unverified_evidence", proposal["reasons"])
        self.assertFalse(proposal["decision"]["review_required"])
        self.assertEqual(proposal["evidence"]["experiment"]["evaluation_sha256"], evaluation_sha256)
        self.assertEqual(dict(store.connection.execute("SELECT * FROM experiments").fetchone()), original)
        helper = lifecycle_fixtures.ProfileLifecycleTest()
        helper.candidate = lifecycle_fixtures.profile("profile-b", "model-b")
        qualification = helper.qualification({"experiment": spec, "evaluation_sha256": evaluation_sha256})
        store.bootstrap_profile_binding(lifecycle_fixtures.lifecycle_template(), lifecycle_fixtures.profile("profile-a", "model-a"), version=7)
        with self.assertRaisesRegex(ContractError, "legacy_unverified_evidence"):
            store.record_profile_qualification(qualification)
        # Import an old qualification as historical test data, then prove its
        # old qualified label cannot bypass the repaired new-decision gate.
        store._insert_concrete_profile(helper.candidate, evaluation["evaluated_at"])
        payload = canonical_json(qualification)
        store.connection.execute(
            "INSERT INTO qualification_runs(qualification_id,alias,template_id,profile_id,experiment_id,evaluation_sha256,verdict,payload_json,payload_sha256,gate_failures_json,recorded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (qualification["qualification_id"], qualification["alias"], qualification["template_id"], helper.candidate["id"],
             spec["experiment_id"], evaluation_sha256, "qualified", payload, hashlib.sha256(payload.encode()).hexdigest(), "[]", evaluation["evaluated_at"]),
        )
        with self.assertRaisesRegex(ContractError, "legacy_unverified_evidence"):
            store.change_profile_binding(lifecycle_fixtures.ProfileLifecycleTest.promotion("unsafe-history-promotion"))
        self.assertEqual(store.profile_binding("review.deep")["version"], 7)
        self.assertEqual(store.profile_binding_decisions("review.deep"), [])


if __name__ == "__main__":
    unittest.main()
