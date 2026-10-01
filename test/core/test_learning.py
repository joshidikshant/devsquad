import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.contracts import ContractError
from devsquad.learning import (
    build_comparison_report,
    build_learning_proposal,
    evaluate_experiment,
    render_learning_proposal_markdown,
    validate_experiment,
    validate_outcome,
)
from devsquad.store import ConflictError, Store, canonical_json


NOW = datetime(2026, 9, 27, 16, 0, tzinfo=timezone.utc)


def final_outcome():
    return {
        "schema_version": 1,
        "outcome_id": "outcome-final",
        "kind": "final",
        "verdict": "succeeded",
        "selection_mode": "automatic",
        "observed_at": NOW.isoformat(),
        "corrects_outcome_id": None,
        "summary": "A repair attempt produced the accepted result.",
        "criteria": [{
            "criterion_id": "checks",
            "status": "passed",
            "evidence_refs": ["receipt.json#criteria/checks"],
        }],
        "contributions": [
            {
                "attempt_id": "attempt-original",
                "role": "implementer",
                "result": "failed",
                "independent_success": False,
                "evidence_refs": ["attempt-original.json"],
            },
            {
                "attempt_id": "attempt-repair",
                "role": "implementer",
                "result": "repair",
                "independent_success": False,
                "evidence_refs": ["attempt-repair.json"],
            },
        ],
        "lead_repairs": [],
        "evidence_refs": ["receipt.json"],
    }


def experiment(project_path):
    return {
        "schema_version": 1,
        "experiment_id": "experiment-profile-b",
        "project_path": str(project_path),
        "question": "Does profile B improve successful outcomes?",
        "hypothesis": "Profile B is non-inferior and improves paired success.",
        "evidence_availability": "tracked_fixture",
        "variable": {
            "kind": "profile_binding",
            "alias": "review.deep",
            "control_profile_id": "profile-a",
            "candidate_profile_id": "profile-b",
        },
        "cases": [
            {
                "case_id": "eval-1", "split": "evaluation",
                "control_outcome_id": "control-eval-1",
                "candidate_outcome_id": "candidate-eval-1",
            },
            {
                "case_id": "eval-2", "split": "evaluation",
                "control_outcome_id": "control-eval-2",
                "candidate_outcome_id": "candidate-eval-2",
            },
            {
                "case_id": "hold-1", "split": "held_out",
                "control_outcome_id": "control-hold-1",
                "candidate_outcome_id": "candidate-hold-1",
            },
        ],
        "gate": {
            "min_evaluation_pairs": 2,
            "min_held_out_pairs": 1,
            "noninferiority_margin": 0.0,
            "minimum_success_gain": 0.5,
            "max_candidate_escaped_defects": 0,
        },
        "budget": {
            "max_cases": 3,
            "max_worker_invocations": 0,
            "wall_seconds": 60,
        },
        "rollback_target": {"profile_id": "profile-a", "binding_version": 7},
    }


def experimental_final(outcome_id, verdict):
    value = final_outcome()
    value.update({
        "outcome_id": outcome_id,
        "verdict": verdict,
        "selection_mode": "experimental",
        "summary": f"Experimental fixture {outcome_id} was {verdict}.",
        "criteria": [],
        "contributions": [],
    })
    return value


