import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.contracts import (
    CapabilityUnavailable,
    ContractError,
    PolicyDenied,
    ProfileUnsupported,
)
from devsquad.router import load_routing, resolve_routing
from devsquad.validation import validate_policy, validate_profile_registry


def profile(
    profile_id,
    *,
    harness="codex",
    family="family-a",
    model=None,
    permission="read_only",
    pool="pool-a",
    billing="subscription",
    quality="proven",
):
    return {
        "id": profile_id,
        "harness": harness,
        "model_family": family,
        "model_id": model or f"model-{profile_id}",
        "effort": {"value": "low", "transport": "native"},
        "required_tools": ["read"],
        "permission_policy": permission,
        "account_pool_id": pool,
        "billing_mode": billing,
        "quality_status": quality,
        "evidence_refs": [f"evidence-{profile_id}"],
    }


def pool(*, modes=None, concurrency=2, unknown="allow_bounded"):
    return {
        "allowed_billing_modes": modes or ["subscription"],
        "max_concurrency": concurrency,
        "unknown_capacity_policy": unknown,
    }


class RouterTest(unittest.TestCase):
    def setUp(self):
        self.task = {
            "schema_version": 1,
            "project": {
                "repo_path": "/tmp/router-fixture",
                "base_ref": "base",
                "target_ref": "target",
            },
            "workflow": "branch-review",
            "goal": "Review the frozen change.",
            "task_class": "fixture-review",
            "acceptance": [{
                "id": "review",
                "description": "Return a bound review.",
                "evidence_kind": "review",
            }],
            "checks": [],
            "scope": {"read_paths": ["src"], "write_paths": []},
            "lead": {"mode": "host"},
            "routing": {
                "profiles_file": "devsquad/profiles.json",
                "policy_file": "devsquad/policy.json",
            },
            "budget": {
                "wall_seconds": 300,
                "max_worker_invocations": 3,
                "max_revisions": 0,
                "max_fallbacks_per_step": 1,
            },
            "origin": {"surface": "test"},
        }
        self.registry = {
            "schema_version": 1,
            "profiles": [
                profile("review-a"),
                profile("review-b", harness="claude", family="family-b"),
            ],
            "bindings": {
                "review.deep": {"profile_id": "review-b", "version": 7},
            },
        }
        self.policy = {
            "schema_version": 1,
            "id": "fixture-policy",
            "version": 3,
            "roles": {
                "reviewer": [
                    {"kind": "alias", "id": "review.deep"},
                    {"kind": "profile", "id": "review-a"},
                ],
            },
            "task_classes": {"fixture-review": "proven"},
            "require_different_model_for_review": True,
            "prefer_different_harness_for_review": True,
            "account_pools": {"pool-a": pool()},
            "experiment_budget": {},
        }

    def test_alias_selection_is_deterministic_frozen_and_explained(self):
        first = resolve_routing(self.task, self.registry, self.policy)
        second = resolve_routing(self.task, self.registry, self.policy)
        self.assertEqual(first, second)
        reviewer = first["roles"]["reviewer"]
        self.assertEqual(reviewer["source"], "automatic")
        self.assertEqual(reviewer["selected"]["profile_id"], "review-b")
        self.assertEqual(
            reviewer["selected"]["binding"],
            {"alias": "review.deep", "version": 7, "profile_id": "review-b"},
        )
        self.assertEqual(
            [candidate["profile_id"] for candidate in reviewer["fallbacks"]],
            ["review-a"],
        )
        self.assertEqual(first["capacity"]["pool-a"]["status"], "unknown")
        self.assertEqual(
            first["capacity"]["pool-a"]["unknown_capacity_policy"],
            "allow_bounded",
        )

        self.registry["profiles"][1]["model_id"] = "mutated-after-selection"
        self.registry["bindings"]["review.deep"]["version"] = 8
        self.assertNotEqual(
            reviewer["selected"]["profile"]["model_id"],
            "mutated-after-selection",
        )
        self.assertEqual(reviewer["selected"]["binding"]["version"], 7)

    def test_one_role_pin_leaves_headless_lead_automatic(self):
        pinned = profile("review-pin", harness="grok", family="family-c")
        lead = profile("lead-a", harness="claude", family="family-b")
        self.registry["profiles"].extend([pinned, lead])
        self.policy["roles"]["lead"] = [{"kind": "profile", "id": "lead-a"}]
        self.task["lead"] = {"mode": "headless"}
        self.task["routing"]["overrides"] = {
            "reviewer": {"profile_id": "review-pin", "fallback": "none"},
        }
        routed = resolve_routing(self.task, self.registry, self.policy)
        self.assertEqual(routed["roles"]["reviewer"]["source"], "override")
        self.assertEqual(
            routed["roles"]["reviewer"]["selected"]["profile_id"], "review-pin",
        )
        self.assertEqual(routed["roles"]["reviewer"]["fallbacks"], [])
        self.assertEqual(routed["roles"]["lead"]["source"], "automatic")
        self.assertEqual(routed["roles"]["lead"]["selected"]["profile_id"], "lead-a")

        self.task["routing"]["overrides"]["implementer"] = {
            "profile_id": "review-pin",
        }
        with self.assertRaisesRegex(ContractError, "not roles in branch-review"):
            resolve_routing(self.task, self.registry, self.policy)

    def test_missing_or_statically_invalid_pin_never_falls_back(self):
        self.task["routing"]["overrides"] = {
            "reviewer": {"profile_id": "missing", "fallback": "policy"},
        }
        with self.assertRaisesRegex(ProfileUnsupported, "does not exist"):
            resolve_routing(self.task, self.registry, self.policy)

        writer = profile("writer", permission="workspace_write")
        self.registry["profiles"].append(writer)
        self.task["routing"]["overrides"]["reviewer"]["profile_id"] = "writer"
        with self.assertRaisesRegex(ProfileUnsupported, "permission_mismatch"):
            resolve_routing(self.task, self.registry, self.policy)

    def test_unavailable_pin_obeys_explicit_fallback_mode(self):
        self.registry["profiles"][1]["account_pool_id"] = "pool-b"
        self.policy["account_pools"]["pool-b"] = pool()
        availability = {
            "pool-a": {"status": "available", "in_flight": 0},
            "pool-b": {"status": "exhausted", "in_flight": 0},
        }
        self.task["routing"]["overrides"] = {
            "reviewer": {"profile_id": "review-b", "fallback": "none"},
        }
        with self.assertRaisesRegex(CapabilityUnavailable, "account_pool_exhausted"):
            resolve_routing(
                self.task, self.registry, self.policy, availability=availability,
            )

        self.task["routing"]["overrides"]["reviewer"]["fallback"] = "policy"
        routed = resolve_routing(
            self.task, self.registry, self.policy, availability=availability,
        )
        reviewer = routed["roles"]["reviewer"]
        self.assertEqual(reviewer["selected"]["profile_id"], "review-a")
        self.assertEqual(reviewer["fallback_mode"], "policy")
        self.assertEqual(reviewer["excluded"][0]["profile_id"], "review-b")
        self.assertEqual(reviewer["excluded"][0]["reason"], "account_pool_exhausted")

    def test_static_policy_filters_are_recorded_without_relaxation(self):
        candidates = [
            profile("suspended", quality="suspended"),
            profile("trial", quality="trial"),
            profile("writer", permission="workspace_write"),
            profile("paid", billing="paid_api"),
            profile("eligible"),
        ]
        self.registry = {"schema_version": 1, "profiles": candidates, "bindings": {}}
        self.policy["roles"]["reviewer"] = [
            {"kind": "profile", "id": candidate["id"]} for candidate in candidates
        ]
        routed = resolve_routing(self.task, self.registry, self.policy)
        reviewer = routed["roles"]["reviewer"]
        self.assertEqual(reviewer["selected"]["profile_id"], "eligible")
        self.assertEqual(
            [item["reason"] for item in reviewer["excluded"]],
            [
                "profile_suspended",
                "quality_below_task_minimum",
                "permission_mismatch",
                "billing_mode_not_allowed",
            ],
        )

    def test_delivery_review_is_different_model_and_prefers_different_harness(self):
        implementer = profile(
            "implementer",
            model="shared-model",
            permission="workspace_write",
        )
        same_model = profile(
            "same-model",
            harness="claude",
            model="shared-model",
        )
        same_harness = profile("same-harness", model="other-codex-model")
        other_harness = profile(
            "other-harness", harness="claude", family="family-b", model="other-model",
        )
        self.registry = {
            "schema_version": 1,
            "profiles": [implementer, same_model, same_harness, other_harness],
            "bindings": {},
        }
        self.policy["roles"] = {
            "implementer": [{"kind": "profile", "id": "implementer"}],
            "reviewer": [
                {"kind": "profile", "id": "same-model"},
                {"kind": "profile", "id": "same-harness"},
                {"kind": "profile", "id": "other-harness"},
            ],
        }
        self.task["workflow"] = "issue-delivery"
        self.task["scope"]["write_paths"] = ["src"]
        routed = resolve_routing(self.task, self.registry, self.policy)
        self.assertEqual(
            routed["roles"]["implementer"]["selected"]["profile_id"],
            "implementer",
        )
        reviewer = routed["roles"]["reviewer"]
        self.assertEqual(reviewer["selected"]["profile_id"], "other-harness")
        reasons = {item["profile_id"]: item["reason"] for item in reviewer["excluded"]}
        self.assertEqual(reasons["same-model"], "review_model_not_independent")
        self.assertEqual(
            [candidate["profile_id"] for candidate in reviewer["fallbacks"]],
            ["same-harness"],
        )

    def test_typed_unknown_and_concurrency_capacity_are_fail_closed(self):
        self.policy["account_pools"]["pool-a"] = pool(unknown="block")
        with self.assertRaisesRegex(CapabilityUnavailable, "currently available"):
            resolve_routing(self.task, self.registry, self.policy)

        self.policy["account_pools"]["pool-a"] = pool(
            concurrency=3, unknown="allow_bounded",
        )
        with self.assertRaisesRegex(CapabilityUnavailable, "currently available"):
            resolve_routing(
                self.task,
                self.registry,
                self.policy,
                availability={"pool-a": {"status": "unknown", "in_flight": 1}},
            )
        routed = resolve_routing(
            self.task,
            self.registry,
            self.policy,
            availability={"pool-a": {"status": "unknown", "in_flight": 0}},
        )
        self.assertEqual(routed["roles"]["reviewer"]["selected"]["profile_id"], "review-b")

        with self.assertRaisesRegex(CapabilityUnavailable, "currently available"):
            resolve_routing(
                self.task,
                self.registry,
                self.policy,
                availability={"pool-a": {"status": "available", "in_flight": 3}},
            )

    def test_policy_missing_task_class_or_required_role_is_denied(self):
        del self.policy["task_classes"]["fixture-review"]
        with self.assertRaisesRegex(PolicyDenied, "does not authorize task class"):
            resolve_routing(self.task, self.registry, self.policy)
        self.policy["task_classes"]["fixture-review"] = "proven"
        del self.policy["roles"]["reviewer"]
        with self.assertRaisesRegex(PolicyDenied, "required roles"):
            resolve_routing(self.task, self.registry, self.policy)

    def test_loader_rejects_duplicate_keys_nan_and_hashes_exact_bytes(self):
        profiles_bytes = (json.dumps(self.registry, indent=2) + "\n").encode()
        policy_bytes = (json.dumps(self.policy, separators=(",", ":")) + "\n").encode()
        routed = load_routing(self.task, profiles_bytes, policy_bytes)
        self.assertEqual(
            routed["profile_registry"]["sha256"], hashlib.sha256(profiles_bytes).hexdigest(),
        )
        self.assertEqual(
            routed["policy"]["sha256"], hashlib.sha256(policy_bytes).hexdigest(),
        )
        with self.assertRaisesRegex(ContractError, "duplicate key"):
            load_routing(
                self.task,
                b'{"schema_version":1,"schema_version":1,"profiles":[],"bindings":{}}',
                policy_bytes,
            )
        with self.assertRaisesRegex(ContractError, "non-finite"):
            load_routing(
                self.task,
                b'{"schema_version":1,"profiles":[],"bindings":{},"bad":NaN}',
                policy_bytes,
            )
        with self.assertRaisesRegex(ContractError, "UTF-8 JSON"):
            load_routing(self.task, b"\xff", policy_bytes)

    def test_registry_and_account_pool_shapes_are_strict(self):
        duplicate = copy.deepcopy(self.registry)
        duplicate["profiles"].append(copy.deepcopy(duplicate["profiles"][0]))
        with self.assertRaisesRegex(ContractError, "ids must be unique"):
            validate_profile_registry(duplicate)
        missing_target = copy.deepcopy(self.registry)
        missing_target["bindings"]["review.deep"]["profile_id"] = "missing"
        with self.assertRaisesRegex(ContractError, "target does not exist"):
            validate_profile_registry(missing_target)

        invalid_policy = copy.deepcopy(self.policy)
        invalid_policy["account_pools"]["pool-a"]["extra"] = True
        with self.assertRaisesRegex(ContractError, "account pool policy fields invalid"):
            validate_policy(invalid_policy)
        invalid_policy = copy.deepcopy(self.policy)
        invalid_policy["account_pools"]["pool-a"]["max_concurrency"] = True
        with self.assertRaisesRegex(ContractError, "max_concurrency"):
            validate_policy(invalid_policy)


if __name__ == "__main__":
    unittest.main()
