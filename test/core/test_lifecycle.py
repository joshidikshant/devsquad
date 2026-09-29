import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.contracts import ContractError
from devsquad.lifecycle import (
    guarded_change_violation,
    profile_template_violation,
    validate_profile_template,
)
from devsquad.router import load_routing
from devsquad.store import ConflictError, Store


NOW = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)


def profile(profile_id, model_id, *, tools=None, pool="pool-a", billing="subscription"):
    return {
        "id": profile_id,
        "harness": "fixture",
        "model_family": "fixture-family",
        "model_id": model_id,
        "effort": {"value": "high", "transport": "native"},
        "required_tools": list(tools or ["read"]),
        "permission_policy": "read_only",
        "account_pool_id": pool,
        "billing_mode": billing,
        "quality_status": "proven",
        "evidence_refs": ["tracked-fixture"],
    }


def lifecycle_template(*, update_mode="guarded_auto"):
    return {
        "schema_version": 1,
        "template_id": "template-review-deep-v1",
        "alias": "review.deep",
        "update_mode": update_mode,
        "policy": {"id": "fixture-policy", "version": 3},
        "allowed_harnesses": ["fixture"],
        "allowed_model_families": ["fixture-family"],
        "allowed_account_pools": ["pool-a"],
        "allowed_task_classes": ["fixture-review-small"],
        "permission_policy": "read_only",
        "allowed_tools": ["read", "web"],
        "allowed_billing_modes": ["subscription"],
        "gate": {
            "min_evaluation_pairs": 1,
            "min_held_out_pairs": 1,
            "max_critical_defects": 0,
            "max_latency_ratio": None,
            "max_usage_ratio": None,
        },
    }


def final_outcome(outcome_id, verdict):
    return {
        "schema_version": 1,
        "outcome_id": outcome_id,
        "kind": "final",
        "verdict": verdict,
        "selection_mode": "experimental",
        "observed_at": NOW.isoformat(),
        "corrects_outcome_id": None,
        "summary": f"Lifecycle fixture {outcome_id} was {verdict}.",
        "criteria": [],
        "contributions": [],
        "lead_repairs": [],
        "evidence_refs": [f"{outcome_id}.json"],
    }


def routing_policy():
    return {
        "schema_version": 1,
        "id": "fixture-policy",
        "version": 3,
        "roles": {"reviewer": [{"kind": "alias", "id": "review.deep"}]},
        "task_classes": {"fixture-review-small": "proven"},
        "require_different_model_for_review": True,
        "prefer_different_harness_for_review": False,
        "account_pools": {
            "pool-a": {
                "allowed_billing_modes": ["subscription"],
                "max_concurrency": 2,
                "unknown_capacity_policy": "allow_bounded",
            },
        },
        "experiment_budget": {},
    }


def review_task(repo, *, pinned_profile_id=None):
    routing = {
        "profiles_file": "profiles.json",
        "policy_file": "policy.json",
    }
    if pinned_profile_id is not None:
        routing["overrides"] = {
            "reviewer": {"profile_id": pinned_profile_id, "fallback": "none"},
        }
    return {
        "schema_version": 1,
        "project": {
            "repo_path": str(repo), "base_ref": "HEAD", "target_ref": "HEAD",
        },
        "workflow": "branch-review",
        "goal": "Verify lifecycle routing.",
        "task_class": "fixture-review-small",
        "acceptance": [{
            "id": "routing", "description": "The expected profile is frozen.",
            "evidence_kind": "review",
        }],
        "checks": [],
        "scope": {"read_paths": ["README"], "write_paths": []},
        "lead": {"mode": "host"},
        "routing": routing,
        "budget": {
            "wall_seconds": 60, "max_worker_invocations": 1,
            "max_revisions": 0, "max_fallbacks_per_step": 0,
        },
        "origin": {"surface": "test"},
    }


class ProfileLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devsquad-lifecycle-")
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.name", "Test"],
            check=True,
        )
        (self.repo / "README").write_text("fixture\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "README"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "base"], check=True)
        self.database = self.root / "state.sqlite3"
        self.artifacts = self.root / "artifacts"
        self.store = Store(self.database, self.artifacts)
        self.incumbent = profile("profile-a", "model-a")
        self.candidate = profile("profile-b", "model-b")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def seed_experiment(self):
        cases = [
            {
                "case_id": "eval-1", "split": "evaluation",
                "control_outcome_id": "control-eval",
                "candidate_outcome_id": "candidate-eval",
            },
            {
                "case_id": "hold-1", "split": "held_out",
                "control_outcome_id": "control-hold",
                "candidate_outcome_id": "candidate-hold",
            },
        ]
        for case in cases:
            for arm, verdict in (("control", "failed"), ("candidate", "succeeded")):
                outcome_id = case[f"{arm}_outcome_id"]
                claim = self.store.claim_start(
                    self.repo, f"run-{outcome_id}", {}, "owner",
                )
                self.store.connection.execute(
                    "UPDATE runs SET state=?,phase=NULL WHERE id=?",
                    (verdict, claim.run_id),
                )
                self.store.record_outcome(
                    claim.run_id, final_outcome(outcome_id, verdict), now=NOW,
                )
        spec = {
            "schema_version": 1,
            "experiment_id": "experiment-profile-b",
            "project_path": str(self.repo),
            "question": "Should profile B replace profile A?",
            "hypothesis": "Profile B improves held-out success.",
            "evidence_availability": "tracked_fixture",
            "variable": {
                "kind": "profile_binding",
                "alias": "review.deep",
                "control_profile_id": "profile-a",
                "candidate_profile_id": "profile-b",
            },
            "cases": cases,
            "gate": {
                "min_evaluation_pairs": 1,
                "min_held_out_pairs": 1,
                "noninferiority_margin": 0.0,
                "minimum_success_gain": 1.0,
                "max_candidate_escaped_defects": 0,
            },
            "budget": {
                "max_cases": 2,
                "max_worker_invocations": 0,
                "wall_seconds": 60,
            },
            "rollback_target": {"profile_id": "profile-a", "binding_version": 7},
        }
        return self.store.evaluate_learning_experiment(spec, now=NOW)

    def qualification(self, evaluation, *, qualification_id="qualification-b"):
        return {
            "schema_version": 1,
            "qualification_id": qualification_id,
            "alias": "review.deep",
            "template_id": "template-review-deep-v1",
            "candidate_profile": self.candidate,
            "task_class": "fixture-review-small",
            "experiment_id": evaluation["experiment"]["experiment_id"],
            "evaluation_sha256": evaluation["evaluation_sha256"],
            "source": {
                "harness_version": "fixture 1.0",
                "catalog_sha256": "a" * 64,
                "model_revision": None,
            },
            "budget": {
                "max_cases": 2,
                "used_cases": 2,
                "max_worker_invocations": 0,
                "worker_invocations": 0,
                "max_wall_seconds": 60,
                "wall_seconds": 5,
            },
            "measured": {
                "evaluation_pairs": 1,
                "held_out_pairs": 1,
                "critical_defects": 0,
                "latency_ratio": None,
                "usage_ratio": None,
            },
            "verdict": "qualified",
            "evidence_refs": ["experiment-profile-b", "evaluation.json"],
        }

    @staticmethod
    def promotion(decision_id, *, actor="human", expected=7):
        return {
            "schema_version": 1,
            "decision_id": decision_id,
            "action": "promote",
            "alias": "review.deep",
            "expected_binding_version": expected,
            "qualification_id": "qualification-b",
            "rollback_target": None,
            "actor": actor,
            "reason": "Held-out evidence passed the reviewed gate.",
            "evidence_refs": ["experiment-profile-b", "evaluation.json"],
        }

    def test_template_and_guarded_authority_boundaries_are_strict(self):
        template = validate_profile_template(lifecycle_template())
        self.assertIsNone(profile_template_violation(self.candidate, template))
        paid = profile("paid", "model-paid", billing="paid_api")
        self.assertEqual(
            profile_template_violation(paid, template),
            "billing_mode_not_allowed",
        )
        expanded = profile("expanded", "model-expanded", tools=["read", "web"])
        self.assertEqual(
            guarded_change_violation(self.incumbent, expanded),
            "guarded_tool_expansion",
        )
        invalid = lifecycle_template()
        invalid["gate"]["min_held_out_pairs"] = 0
        with self.assertRaisesRegex(ContractError, "min_held_out_pairs"):
            validate_profile_template(invalid)

    def test_qualification_cas_promotion_and_rollback_are_replay_safe(self):
        template = lifecycle_template()
        registered = self.store.register_profile_template(template, now=NOW)
        replayed = self.store.register_profile_template(template, now=NOW)
        self.assertFalse(registered["replayed"])
        self.assertTrue(replayed["replayed"])
        baseline = self.store.bootstrap_profile_binding(
            template, self.incumbent, version=7, now=NOW,
        )
        self.assertFalse(baseline["replayed"])
        self.assertTrue(self.store.bootstrap_profile_binding(
            template, self.incumbent, version=7, now=NOW,
        )["replayed"])
        registry = {
            "schema_version": 1,
            "profiles": [self.incumbent],
            "bindings": {
                "review.deep": {"profile_id": "profile-a", "version": 7},
            },
        }
        profiles_payload = json.dumps(registry, sort_keys=True) + "\n"
        policy_payload = json.dumps(routing_policy(), sort_keys=True) + "\n"
        frozen_input = self.store.effective_profile_registry(
            profiles_payload, policy_payload,
        )
        frozen_routing = load_routing(
            review_task(self.repo), frozen_input["profiles_payload"], policy_payload,
        )
        self.assertEqual(
            frozen_routing["roles"]["reviewer"]["selected"]["profile_id"],
            "profile-a",
        )
        evaluation = self.seed_experiment()
        qualified = self.store.record_profile_qualification(
            self.qualification(evaluation), now=NOW,
        )
        self.assertEqual(qualified["gate_failures"], [])
        self.assertTrue(self.store.record_profile_qualification(
            self.qualification(evaluation), now=NOW,
        )["replayed"])

        barrier = threading.Barrier(2)
        results = []

        def promote(decision_id):
            connection = Store(self.database, self.artifacts)
            try:
                barrier.wait(timeout=10)
                results.append(connection.change_profile_binding(
                    self.promotion(decision_id), now=NOW,
                ))
            except Exception as exc:
                results.append(exc)
            finally:
                connection.close()

        threads = [
            threading.Thread(target=promote, args=(decision_id,))
            for decision_id in ("decision-promote-a", "decision-promote-b")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        successes = [item for item in results if isinstance(item, dict)]
        conflicts = [item for item in results if isinstance(item, ConflictError)]
        self.assertEqual((len(successes), len(conflicts)), (1, 1), results)
        receipt = successes[0]["receipt"]
        self.assertEqual(receipt["to"]["binding_version"], 8)
        self.assertTrue(receipt["affects_new_runs_only"])
        self.assertEqual(self.store.profile_binding("review.deep")["profile_id"], "profile-b")
        promoted_input = self.store.effective_profile_registry(
            profiles_payload, policy_payload,
        )
        promoted_routing = load_routing(
            review_task(self.repo), promoted_input["profiles_payload"], policy_payload,
        )
        self.assertEqual(
            promoted_routing["roles"]["reviewer"]["selected"]["profile_id"],
            "profile-b",
        )
        self.assertEqual(
            frozen_routing["roles"]["reviewer"]["selected"]["profile_id"],
            "profile-a",
        )
        pinned = load_routing(
            review_task(self.repo, pinned_profile_id="profile-a"),
            promoted_input["profiles_payload"], policy_payload,
        )
        self.assertEqual(
            pinned["roles"]["reviewer"]["selected"]["profile_id"],
            "profile-a",
        )
        winning_request = self.promotion(receipt["decision_id"])
        self.assertTrue(self.store.change_profile_binding(
            winning_request, now=NOW,
        )["replayed"])

        rollback = {
            "schema_version": 1,
            "decision_id": "decision-rollback-a",
            "action": "rollback",
            "alias": "review.deep",
            "expected_binding_version": 8,
            "qualification_id": None,
            "rollback_target": {"profile_id": "profile-a", "binding_version": 7},
            "actor": "guarded_auto",
            "reason": "Held-out regression requires the qualified predecessor.",
            "evidence_refs": ["regression.json"],
        }
        rolled_back = self.store.change_profile_binding(rollback, now=NOW)
        self.assertEqual(rolled_back["receipt"]["to"]["binding_version"], 9)
        self.assertEqual(self.store.profile_binding("review.deep")["profile_id"], "profile-a")
        self.assertEqual(len(self.store.profile_binding_decisions("review.deep")), 2)
        rollback_input = self.store.effective_profile_registry(
            profiles_payload, policy_payload,
        )
        rollback_routing = load_routing(
            review_task(self.repo), rollback_input["profiles_payload"], policy_payload,
        )
        self.assertEqual(
            rollback_routing["roles"]["reviewer"]["selected"]["binding"]["version"],
            9,
        )

    def test_insufficient_evidence_and_disabled_guarded_auto_cannot_promote(self):
        reviewed = lifecycle_template(update_mode="reviewed")
        self.store.bootstrap_profile_binding(
            reviewed, self.incumbent, version=7, now=NOW,
        )
        incomplete = self.qualification({
            "experiment": {"experiment_id": "unused"},
            "evaluation_sha256": "0" * 64,
        }, qualification_id="qualification-incomplete")
        incomplete.update({
            "experiment_id": None,
            "evaluation_sha256": None,
            "verdict": "incomplete",
            "evidence_refs": [],
        })
        saved = self.store.record_profile_qualification(incomplete, now=NOW)
        self.assertIn("experiment_evidence_missing", saved["gate_failures"])
        with self.assertRaisesRegex(ContractError, "qualified candidate"):
            self.store.change_profile_binding(
                self.promotion("decision-insufficient"), now=NOW,
            )

        evaluation = self.seed_experiment()
        self.store.record_profile_qualification(
            self.qualification(evaluation), now=NOW,
        )
        with self.assertRaisesRegex(ContractError, "not enabled"):
            self.store.change_profile_binding(
                self.promotion("decision-auto", actor="guarded_auto"), now=NOW,
            )

    def test_schema_twelve_contains_lifecycle_ledger(self):
        version = self.store.connection.execute(
            "SELECT MAX(version) FROM schema_migrations",
        ).fetchone()[0]
        self.assertEqual(version, 12)
        tables = {
            row[0] for row in self.store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            )
        }
        self.assertTrue({
            "profile_templates", "concrete_profiles", "qualification_runs",
            "profile_bindings", "profile_binding_versions", "binding_decisions",
        } <= tables)


if __name__ == "__main__":
    unittest.main()
