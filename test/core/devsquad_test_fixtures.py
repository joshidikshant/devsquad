import json


def branch_review_routing_documents():
    profiles = {
        "schema_version": 1,
        "profiles": [{
            "id": "fixture-reviewer",
            "harness": "fixture",
            "model_family": "fixture-family-a",
            "model_id": "fixture-review-model",
            "effort": {"value": "low", "transport": "native"},
            "required_tools": ["read"],
            "permission_policy": "read_only",
            "account_pool_id": "fixture-subscription",
            "billing_mode": "subscription",
            "quality_status": "proven",
            "evidence_refs": ["tracked-fixture"],
        }],
        "bindings": {
            "review.deep": {"profile_id": "fixture-reviewer", "version": 1},
        },
    }
    policy = {
        "schema_version": 1,
        "id": "fixture-policy",
        "version": 1,
        "roles": {"reviewer": [{"kind": "alias", "id": "review.deep"}]},
        "task_classes": {"fixture-review-small": "proven"},
        "require_different_model_for_review": True,
        "prefer_different_harness_for_review": True,
        "account_pools": {
            "fixture-subscription": {
                "allowed_billing_modes": ["subscription"],
                "max_concurrency": 1,
                "unknown_capacity_policy": "allow_bounded",
            },
        },
        "experiment_budget": {},
    }
    return (
        json.dumps(profiles, sort_keys=True) + "\n",
        json.dumps(policy, sort_keys=True) + "\n",
    )
