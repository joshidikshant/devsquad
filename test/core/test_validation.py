from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "plugin" / "core"
sys.path.insert(0, str(CORE / "src"))

from devsquad.contracts import ContractError, ExecutionIdentity, LaunchSpec, NormalizedResult, validate_launch_payload
from devsquad.validation import validate_policy, validate_profile, validate_task


class AdversarialValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[2]
        self.task = json.loads((root / "docs/plans/engineering-team/examples/issue-delivery.json").read_text())
        self.identity = ExecutionIdentity("codex", None, "openai", "gpt", "gpt-test", "low", ("read",), "read_only", "pool", "verified")

    def assert_contract_error(self, fn, *args):
        with self.assertRaises(ContractError):
            fn(*args)

    def test_launch_rejects_bool_nan_string_argv_and_environment_abuse(self):
        base = LaunchSpec(1, "codex", "cli_exec", ("codex",), "/tmp", None, 3, self.identity).to_dict()
        for key, bad in (("timeout_seconds", True), ("timeout_seconds", math.nan), ("argv", "codex exec")):
            value = copy.deepcopy(base); value[key] = bad
            self.assert_contract_error(validate_launch_payload, value)
        for env in ({"PATH": "/tmp"}, {"DEVSQUAD_WORKER": True}):
            value = copy.deepcopy(base); value["environment"] = env
            self.assert_contract_error(validate_launch_payload, value)

    def test_identity_and_result_validate_all_nested_fields(self):
        for change in ({"harness_version": 1}, {"model": ""}, {"tools": ["read", "read"]}, {"account_pool": {}}):
            raw = {**self.identity.__dict__, **change}
            raw["tools"] = tuple(raw["tools"])
            with self.assertRaises(ContractError):
                ExecutionIdentity(**raw)
        self.assert_contract_error(NormalizedResult, 1, "succeeded", None, None, "unknown", "not_evaluated", self.identity, None, {"turn": 1})

    def test_task_rejects_adversarial_nested_types(self):
        mutations = [
            lambda t: t["acceptance"][0].__setitem__("id", {"nested": "id"}),
            lambda t: t["checks"][0].__setitem__("argv", "python -m test"),
            lambda t: t["checks"][0].__setitem__("timeout_seconds", True),
            lambda t: t["budget"].__setitem__("wall_seconds", True),
            lambda t: t["scope"]["read_paths"].append("../escape"),
            lambda t: t["origin"].__setitem__("session_ref", []),
            lambda t: t["acceptance"].append(copy.deepcopy(t["acceptance"][0])),
            lambda t: t["checks"].append(copy.deepcopy(t["checks"][0])),
            lambda t: t.__setitem__(
                "acceptance", [copy.deepcopy(t["acceptance"][0])] * 101,
            ),
            lambda t: t.__setitem__(
                "checks", [copy.deepcopy(t["checks"][0])] * 17,
            ),
            lambda t: t["scope"].__setitem__(
                "read_paths", [f"path-{i}" for i in range(257)],
            ),
        ]
        for mutate in mutations:
            value = copy.deepcopy(self.task); mutate(value)
            self.assert_contract_error(validate_task, value)

    def test_task_accepts_exact_embedded_routing_and_rejects_mixed_sources(self):
        profiles = {
            "schema_version": 1,
            "profiles": [{
                "id": "reviewer", "harness": "codex",
                "model_family": "gpt", "model_id": "gpt-test",
                "effort": {"value": "low", "transport": "native"},
                "required_tools": ["read"],
                "permission_policy": "read_only",
                "account_pool_id": "codex-subscription",
                "billing_mode": "subscription", "quality_status": "trial",
                "evidence_refs": ["managed-entry"],
            }],
            "bindings": {},
        }
        policy = {
            "schema_version": 1, "id": "managed", "version": 1,
            "roles": {"reviewer": [{"kind": "profile", "id": "reviewer"}]},
            "task_classes": {"fixture-bugfix-small": "trial"},
            "require_different_model_for_review": True,
            "account_pools": {
                "codex-subscription": {
                    "allowed_billing_modes": ["subscription"],
                    "max_concurrency": 1,
                },
            },
            "experiment_budget": {},
        }
        embedded = copy.deepcopy(self.task)
        embedded["routing"] = {"profiles": profiles, "policy": policy}
        validate_task(embedded)

        mixed = copy.deepcopy(embedded)
        mixed["routing"]["profiles_file"] = "profiles.json"
        mixed["routing"]["policy_file"] = "policy.json"
        self.assert_contract_error(validate_task, mixed)

        partial = copy.deepcopy(embedded)
        del partial["routing"]["policy"]
        self.assert_contract_error(validate_task, partial)

    def test_profile_and_policy_reject_nested_type_confusion(self):
        profile = {"id":"p","harness":"codex","model_family":"gpt","model_id":"m","effort":{"value":"low","transport":"native"},"required_tools":["read"],"permission_policy":"read_only","account_pool_id":"pool","billing_mode":"subscription","quality_status":"proven","evidence_refs":[]}
        bad = copy.deepcopy(profile); bad["required_tools"] = [""]
        self.assert_contract_error(validate_profile, bad)
        policy = {"schema_version":1,"id":"p","version":1,"roles":{"reviewer":[{"kind":"profile","id":"p"}]},"task_classes":{},"require_different_model_for_review":True,"account_pools":{},"experiment_budget":{}}
        validate_policy(policy)
        for field, value in (("version", True), ("id", {}), ("experiment_budget", {"limit": math.nan})):
            bad = copy.deepcopy(policy); bad[field] = value
            self.assert_contract_error(validate_policy, bad)


if __name__ == "__main__":
    unittest.main()
