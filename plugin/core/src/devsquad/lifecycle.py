"""Strict M6 profile lifecycle contracts and qualification gates."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any

from .contracts import ContractError
from .store import canonical_json
from .validation import validate_profile


TEMPLATE_FIELDS = {
    "schema_version", "template_id", "alias", "update_mode", "policy",
    "allowed_harnesses", "allowed_model_families", "allowed_account_pools",
    "allowed_task_classes", "permission_policy", "allowed_tools",
    "allowed_billing_modes", "gate",
}
QUALIFICATION_FIELDS = {
    "schema_version", "qualification_id", "alias", "template_id",
    "candidate_profile", "task_class", "experiment_id", "evaluation_sha256",
    "source", "budget", "measured", "verdict", "evidence_refs",
}
BINDING_CHANGE_FIELDS = {
    "schema_version", "decision_id", "action", "alias",
    "expected_binding_version", "qualification_id", "rollback_target",
    "actor", "reason", "evidence_refs",
}
SHA256_LENGTH = 64


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ContractError(f"{label} fields are invalid")
    return value


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"lifecycle {field} must be a non-empty string")
    return value


def _strings(value: Any, field: str, *, required: bool = True) -> list[str]:
    if (not isinstance(value, list)
            or (required and not value)
            or any(not isinstance(item, str) or not item for item in value)
            or len(set(value)) != len(value)):
        raise ContractError(f"lifecycle {field} must contain unique strings")
    return list(value)


def _sha256(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if (not isinstance(value, str) or len(value) != SHA256_LENGTH
            or any(character not in "0123456789abcdef" for character in value)):
        raise ContractError(f"lifecycle {field} must be a SHA256 digest")
    return value


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"lifecycle {field} must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"lifecycle {field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"lifecycle {field} must include a timezone")
    return value


def validate_profile_template(value: dict[str, Any]) -> dict[str, Any]:
    """Validate one reviewed, versioned alias qualification policy."""
    _exact(value, TEMPLATE_FIELDS, "profile template")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("profile template schema_version is invalid")
    template_id = _identifier(value["template_id"], "template_id")
    alias = _identifier(value["alias"], "alias")
    if value["update_mode"] not in {"reviewed", "guarded_auto"}:
        raise ContractError("profile template update_mode is invalid")
    policy = _exact(value["policy"], {"id", "version"}, "profile template policy")
    _identifier(policy["id"], "policy.id")
    if type(policy["version"]) is not int or policy["version"] < 1:
        raise ContractError("profile template policy version is invalid")
    permission = value["permission_policy"]
    if permission not in {"read_only", "workspace_write"}:
        raise ContractError("profile template permission policy is invalid")
    billing = _strings(value["allowed_billing_modes"], "allowed_billing_modes")
    if any(mode not in {"subscription", "paid_api"} for mode in billing):
        raise ContractError("profile template billing mode is invalid")
    gate = _exact(value["gate"], {
        "min_evaluation_pairs", "min_held_out_pairs", "max_critical_defects",
        "max_latency_ratio", "max_usage_ratio",
    }, "profile template gate")
    for field in (
        "min_evaluation_pairs", "min_held_out_pairs", "max_critical_defects",
    ):
        if type(gate[field]) is not int or gate[field] < (0 if field.startswith("max_") else 1):
            raise ContractError(f"profile template gate {field} is invalid")
    for field in ("max_latency_ratio", "max_usage_ratio"):
        number = gate[field]
        if (number is not None and (isinstance(number, bool)
                or not isinstance(number, (int, float)) or number <= 0)):
            raise ContractError(f"profile template gate {field} is invalid")
    normalized = {
        **value,
        "template_id": template_id,
        "alias": alias,
        "policy": dict(policy),
        "allowed_harnesses": _strings(
            value["allowed_harnesses"], "allowed_harnesses",
        ),
        "allowed_model_families": _strings(
            value["allowed_model_families"], "allowed_model_families",
        ),
        "allowed_account_pools": _strings(
            value["allowed_account_pools"], "allowed_account_pools",
        ),
        "allowed_task_classes": _strings(
            value["allowed_task_classes"], "allowed_task_classes",
        ),
        "allowed_tools": _strings(
            value["allowed_tools"], "allowed_tools", required=False,
        ),
        "allowed_billing_modes": billing,
        "gate": dict(gate),
    }
    return json.loads(canonical_json(normalized))


def profile_fingerprint(profile: dict[str, Any]) -> str:
    validate_profile(profile)
    return hashlib.sha256(canonical_json(profile).encode()).hexdigest()


def profile_template_violation(
    profile: dict[str, Any], template: dict[str, Any],
) -> str | None:
    """Return the first authority-boundary violation, if any."""
    validate_profile(profile)
    normalized = validate_profile_template(template)
    checks = (
        (profile["harness"] in normalized["allowed_harnesses"], "harness_not_allowed"),
        (
            profile["model_family"] in normalized["allowed_model_families"],
            "model_family_not_allowed",
        ),
        (
            profile["account_pool_id"] in normalized["allowed_account_pools"],
            "account_pool_not_allowed",
        ),
        (
            profile["permission_policy"] == normalized["permission_policy"],
            "permission_change_not_allowed",
        ),
        (
            set(profile["required_tools"]) <= set(normalized["allowed_tools"]),
            "tool_not_allowed",
        ),
        (
            profile["billing_mode"] in normalized["allowed_billing_modes"],
            "billing_mode_not_allowed",
        ),
    )
    return next((reason for allowed, reason in checks if not allowed), None)


def guarded_change_violation(
    incumbent: dict[str, Any], candidate: dict[str, Any],
) -> str | None:
    """Prevent guarded automation from widening account/tool authority."""
    validate_profile(incumbent)
    validate_profile(candidate)
    if incumbent["permission_policy"] != candidate["permission_policy"]:
        return "guarded_permission_change"
    if incumbent["billing_mode"] != candidate["billing_mode"]:
        return "guarded_billing_change"
    if incumbent["account_pool_id"] != candidate["account_pool_id"]:
        return "guarded_account_route_change"
    if not set(candidate["required_tools"]) <= set(incumbent["required_tools"]):
        return "guarded_tool_expansion"
    return None


def validate_qualification(value: dict[str, Any]) -> dict[str, Any]:
    """Validate a bounded candidate qualification record."""
    _exact(value, QUALIFICATION_FIELDS, "qualification")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("qualification schema_version is invalid")
    qualification_id = _identifier(value["qualification_id"], "qualification_id")
    alias = _identifier(value["alias"], "qualification.alias")
    template_id = _identifier(value["template_id"], "qualification.template_id")
    task_class = _identifier(value["task_class"], "qualification.task_class")
    candidate = json.loads(canonical_json(value["candidate_profile"]))
    validate_profile(candidate)
    experiment_id = value["experiment_id"]
    evaluation_sha256 = value["evaluation_sha256"]
    if (experiment_id is None) != (evaluation_sha256 is None):
        raise ContractError("qualification experiment evidence must be paired")
    if experiment_id is not None:
        _identifier(experiment_id, "qualification.experiment_id")
        _sha256(evaluation_sha256, "qualification.evaluation_sha256")
    source = _exact(value["source"], {
        "harness_version", "catalog_sha256", "model_revision",
    }, "qualification source")
    if source["harness_version"] is not None:
        _identifier(source["harness_version"], "source.harness_version")
    if source["model_revision"] is not None:
        _identifier(source["model_revision"], "source.model_revision")
    _sha256(source["catalog_sha256"], "source.catalog_sha256", nullable=True)
    budget = _exact(value["budget"], {
        "max_cases", "used_cases", "max_worker_invocations",
        "worker_invocations", "max_wall_seconds", "wall_seconds",
    }, "qualification budget")
    for field in budget:
        if type(budget[field]) is not int or budget[field] < 0:
            raise ContractError(f"qualification budget {field} is invalid")
    if (budget["used_cases"] > budget["max_cases"]
            or budget["worker_invocations"] > budget["max_worker_invocations"]
            or budget["wall_seconds"] > budget["max_wall_seconds"]):
        raise ContractError("qualification exceeded its frozen budget")
    measured = _exact(value["measured"], {
        "evaluation_pairs", "held_out_pairs", "critical_defects",
        "latency_ratio", "usage_ratio",
    }, "qualification measurements")
    for field in ("evaluation_pairs", "held_out_pairs", "critical_defects"):
        if type(measured[field]) is not int or measured[field] < 0:
            raise ContractError(f"qualification measurement {field} is invalid")
    for field in ("latency_ratio", "usage_ratio"):
        number = measured[field]
        if (number is not None and (isinstance(number, bool)
                or not isinstance(number, (int, float)) or number < 0)):
            raise ContractError(f"qualification measurement {field} is invalid")
    if value["verdict"] not in {"qualified", "rejected", "incomplete"}:
        raise ContractError("qualification verdict is invalid")
    normalized = {
        **value,
        "qualification_id": qualification_id,
        "alias": alias,
        "template_id": template_id,
        "task_class": task_class,
        "candidate_profile": candidate,
        "source": dict(source),
        "budget": dict(budget),
        "measured": dict(measured),
        "evidence_refs": _strings(
            value["evidence_refs"], "qualification.evidence_refs",
            required=value["verdict"] == "qualified",
        ),
    }
    return json.loads(canonical_json(normalized))


def qualification_gate_failures(
    qualification: dict[str, Any], template: dict[str, Any],
) -> list[str]:
    """Evaluate the static preauthorized qualification gate."""
    record = validate_qualification(qualification)
    policy = validate_profile_template(template)
    reasons = []
    if record["alias"] != policy["alias"] or record["template_id"] != policy["template_id"]:
        reasons.append("template_identity_mismatch")
    if record["task_class"] not in policy["allowed_task_classes"]:
        reasons.append("task_class_not_allowed")
    violation = profile_template_violation(record["candidate_profile"], policy)
    if violation:
        reasons.append(violation)
    if record["candidate_profile"]["quality_status"] not in {"trial", "proven"}:
        reasons.append("candidate_quality_not_eligible")
    if record["experiment_id"] is None:
        reasons.append("experiment_evidence_missing")
    measured = record["measured"]
    gate = policy["gate"]
    if measured["evaluation_pairs"] < gate["min_evaluation_pairs"]:
        reasons.append("insufficient_evaluation_pairs")
    if measured["held_out_pairs"] < gate["min_held_out_pairs"]:
        reasons.append("insufficient_held_out_pairs")
    if measured["critical_defects"] > gate["max_critical_defects"]:
        reasons.append("critical_defect_limit_exceeded")
    for measurement, limit in (
        ("latency_ratio", "max_latency_ratio"),
        ("usage_ratio", "max_usage_ratio"),
    ):
        if gate[limit] is not None and measured[measurement] is None:
            reasons.append(f"{measurement}_missing")
        elif (gate[limit] is not None
                and measured[measurement] > gate[limit]):
            reasons.append(f"{measurement}_limit_exceeded")
    return sorted(set(reasons))


def validate_binding_change(value: dict[str, Any]) -> dict[str, Any]:
    """Validate a replay-safe promotion or rollback request."""
    _exact(value, BINDING_CHANGE_FIELDS, "binding change")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("binding change schema_version is invalid")
    decision_id = _identifier(value["decision_id"], "decision_id")
    alias = _identifier(value["alias"], "binding change alias")
    if value["action"] not in {"promote", "rollback"}:
        raise ContractError("binding change action is invalid")
    if (type(value["expected_binding_version"]) is not int
            or value["expected_binding_version"] < 1):
        raise ContractError("binding change expected version is invalid")
    qualification_id = value["qualification_id"]
    rollback_target = value["rollback_target"]
    if value["action"] == "promote":
        _identifier(qualification_id, "binding change qualification_id")
        if rollback_target is not None:
            raise ContractError("promotion cannot specify a rollback target")
    else:
        if qualification_id is not None:
            raise ContractError("rollback cannot specify a qualification")
        _exact(rollback_target, {"profile_id", "binding_version"}, "rollback target")
        _identifier(rollback_target["profile_id"], "rollback profile_id")
        if type(rollback_target["binding_version"]) is not int or rollback_target["binding_version"] < 1:
            raise ContractError("rollback binding_version is invalid")
    if value["actor"] not in {"human", "guarded_auto"}:
        raise ContractError("binding change actor is invalid")
    reason = _identifier(value["reason"], "binding change reason")
    return json.loads(canonical_json({
        **value,
        "decision_id": decision_id,
        "alias": alias,
        "reason": reason,
        "evidence_refs": _strings(
            value["evidence_refs"], "binding change evidence_refs", required=True,
        ),
        "rollback_target": (
            dict(rollback_target) if rollback_target is not None else None
        ),
    }))


def validate_recorded_at(value: Any) -> str:
    return _timestamp(value, "recorded_at")


def render_binding_decision_markdown(receipt: dict[str, Any]) -> str:
    """Render an inspectable local promotion/rollback decision receipt."""
    required = {
        "schema_version", "decision_id", "action", "alias", "actor", "reason",
        "evidence_refs", "from", "to", "qualification_id", "qualification",
        "policy", "rollback_target", "requested_rollback_target", "effective_at",
        "affects_new_runs_only",
    }
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise ContractError("binding decision receipt is invalid")
    if (receipt["schema_version"] != 1
            or receipt["action"] not in {"promote", "rollback"}
            or receipt["actor"] not in {"human", "guarded_auto"}
            or receipt["affects_new_runs_only"] is not True):
        raise ContractError("binding decision receipt values are invalid")
    _timestamp(receipt["effective_at"], "effective_at")
    lines = [
        f"# Profile binding decision {receipt['decision_id']}",
        "",
        f"- Action: `{receipt['action']}`",
        f"- Alias: `{receipt['alias']}`",
        f"- Actor: `{receipt['actor']}`",
        f"- Effective: `{receipt['effective_at']}`",
        "- Scope: new runs only",
        f"- Reason: {receipt['reason']}",
        "",
        "## Binding change",
        "",
        f"- From: `{receipt['from']['profile_id']}` at version "
        f"{receipt['from']['binding_version']}",
        f"- To: `{receipt['to']['profile_id']}` at version "
        f"{receipt['to']['binding_version']}",
        f"- Rollback: `{receipt['rollback_target']['profile_id']}` at version "
        f"{receipt['rollback_target']['binding_version']}",
        f"- Policy: `{receipt['policy']['id']}` version {receipt['policy']['version']}",
        "",
        "## Evidence",
        "",
    ]
    lines.extend(
        [f"- `{reference}`" for reference in receipt["evidence_refs"]]
        or ["- None."]
    )
    return "\n".join(lines) + "\n"
