"""Dependency-free strict validation for M1 public fixtures."""

from __future__ import annotations
from pathlib import Path
from typing import Any
from .contracts import ContractError

TASK_FIELDS = {"schema_version", "project", "workflow", "goal", "task_class", "acceptance", "checks", "scope", "lead", "routing", "budget", "origin", "review"}
MAX_ACCEPTANCE_CRITERIA = 100
MAX_CHECKS = 16
MAX_SCOPE_PATHS = 256


def _exact(value: dict[str, Any], allowed: set[str], required: set[str], label: str) -> None:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    unknown, missing = set(value) - allowed, required - set(value)
    if unknown or missing:
        raise ContractError(f"{label} fields invalid: unknown={sorted(unknown)} missing={sorted(missing)}")


def _relative(path: str, label: str) -> None:
    if not isinstance(path, str) or not path:
        raise ContractError(f"{label} must be a non-empty string")
    p = Path(path)
    if p.is_absolute() or ".." in p.parts:
        raise ContractError(f"{label} must be repository-relative without traversal")


def validate_task(value: dict[str, Any], *, require_existing_repo: bool = False) -> None:
    _exact(value, TASK_FIELDS, TASK_FIELDS - {"review"}, "task")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or not isinstance(value["workflow"], str) or value["workflow"] not in {"branch-review", "issue-delivery"}:
        raise ContractError("unsupported task schema or workflow")
    project = value["project"]
    _exact(project, {"repo_path", "base_ref", "target_ref"}, {"repo_path", "base_ref", "target_ref"}, "project")
    if not all(isinstance(project[k], str) and project[k] for k in ("repo_path", "base_ref", "target_ref")) or not Path(project["repo_path"]).is_absolute() or (require_existing_repo and not (Path(project["repo_path"]) / ".git").exists()):
        raise ContractError("project.repo_path must be an existing absolute Git repository")
    if not isinstance(value["goal"], str) or not value["goal"].strip() or not isinstance(value["task_class"], str) or not value["task_class"].strip():
        raise ContractError("goal and task_class must be non-empty strings")
    if (not isinstance(value["acceptance"], list) or not value["acceptance"]
            or len(value["acceptance"]) > MAX_ACCEPTANCE_CRITERIA):
        raise ContractError("acceptance must be a non-empty bounded array")
    acceptance_ids = set()
    for item in value["acceptance"]:
        _exact(item, {"id", "description", "evidence_kind"}, {"id", "description", "evidence_kind"}, "acceptance item")
        if not all(isinstance(item[k], str) and item[k].strip() for k in ("id", "description")):
            raise ContractError("acceptance id and description must be non-empty strings")
        if not isinstance(item["evidence_kind"], str) or item["evidence_kind"] not in {"review", "check", "artifact", "host"}:
            raise ContractError("invalid evidence_kind")
        if item["id"] in acceptance_ids:
            raise ContractError("acceptance ids must be unique")
        acceptance_ids.add(item["id"])
    if not isinstance(value["checks"], list) or len(value["checks"]) > MAX_CHECKS:
        raise ContractError("checks must be a bounded array")
    check_ids = set()
    for check in value["checks"]:
        _exact(check, {"id", "argv", "cwd", "timeout_seconds", "required_to_pass", "output_paths"}, {"id", "argv", "cwd", "timeout_seconds", "required_to_pass"}, "check")
        if not isinstance(check["id"], str) or not check["id"]: raise ContractError("check id must be non-empty")
        if check["id"] in check_ids: raise ContractError("check ids must be unique")
        check_ids.add(check["id"])
        if not isinstance(check["argv"], list) or not check["argv"] or not all(isinstance(v, str) and v for v in check["argv"]): raise ContractError("check argv must be a non-empty string array")
        _relative(check["cwd"], "check cwd")
        if not isinstance(check["timeout_seconds"], int) or isinstance(check["timeout_seconds"], bool) or check["timeout_seconds"] <= 0: raise ContractError("check timeout must be positive")
        if type(check["required_to_pass"]) is not bool: raise ContractError("required_to_pass must be boolean")
        outputs = check.get("output_paths", [])
        if not isinstance(outputs, list) or len(outputs) > 32:
            raise ContractError("check output_paths must be a bounded array")
        for output in outputs:
            _relative(output, "check output path")
            if Path(output).as_posix() != output or output == "." or ".git" in Path(output).parts:
                raise ContractError("check output path must be canonical and exclude Git metadata/root")
        if len(set(outputs)) != len(outputs):
            raise ContractError("check output paths must be unique")
    scope = value["scope"]; _exact(scope, {"read_paths", "write_paths"}, {"read_paths", "write_paths"}, "scope")
    if not isinstance(scope["read_paths"], list) or not isinstance(scope["write_paths"], list) or not all(isinstance(p, str) and p for p in scope["read_paths"] + scope["write_paths"]): raise ContractError("scope paths must be non-empty string arrays")
    if len(scope["read_paths"]) + len(scope["write_paths"]) > MAX_SCOPE_PATHS: raise ContractError("scope paths exceed their bound")
    if len(set(scope["read_paths"])) != len(scope["read_paths"]) or len(set(scope["write_paths"])) != len(scope["write_paths"]): raise ContractError("scope paths must be unique")
    for p in scope["read_paths"] + scope["write_paths"]: _relative(p, "scope path")
    if value["workflow"] == "branch-review" and scope["write_paths"]: raise ContractError("branch review cannot write")
    lead = value["lead"]; _exact(lead, {"mode"}, {"mode"}, "lead")
    if not isinstance(lead["mode"], str) or lead["mode"] not in {"host", "headless"}: raise ContractError("invalid lead mode")
    routing = value["routing"]
    _exact(
        routing,
        {"profiles_file", "policy_file", "profiles", "policy", "overrides"},
        set(),
        "routing",
    )
    file_fields = {"profiles_file", "policy_file"}
    embedded_fields = {"profiles", "policy"}
    if set(routing) & file_fields == file_fields and not set(routing) & embedded_fields:
        for key in file_fields:
            if not isinstance(routing[key], str) or not routing[key]:
                raise ContractError(f"routing {key} must be a path")
    elif set(routing) & embedded_fields == embedded_fields and not set(routing) & file_fields:
        validate_profile_registry(routing["profiles"])
        validate_policy(routing["policy"])
    else:
        raise ContractError(
            "routing requires exactly one complete file or embedded configuration"
        )
    overrides = routing.get("overrides", {})
    if not isinstance(overrides, dict): raise ContractError("routing overrides must be an object")
    for role, override in overrides.items():
        if role not in {"implementer", "reviewer", "lead", "researcher"}: raise ContractError("invalid override role")
        _exact(override, {"profile_id", "fallback"}, {"profile_id"}, "routing override")
        if not isinstance(override["profile_id"], str) or not override["profile_id"]: raise ContractError("override profile_id must be non-empty")
        if not isinstance(override.get("fallback", "none"), str) or override.get("fallback", "none") not in {"none", "policy"}: raise ContractError("override fallback must be none or policy")
    origin = value["origin"]; _exact(origin, {"surface", "session_ref"}, {"surface"}, "origin")
    if not isinstance(origin["surface"], str) or not origin["surface"]: raise ContractError("origin surface must be non-empty")
    if "session_ref" in origin and (not isinstance(origin["session_ref"], str) or not origin["session_ref"]): raise ContractError("origin session_ref must be non-empty")
    if "review" in value:
        review = value["review"]; _exact(review, {"mode", "focus"}, {"mode"}, "review")
        if not isinstance(review["mode"], str) or review["mode"] not in {"standard", "adversarial"}: raise ContractError("invalid review mode")
        if "focus" in review and review["mode"] != "adversarial": raise ContractError("review focus requires adversarial mode")
        if "focus" in review and (not isinstance(review["focus"], str) or not review["focus"]): raise ContractError("review focus must be non-empty")
    budget = value["budget"]; required = {"wall_seconds", "max_worker_invocations", "max_revisions", "max_fallbacks_per_step"}; _exact(budget, required, required, "budget")
    for key, number in budget.items():
        if not isinstance(number, int) or isinstance(number, bool) or number < 0: raise ContractError(f"budget {key} must be a finite non-negative integer")
    if budget["wall_seconds"] == 0 or budget["max_worker_invocations"] == 0: raise ContractError("wall_seconds and max_worker_invocations must be positive")


