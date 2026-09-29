import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))
sys.path.insert(0, str(ROOT / "test/core/fakes"))

from decision_adapter import FakeDecisionAdapter
from devsquad.contracts import ContractError
from devsquad.decision import (
    apply_decision_response,
    build_decision_request,
    decision_cache_key,
    decision_fallback,
    validate_decision_policy,
    validate_decision_response,
)
from devsquad.router import resolve_routing
from devsquad.validation import validate_policy


def profile(profile_id, *, permission="read_only", quality="proven"):
    return {
        "id": profile_id,
        "harness": "fixture",
        "model_family": "fixture-family",
        "model_id": f"model-{profile_id}",
        "effort": {"value": "high", "transport": "native"},
        "required_tools": ["read"],
        "permission_policy": permission,
        "account_pool_id": "fixture-pool",
        "billing_mode": "subscription",
        "quality_status": quality,
        "evidence_refs": [f"evidence-{profile_id}"],
    }


def decision_config(mode="shadow"):
    return {
        "schema_version": 1,
        "mode": mode,
        "purpose": {
            "id": "profile-ranking",
            "version": 1,
            "question_sha256": "a" * 64,
            "rubric_sha256": "b" * 64,
        },
        "adapter": {
            "id": "fixture",
            "model": "fixture-v1",
            "runtime_revision": "fixture-runtime-1",
            "calibration_version": None,
        },
        "language": "en",
        "min_confidence": 0.7,
        "gate_evidence_sha256": "c" * 64 if mode == "advisory" else None,
        "budget": {
            "max_calls": 1,
            "max_input_bytes": 4096,
            "wall_seconds": 2,
            "max_cost_usd": 0.01,
        },
    }