class LearningContractTest(unittest.TestCase):
    def test_outcome_contract_rejects_false_success_and_mutation(self):
        normalized = validate_outcome(final_outcome(), now=NOW)
        self.assertEqual(normalized["verdict"], "succeeded")
        changed = final_outcome()
        changed["criteria"][0]["status"] = "failed"
        with self.assertRaisesRegex(ContractError, "must all pass"):
            validate_outcome(changed, now=NOW)
        changed = final_outcome()
        changed["contributions"][0]["independent_success"] = True
        with self.assertRaisesRegex(ContractError, "only a successful"):
            validate_outcome(changed, now=NOW)
        changed = final_outcome()
        changed["extra"] = True
        with self.assertRaisesRegex(ContractError, "fields invalid"):
            validate_outcome(changed, now=NOW)
        changed = final_outcome()
        changed["observed_at"] = (NOW + timedelta(minutes=6)).isoformat()
        with self.assertRaisesRegex(ContractError, "clock skew"):
            validate_outcome(changed, now=NOW)

    def test_experiment_gate_promotes_only_complete_held_out_evidence(self):
        spec = experiment(Path("/tmp/experiment-project"))
        validate_experiment(spec)
        chains = {}
        for case in spec["cases"]:
            chains[case["control_outcome_id"]] = {
                "final": experimental_final(case["control_outcome_id"], "failed"),
                "late_corrections": [],
            }
            chains[case["candidate_outcome_id"]] = {
                "final": experimental_final(case["candidate_outcome_id"], "succeeded"),
                "late_corrections": [],
            }
        promoted = evaluate_experiment(spec, chains, evaluated_at=NOW.isoformat())
        self.assertEqual(promoted["verdict"], "promotion_proposal")
        self.assertFalse(promoted["active_policy_changed"])
        self.assertEqual(
            promoted["rollback_target"],
            {"profile_id": "profile-a", "binding_version": 7},
        )

        missing = dict(chains)
        del missing["candidate-hold-1"]
        no_change = evaluate_experiment(spec, missing, evaluated_at=NOW.isoformat())
        self.assertEqual(no_change["verdict"], "no_change")
        self.assertIn("insufficient_held_out_pairs", no_change["reasons"])

        escaped = copy.deepcopy(chains)
        escaped["candidate-hold-1"]["late_corrections"] = [{
            "verdict": "escaped_defect",
        }]
        no_change = evaluate_experiment(spec, escaped, evaluated_at=NOW.isoformat())
        self.assertEqual(no_change["verdict"], "no_change")
        self.assertIn(
            "candidate_escaped_defect_limit_exceeded", no_change["reasons"],
        )

        invalid = experiment(Path("/tmp/experiment-project"))
        invalid["rollback_target"]["profile_id"] = "profile-b"
        with self.assertRaisesRegex(ContractError, "control profile"):
            validate_experiment(invalid)
        invalid = experiment(Path("/tmp/experiment-project"))
        invalid["evidence_availability"] = []
        with self.assertRaisesRegex(ContractError, "evidence_availability"):
            validate_experiment(invalid)
        invalid = experiment(Path("/tmp/experiment-project"))
        invalid["cases"][0]["split"] = []
        with self.assertRaisesRegex(ContractError, "case split"):
            validate_experiment(invalid)
        invalid_chains = copy.deepcopy(chains)
        invalid_chains["candidate-hold-1"]["late_corrections"] = [None]
        with self.assertRaisesRegex(ContractError, "late corrections"):
            evaluate_experiment(spec, invalid_chains, evaluated_at=NOW.isoformat())

    def test_experiment_evaluation_is_persisted_and_replay_safe(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            repository = path / "repo"
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.name", "Test"],
                check=True,
            )
            (repository / "README").write_text("fixture\n")
            subprocess.run(["git", "-C", str(repository), "add", "README"], check=True)
            subprocess.run(["git", "-C", str(repository), "commit", "-qm", "base"], check=True)
            store = Store(path / "state.sqlite3", path / "artifacts")
            self.addCleanup(store.close)
            spec = experiment(repository)
            for case in spec["cases"]:
                for arm, verdict in (("control", "failed"), ("candidate", "succeeded")):
                    outcome_id = case[f"{arm}_outcome_id"]
                    claim = store.claim_start(
                        repository, f"run-{outcome_id}", {}, "owner",
                    )
                    store.connection.execute(
                        "UPDATE runs SET state=?,phase=NULL WHERE id=?",
                        (verdict, claim.run_id),
                    )
                    store.record_outcome(
                        claim.run_id,
                        experimental_final(outcome_id, verdict),
                        now=NOW,
                    )
            first = store.evaluate_learning_experiment(spec, now=NOW)
            replay = store.evaluate_learning_experiment(spec, now=NOW)
            self.assertEqual(first["evaluation"]["verdict"], "promotion_proposal")
            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            changed = copy.deepcopy(spec)
            changed["hypothesis"] = "Mutated after evaluation."
            with self.assertRaisesRegex(ConflictError, "different specification"):
                store.evaluate_learning_experiment(changed, now=NOW)
            inputs = store.learning_proposal_inputs(repository, now=NOW)
            self.assertEqual(
                inputs["experiment"]["experiment"]["experiment_id"],
                spec["experiment_id"],
            )

    def test_learning_proposal_is_traceable_and_never_changes_policy(self):
        project_path = "/tmp/experiment-project"
        report = build_comparison_report(
            project_id=None,
            project_path=project_path,
            terminal_runs=[],
            outcome_records=[],
            attempt_profiles={},
            generated_at=NOW.isoformat(),
        )
        no_evidence = build_learning_proposal(
            report, None, generated_at=NOW.isoformat(),
        )
        self.assertEqual(no_evidence["verdict"], "no_change")
        self.assertFalse(no_evidence["active_policy_changed"])
        self.assertEqual(no_evidence["reasons"], ["no_evaluated_experiment"])
        self.assertEqual(
            no_evidence["decision"],
            {"action": "retain_current_policy", "review_required": False},
        )

        spec = experiment(Path(project_path))
        chains = {}
        for case in spec["cases"]:
            chains[case["control_outcome_id"]] = {
                "final": experimental_final(case["control_outcome_id"], "failed"),
                "late_corrections": [],
            }
            chains[case["candidate_outcome_id"]] = {
                "final": experimental_final(case["candidate_outcome_id"], "succeeded"),
                "late_corrections": [],
            }
        evaluation = evaluate_experiment(
            spec, chains, evaluated_at=NOW.isoformat(),
        )
        spec_sha256 = hashlib.sha256(
            canonical_json(validate_experiment(spec)).encode(),
        ).hexdigest()
        evaluation_sha256 = hashlib.sha256(
            canonical_json(evaluation).encode(),
        ).hexdigest()
        record = {
            "experiment": spec,
            "spec_sha256": spec_sha256,
            "evaluation": evaluation,
            "evaluation_sha256": evaluation_sha256,
            "recorded_at": NOW.isoformat(),
        }
        proposal = build_learning_proposal(
            report, record, generated_at=NOW.isoformat(),
        )
        self.assertEqual(proposal["verdict"], "promotion_proposal")
        self.assertFalse(proposal["active_policy_changed"])
        self.assertEqual(proposal["rollback_target"]["profile_id"], "profile-a")
        self.assertEqual(
            proposal["evidence"]["experiment"]["evaluation_sha256"],
            evaluation_sha256,
        )
        self.assertIn(proposal["proposal_id"], render_learning_proposal_markdown(proposal))
        tampered = copy.deepcopy(record)
        tampered["evaluation_sha256"] = "0" * 64
        with self.assertRaisesRegex(ContractError, "evidence hash"):
            build_learning_proposal(report, tampered, generated_at=NOW.isoformat())

    def test_final_and_late_outcomes_are_append_only_and_attempt_bound(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            repository = path / "repo"
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.name", "Test"],
                check=True,
            )
            (repository / "README").write_text("fixture\n")
            subprocess.run(["git", "-C", str(repository), "add", "README"], check=True)
            subprocess.run(["git", "-C", str(repository), "commit", "-qm", "base"], check=True)
            store = Store(path / "state.sqlite3", path / "artifacts")
            self.addCleanup(store.close)
            claim = store.claim_start(repository, "outcome-run", {}, "owner")
            run = store.run(claim.run_id)
            store.connection.execute(
                "UPDATE runs SET state='succeeded',phase=NULL WHERE id=?",
                (claim.run_id,),
            )
            for attempt_id, profile_id, metadata in (
                ("attempt-original", "profile-original", {"failure": {"error": "seeded"}}),
                ("attempt-repair", "profile-repair", {}),
            ):
                store.connection.execute(
                    "INSERT INTO attempts(id,run_id,project_id,worktree_path,attempt_token,"
                    "status,heartbeat_at,package_digest,output_metadata,created_at,finished_at,"
                    "role,profile_id) VALUES(?,?,?,?,?,'finished',?,?,?,?,?,'implementer',?)",
                    (
                        attempt_id, claim.run_id, run["project_id"], str(repository),
                        f"token-{attempt_id}", NOW.isoformat(), "package",
                        json.dumps(metadata), NOW.isoformat(), NOW.isoformat(), profile_id,
                    ),
                )

            false_credit = final_outcome()
            false_credit["contributions"][0]["result"] = "successful"
            false_credit["contributions"][0]["independent_success"] = True
            with self.assertRaisesRegex(ConflictError, "failed attempt"):
                store.record_outcome(claim.run_id, false_credit, now=NOW)

            first = store.record_outcome(claim.run_id, final_outcome(), now=NOW)
            replay = store.record_outcome(claim.run_id, final_outcome(), now=NOW)
            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            changed = copy.deepcopy(final_outcome())
            changed["summary"] = "Changed after persistence."
            with self.assertRaisesRegex(ConflictError, "different evidence"):
                store.record_outcome(claim.run_id, changed, now=NOW)

            late = {
                "schema_version": 1,
                "outcome_id": "outcome-late-defect",
                "kind": "late_correction",
                "verdict": "escaped_defect",
                "selection_mode": "automatic",
                "observed_at": (NOW + timedelta(minutes=1)).isoformat(),
                "corrects_outcome_id": "outcome-final",
                "summary": "A defect escaped the original acceptance evidence.",
                "criteria": [{
                    "criterion_id": "checks", "status": "failed",
                    "evidence_refs": ["late-defect.json"],
                }],
                "contributions": [],
                "lead_repairs": [],
                "evidence_refs": ["late-defect.json"],
            }
            predating = copy.deepcopy(late)
            predating["observed_at"] = (NOW - timedelta(seconds=1)).isoformat()
            with self.assertRaisesRegex(ConflictError, "predates"):
                store.record_outcome(claim.run_id, predating, now=NOW)
            store.record_outcome(
                claim.run_id, late, now=NOW + timedelta(minutes=1),
            )
            history = store.outcomes_for_run(claim.run_id)
            self.assertEqual(
                [item["outcome"]["verdict"] for item in history],
                ["succeeded", "escaped_defect"],
            )
            self.assertFalse(
                history[0]["outcome"]["contributions"][0]["independent_success"],
            )
            missing = store.claim_start(repository, "missing-outcome", {}, "owner")
            store.connection.execute(
                "UPDATE runs SET state='failed',phase=NULL WHERE id=?",
                (missing.run_id,),
            )
            report = store.learning_report(
                repository, now=NOW + timedelta(minutes=2),
            )
            self.assertEqual(report["sample_size"], 1)
            self.assertEqual(report["terminal_run_count"], 2)
            self.assertEqual(report["final_successes"], 1)
            self.assertEqual(report["escaped_defects"], 1)
            self.assertEqual(
                report["selection_modes"]["automatic"]["success_rate"], 1.0,
            )
            self.assertEqual(report["profiles"]["profile-original"]["failed"], 1)
            self.assertEqual(report["profiles"]["profile-repair"]["repairs"], 1)
            self.assertEqual(
                report["missingness"]["terminal_runs_without_final_outcome"], 1,
            )
            self.assertTrue(
                report["interpretation"]["final_task_success_is_not_profile_success"],
            )

    def test_current_schema_contains_outcome_ledger(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            store = Store(path / "state.sqlite3", path / "artifacts")
            self.addCleanup(store.close)
            self.assertEqual(
                store.connection.execute(
                    "SELECT MAX(version) FROM schema_migrations",
                ).fetchone()[0],
                14,
            )
            columns = {
                row[1] for row in store.connection.execute("PRAGMA table_info(outcomes)")
            }
            self.assertTrue({"outcome_id", "payload_sha256", "corrects_outcome_id"} <= columns)
            experiment_columns = {
                row[1]
                for row in store.connection.execute("PRAGMA table_info(experiments)")
            }
            self.assertTrue({
                "experiment_id", "spec_sha256", "evaluation_sha256", "verdict",
            } <= experiment_columns)


if __name__ == "__main__":
    unittest.main()