def validate_profile(value: dict[str, Any]) -> None:
    fields = {"id", "harness", "model_family", "model_id", "effort", "required_tools", "permission_policy", "account_pool_id", "billing_mode", "quality_status", "evidence_refs"}
    _exact(value, fields, fields, "profile")
    for key in ("id", "harness", "model_family", "model_id", "account_pool_id"):
        if not isinstance(value[key], str) or not value[key]: raise ContractError(f"profile {key} must be non-empty")
    effort = value["effort"]; _exact(effort, {"value", "transport"}, {"value", "transport"}, "profile effort")
    if effort["value"] is not None and not isinstance(effort["value"], str): raise ContractError("effort value must be string or null")
    if not isinstance(effort["transport"], str) or effort["transport"] not in {"native", "model_variant", "provider_default"}: raise ContractError("invalid effort transport")
    for key in ("required_tools", "evidence_refs"):
        if not isinstance(value[key], list) or not all(isinstance(v, str) and v for v in value[key]) or len(set(value[key])) != len(value[key]): raise ContractError(f"profile {key} must contain unique non-empty strings")
    if not isinstance(value["permission_policy"], str) or value["permission_policy"] not in {"read_only", "workspace_write"}: raise ContractError("invalid permission policy")
    if not isinstance(value["billing_mode"], str) or value["billing_mode"] not in {"subscription", "paid_api"}: raise ContractError("invalid billing mode")
    if not isinstance(value["quality_status"], str) or value["quality_status"] not in {"unvalidated", "trial", "proven", "suspended"}: raise ContractError("invalid quality status")


