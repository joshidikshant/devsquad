"""Dependency-free strict validation for M1 public fixtures."""

from __future__ import annotations
from pathlib import Path
from typing import Any
from .contracts import ContractError

TASK_FIELDS = {"schema_version", "project", "workflow", "goal", "task_class", "acceptance", "checks", "scope", "lead", "routing", "budget", "origin", "review"}


def _exact(value: dict[str, Any], allowed: set[str], required: set[str], label: str) -> None:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    unknown, missing = set(value) - allowed, required - set(value)
    if unknown or missing:
        raise ContractError(f"{label} fields invalid: unknown={sorted(unknown)} missing={sorted(missing)}")


def _relative(path: str, label: str) -> None:
    p = Path(path)
    if p.is_absolute() or ".." in p.parts:
        raise ContractError(f"{label} must be repository-relative without traversal")


def validate_task(value: dict[str, Any], *, require_existing_repo: bool = False) -> None:
    _exact(value, TASK_FIELDS, TASK_FIELDS - {"review"}, "task")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["workflow"] not in {"branch-review", "issue-delivery"}:
        raise ContractError("unsupported task schema or workflow")
    project = value["project"]
    _exact(project, {"repo_path", "base_ref", "target_ref"}, {"repo_path", "base_ref", "target_ref"}, "project")
    if not Path(project["repo_path"]).is_absolute() or (require_existing_repo and not (Path(project["repo_path"]) / ".git").exists()):
        raise ContractError("project.repo_path must be an existing absolute Git repository")
    if not isinstance(value["goal"], str) or not value["goal"].strip() or not isinstance(value["task_class"], str) or not value["task_class"].strip():
        raise ContractError("goal and task_class must be non-empty strings")
    if not isinstance(value["acceptance"], list) or not value["acceptance"]:
        raise ContractError("acceptance must be non-empty")
    for item in value["acceptance"]:
        _exact(item, {"id", "description", "evidence_kind"}, {"id", "description", "evidence_kind"}, "acceptance item")
        if item["evidence_kind"] not in {"review", "check", "artifact", "host"}:
            raise ContractError("invalid evidence_kind")
    if not isinstance(value["checks"], list): raise ContractError("checks must be an array")
    for check in value["checks"]:
        _exact(check, {"id", "argv", "cwd", "timeout_seconds", "required_to_pass"}, {"id", "argv", "cwd", "timeout_seconds", "required_to_pass"}, "check")
        if not isinstance(check["argv"], list) or not check["argv"] or not all(isinstance(v, str) and v for v in check["argv"]): raise ContractError("check argv must be a non-empty string array")
        _relative(check["cwd"], "check cwd")
        if not isinstance(check["timeout_seconds"], int) or isinstance(check["timeout_seconds"], bool) or check["timeout_seconds"] <= 0: raise ContractError("check timeout must be positive")
        if type(check["required_to_pass"]) is not bool: raise ContractError("required_to_pass must be boolean")
    scope = value["scope"]; _exact(scope, {"read_paths", "write_paths"}, {"read_paths", "write_paths"}, "scope")
    if not isinstance(scope["read_paths"], list) or not isinstance(scope["write_paths"], list) or not all(isinstance(p, str) for p in scope["read_paths"] + scope["write_paths"]): raise ContractError("scope paths must be string arrays")
    for p in scope["read_paths"] + scope["write_paths"]: _relative(p, "scope path")
    if value["workflow"] == "branch-review" and scope["write_paths"]: raise ContractError("branch review cannot write")
    lead = value["lead"]; _exact(lead, {"mode"}, {"mode"}, "lead")
    if lead["mode"] not in {"host", "headless"}: raise ContractError("invalid lead mode")
    routing = value["routing"]; _exact(routing, {"profiles_file", "policy_file", "overrides"}, {"profiles_file", "policy_file"}, "routing")
    for key in ("profiles_file", "policy_file"):
        if not isinstance(routing[key], str) or not routing[key]: raise ContractError(f"routing {key} must be a path")
    origin = value["origin"]; _exact(origin, {"surface", "session_ref"}, {"surface"}, "origin")
    if not isinstance(origin["surface"], str) or not origin["surface"]: raise ContractError("origin surface must be non-empty")
    if "review" in value:
        review = value["review"]; _exact(review, {"mode", "focus"}, {"mode"}, "review")
        if review["mode"] not in {"standard", "adversarial"}: raise ContractError("invalid review mode")
        if "focus" in review and review["mode"] != "adversarial": raise ContractError("review focus requires adversarial mode")
    budget = value["budget"]; required = {"wall_seconds", "max_worker_invocations", "max_revisions", "max_fallbacks_per_step"}; _exact(budget, required, required, "budget")
    for key, number in budget.items():
        if not isinstance(number, int) or isinstance(number, bool) or number < 0: raise ContractError(f"budget {key} must be a finite non-negative integer")
    if budget["wall_seconds"] == 0 or budget["max_worker_invocations"] == 0: raise ContractError("wall_seconds and max_worker_invocations must be positive")