class DecisionHelperTest(unittest.TestCase):
    def setUp(self):
        self.task = {
            "schema_version": 1,
            "project": {
                "repo_path": "/tmp/decision-fixture",
                "base_ref": "base",
                "target_ref": "target",
            },
            "workflow": "branch-review",
            "goal": "Review the bounded change.",
            "task_class": "fixture-review",
            "acceptance": [{
                "id": "review", "description": "Return a bound review.",
                "evidence_kind": "review",
            }],
            "checks": [],
            "scope": {"read_paths": ["src"], "write_paths": []},
            "lead": {"mode": "host"},
            "routing": {
                "profiles_file": "profiles.json", "policy_file": "policy.json",
            },
            "budget": {
                "wall_seconds": 60, "max_worker_invocations": 2,
                "max_revisions": 0, "max_fallbacks_per_step": 1,
            },
            "origin": {"surface": "test"},
        }
        self.registry = {
            "schema_version": 1,
            "profiles": [
                profile("review-a"), profile("review-b"),
                profile("writer", permission="workspace_write"),
                profile("trial", quality="trial"),
            ],
            "bindings": {},
        }
        self.policy = {
            "schema_version": 1,
            "id": "fixture-policy",
            "version": 1,
            "roles": {"reviewer": [
                {"kind": "profile", "id": "review-a"},
                {"kind": "profile", "id": "writer"},
                {"kind": "profile", "id": "trial"},
                {"kind": "profile", "id": "review-b"},
            ]},
            "task_classes": {"fixture-review": "proven"},
            "require_different_model_for_review": False,
            "prefer_different_harness_for_review": False,
            "account_pools": {"fixture-pool": {
                "allowed_billing_modes": ["subscription"],
                "max_concurrency": 2,
                "unknown_capacity_policy": "allow_bounded",
            }},
            "experiment_budget": {},
        }
        self.routing = resolve_routing(self.task, self.registry, self.policy)

    def request(self, mode="shadow", payload="bounded evidence"):
        policy = {**self.policy, "decision_helper": decision_config(mode)}
        return policy, build_decision_request(
            self.task, self.routing, policy, payload,
        )

    def test_default_off_is_exactly_equivalent_and_does_not_build_a_request(self):
        validate_policy(self.policy)
        self.assertEqual(
            validate_decision_policy(None), {"schema_version": 1, "mode": "off"},
        )
        self.assertIsNone(build_decision_request(
            self.task, self.routing, self.policy, "ignored",
        ))
        off = apply_decision_response(
            self.routing,
            {},
            {},
            {"schema_version": 1, "mode": "off"},
        )
        self.assertEqual(off, self.routing)
        self.assertNotIn("decision_helper", off)

    def test_shadow_records_a_valid_suggestion_without_changing_routes(self):
        policy, request = self.request("shadow")
        adapter = FakeDecisionAdapter({"reviewer": ["review-b", "review-a"]})
        response = adapter.decide(request)
        routed = apply_decision_response(
            self.routing, request, response, policy["decision_helper"],
        )
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(routed["roles"], self.routing["roles"])
        self.assertEqual(
            routed["decision_helper"]["role_status"],
            {"reviewer": "shadow_mode"},
        )
        self.assertEqual(routed["decision_helper"]["applied_roles"], [])

    def test_advisory_can_only_reorder_the_eligible_set(self):
        policy, request = self.request("advisory")
        self.assertEqual(request["candidates"], {
            "reviewer": ["review-a", "review-b"],
        })
        response = FakeDecisionAdapter({
            "reviewer": ["review-b", "review-a"],
        }).decide(request)
        routed = apply_decision_response(
            self.routing, request, response, policy["decision_helper"],
        )
        reviewer = routed["roles"]["reviewer"]
        self.assertEqual(reviewer["selected"]["profile_id"], "review-b")
        self.assertEqual(
            [item["profile_id"] for item in reviewer["fallbacks"]], ["review-a"],
        )
        self.assertEqual(
            {item["profile_id"] for item in reviewer["excluded"]},
            {"writer", "trial"},
        )

        attacked = copy.deepcopy(response)
        attacked["recommendations"]["reviewer"]["ranking"][0] = "writer"
        with self.assertRaisesRegex(ContractError, "eligible set"):
            validate_decision_response(
                request, attacked, policy["decision_helper"],
            )

    def test_pin_unknown_ids_nan_and_identity_drift_fail_closed(self):
        self.task["routing"]["overrides"] = {
            "reviewer": {"profile_id": "review-a", "fallback": "policy"},
        }
        pinned_routing = resolve_routing(self.task, self.registry, self.policy)
        policy = {**self.policy, "decision_helper": decision_config("advisory")}
        request = build_decision_request(
            self.task, pinned_routing, policy, "bounded evidence",
        )
        response = FakeDecisionAdapter().decide(request)
        routed = apply_decision_response(
            pinned_routing, request, response, policy["decision_helper"],
        )
        self.assertEqual(
            routed["roles"]["reviewer"]["selected"]["profile_id"], "review-a",
        )
        self.assertEqual(
            routed["decision_helper"]["role_status"]["reviewer"], "pinned_route",
        )

        malformed = copy.deepcopy(response)
        malformed["recommendations"]["reviewer"]["probabilities"]["review-a"] = float("nan")
        with self.assertRaisesRegex(ContractError, "finite probability"):
            validate_decision_response(request, malformed, policy["decision_helper"])
        drifted = copy.deepcopy(response)
        drifted["adapter"]["model"] = "fixture-latest"
        with self.assertRaisesRegex(ContractError, "identity drifted"):
            validate_decision_response(request, drifted, policy["decision_helper"])

    def test_input_drift_changes_cache_key_and_unusable_outputs_preserve_order(self):
        policy, request_a = self.request("advisory", "evidence A")
        _, request_b = self.request("advisory", "evidence B")
        self.assertNotEqual(decision_cache_key(request_a), decision_cache_key(request_b))
        response = FakeDecisionAdapter(
            {"reviewer": ["review-b", "review-a"]}, confidence=0.4,
        ).decide(request_a)
        routed = apply_decision_response(
            self.routing, request_a, response, policy["decision_helper"],
        )
        self.assertEqual(routed["roles"], self.routing["roles"])
        self.assertEqual(
            routed["decision_helper"]["role_status"]["reviewer"],
            "below_confidence_gate",
        )
        fallback = decision_fallback(
            self.routing, "advisory", "invalid_response",
            hashlib.sha256(json.dumps(request_a, sort_keys=True).encode()).hexdigest(),
        )
        self.assertEqual(fallback["roles"], self.routing["roles"])
        self.assertEqual(fallback["decision_helper"]["applied_roles"], [])

    def test_advisory_requires_gate_and_budget_or_truncation_cannot_be_hidden(self):
        invalid = decision_config("advisory")
        invalid["gate_evidence_sha256"] = None
        with self.assertRaisesRegex(ContractError, "reviewed gate"):
            validate_decision_policy(invalid)
        policy = {**self.policy, "decision_helper": decision_config("shadow")}
        with self.assertRaisesRegex(ContractError, "byte budget"):
            build_decision_request(
                self.task, self.routing, policy, "x" * 4097,
            )
        request = build_decision_request(
            self.task, self.routing, policy, "x" * 4097, truncated=True,
        )
        response = FakeDecisionAdapter().decide(request)
        routed = apply_decision_response(
            self.routing, request, response, policy["decision_helper"],
        )
        self.assertEqual(routed["roles"], self.routing["roles"])
        self.assertEqual(
            routed["decision_helper"]["role_status"]["reviewer"], "shadow_mode",
        )

    def test_frozen_baseline_uses_only_the_synthetic_labeled_corpus(self):
        experiment_dir = ROOT / "docs/plans/engineering-team/experiments"
        baseline = json.loads(
            (experiment_dir / "decision-helper-baseline-v1.json").read_text(),
        )
        self.assertEqual(set(baseline), {
            "schema_version", "experiment_id", "status", "purpose", "corpus",
            "split", "modes", "resource_ceiling", "acceptance", "adoption",
        })
        corpus_path = experiment_dir / baseline["corpus"]["path"]
        self.assertEqual(
            hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
            baseline["corpus"]["sha256"],
        )
        corpus = json.loads(corpus_path.read_text())
        case_ids = [case["id"] for case in corpus["cases"]]
        self.assertEqual(case_ids, baseline["corpus"]["case_ids"])
        self.assertEqual(
            set(baseline["split"]["mechanics"] + baseline["split"]["held_out"]),
            set(case_ids),
        )
        self.assertFalse(baseline["corpus"]["contains_private_content"])
        self.assertEqual(baseline["resource_ceiling"]["network_calls_in_baseline"], 0)
        self.assertFalse(baseline["adoption"]["advisory_authorized_by_baseline"])
        self.assertEqual(baseline["adoption"]["runtime_default"], "off")


if __name__ == "__main__":
    unittest.main()