def validate_profile_registry(value: dict[str, Any]) -> None:
    fields = {"schema_version", "profiles", "bindings"}
    _exact(value, fields, fields, "profile registry")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("invalid profile registry schema version")
    profiles = value["profiles"]
    if not isinstance(profiles, list) or not profiles:
        raise ContractError("profile registry profiles must be a non-empty array")
    identifiers = []
    for profile in profiles:
        validate_profile(profile)
        identifiers.append(profile["id"])
    if len(set(identifiers)) != len(identifiers):
        raise ContractError("profile registry profile ids must be unique")
    bindings = value["bindings"]
    if not isinstance(bindings, dict):
        raise ContractError("profile registry bindings must be an object")
    for alias, binding in bindings.items():
        if not isinstance(alias, str) or not alias:
            raise ContractError("profile binding aliases must be non-empty strings")
        _exact(binding, {"profile_id", "version"}, {"profile_id", "version"}, "profile binding")
        if not isinstance(binding["profile_id"], str) or not binding["profile_id"]:
            raise ContractError("profile binding profile_id must be non-empty")
        if type(binding["version"]) is not int or binding["version"] < 1:
            raise ContractError("profile binding version must be positive")
        if binding["profile_id"] not in identifiers:
            raise ContractError(f"profile binding target does not exist: {binding['profile_id']}")


def validate_policy(value: dict[str, Any]) -> None:
    fields = {"schema_version", "id", "version", "roles", "task_classes", "require_different_model_for_review", "prefer_different_harness_for_review", "account_pools", "experiment_budget", "decision_helper"}
    required = fields - {"prefer_different_harness_for_review", "decision_helper"}
    _exact(value, fields, required, "policy")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or type(value["version"]) is not int or value["version"] < 1: raise ContractError("invalid policy version")
    if not isinstance(value["id"], str) or not value["id"]: raise ContractError("policy id must be non-empty")
    if type(value["require_different_model_for_review"]) is not bool or ("prefer_different_harness_for_review" in value and type(value["prefer_different_harness_for_review"]) is not bool): raise ContractError("policy review flags must be boolean")
    if not isinstance(value["roles"], dict) or set(value["roles"]) - {"implementer", "reviewer", "lead", "researcher"}: raise ContractError("invalid policy roles")
    for candidates in value["roles"].values():
        if not isinstance(candidates, list) or not candidates: raise ContractError("role candidates must be non-empty arrays")
        for ref in candidates:
            _exact(ref, {"kind", "id"}, {"kind", "id"}, "candidate reference")
            if not isinstance(ref["kind"], str) or ref["kind"] not in {"profile", "alias"} or not isinstance(ref["id"], str) or not ref["id"]: raise ContractError("invalid candidate reference")
    for key in ("task_classes", "account_pools", "experiment_budget"):
        if not isinstance(value[key], dict): raise ContractError(f"policy {key} must be an object")
        _validate_json_tree(value[key], f"policy {key}")
    if not all(isinstance(k, str) and k and isinstance(v, str) and v in {"unvalidated", "trial", "proven", "suspended"} for k, v in value["task_classes"].items()):
        raise ContractError("task_classes must map names to quality status")
    if not all(isinstance(k, str) and k and isinstance(v, dict) for k, v in value["account_pools"].items()):
        raise ContractError("account_pools must map names to objects")
    for pool in value["account_pools"].values():
        _exact(
            pool,
            {"allowed_billing_modes", "max_concurrency", "unknown_capacity_policy"},
            {"allowed_billing_modes", "max_concurrency"},
            "account pool policy",
        )
        modes = pool["allowed_billing_modes"]
        if (not isinstance(modes, list) or not modes
                or len(set(modes)) != len(modes)
                or not all(isinstance(mode, str) and mode in {"subscription", "paid_api"}
                           for mode in modes)):
            raise ContractError("account pool billing modes are invalid")
        if type(pool["max_concurrency"]) is not int or pool["max_concurrency"] < 1:
            raise ContractError("account pool max_concurrency must be positive")
        if pool.get("unknown_capacity_policy", "allow_bounded") not in {"allow_bounded", "block"}:
            raise ContractError("account pool unknown_capacity_policy is invalid")
    if not all(isinstance(k, str) and k and type(v) is int and v >= 0 for k, v in value["experiment_budget"].items()):
        raise ContractError("experiment_budget must contain non-negative integers")
    from .decision import validate_decision_policy
    validate_decision_policy(value.get("decision_helper"))


def _validate_json_tree(value: Any, label: str) -> None:
    """Reject non-JSON and numerically ambiguous values in extension maps."""
    if value is None or isinstance(value, str) or type(value) is bool:
        return
    if type(value) is int:
        return
    if isinstance(value, list):
        for item in value: _validate_json_tree(item, label)
        return
    if isinstance(value, dict):
        if not all(isinstance(k, str) and k for k in value): raise ContractError(f"{label} keys must be non-empty strings")
        for item in value.values(): _validate_json_tree(item, label)
        return
    raise ContractError(f"{label} contains a non-JSON or non-finite value")
