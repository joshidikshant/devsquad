"""Manual, bounded Council command preparation; automatic triggering is off."""
from __future__ import annotations

from pathlib import Path

from .contracts import CapabilityUnavailable, ContractError
from .council import ROLES, digest, validate_spec
from .task_entry import _relative_path, _resolve_commit, resolve_repository
from .validation import validate_task


def build_council_task(*, project_dir: Path, goal: str, identities: list[dict],
                       base_ref: str = "HEAD", target_ref: str = "HEAD", read_paths=(), checks=(),
                       lead_mode: str = "headless", max_invocations: int = 4, evidence=(), rubric=None) -> tuple[dict, dict]:
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 16000:
        raise ContractError("Council question must be non-empty and bounded")
    if (len(identities) != 3 or any(not isinstance(value, dict) or value.get("harness") != "codex" for value in identities)
            or len({value.get("model_id", "").casefold() for value in identities}) != 3):
        raise CapabilityUnavailable("Council needs three distinct entitled, verified Codex model IDs")
    repo = resolve_repository(project_dir)
    base, target = _resolve_commit(repo, base_ref), _resolve_commit(repo, target_ref)
    paths = list(dict.fromkeys(_relative_path(path, "Council read path") for path in read_paths))
    profiles = []
    for role, identity in zip(ROLES, identities):
        if any(not isinstance(identity.get(key), str) or not identity[key] for key in ("model_id", "model_family", "effort", "harness_version")):
            raise ContractError("Council native catalog identity is incomplete")
        profile = {"id": f"managed-council-{role}", "harness": "codex", "model_family": identity["model_family"],
            "model_id": identity["model_id"], "effort": {"value": identity["effort"], "transport": "native"},
            "required_tools": [], "permission_policy": "read_only", "account_pool_id": identity.get("account_pool_id", "codex-subscription"),
            "billing_mode": "subscription", "quality_status": "trial",
            "evidence_refs": [f"runtime-catalog:{identity['harness_version']}:{identity['model_id']}",
                *([f"runtime-catalog-fingerprint:{identity['catalog_fingerprint']}"] if "catalog_fingerprint" in identity else []),
                *([f"runtime-native-scope:{identity['native_scope']}"] if "native_scope" in identity else [])]}
        profile["id"] += "-" + digest(profile)[:16]
        profiles.append(profile)
    rubric = rubric or [{"id": "correctness", "description": "Claims fit the frozen evidence"},
                         {"id": "risk", "description": "Important risks and objections are retained"},
                         {"id": "validation", "description": "Decision names objective validation and uncertainty"}]
    seed = digest({"goal": goal.strip(), "base_oid": base, "target_oid": target, "read_paths": paths,
                   "evidence": list(evidence), "rubric": rubric, "profiles": profiles})
    council = {"schema_version": 1, "enabled": True, "automatic": False,
        "reason": "Explicit manual Council request", "min_valid_proposals": 2, "required_critics": 1,
        "max_invocations": max_invocations, "seed": seed, "evidence": list(evidence), "rubric": rubric}
    validate_spec(council)
    roles = {role: [{"kind": "profile", "id": profile["id"]}] for role, profile in zip(ROLES, profiles)}
    if lead_mode == "headless":
        roles["lead"] = [{"kind": "profile", "id": profiles[-1]["id"]}]
    declared_checks = [{"id": "candidate-integrity", "argv": ["git", "diff", "--check", base, target],
                        "cwd": ".", "timeout_seconds": 30, "required_to_pass": True}]
    declared_checks.extend({"id": f"user-check-{index + 1}", "argv": list(argv), "cwd": ".",
                            "timeout_seconds": 60, "required_to_pass": True} for index, argv in enumerate(checks))
    task = {"schema_version": 1, "project": {"repo_path": str(repo), "base_ref": base, "target_ref": target},
        "workflow": "council-decision", "goal": goal.strip(), "task_class": "managed-council",
        "acceptance": [{"id": item["id"], "description": item["description"], "evidence_kind": "review"} for item in rubric],
        "checks": declared_checks, "scope": {"read_paths": paths, "write_paths": []}, "lead": {"mode": lead_mode},
        "routing": {"profiles": {"schema_version": 1, "profiles": profiles, "bindings": {}},
            "policy": {"schema_version": 1, "id": "managed-manual-council", "version": 1,
                "roles": roles, "task_classes": {"managed-council": "trial"}, "require_different_model_for_review": True,
                "prefer_different_harness_for_review": True, "account_pools": {profile["account_pool_id"]: {
                    "allowed_billing_modes": ["subscription"], "max_concurrency": 1, "unknown_capacity_policy": "allow_bounded"} for profile in profiles},
                "experiment_budget": {}, "decision_helper": {"schema_version": 1, "mode": "off"}}},
        "budget": {"wall_seconds": 600, "max_worker_invocations": max_invocations, "max_revisions": 0, "max_fallbacks_per_step": 0},
        "origin": {"surface": "cli"}, "council": council}
    validate_task(task, require_existing_repo=True)
    summary = {"workflow": "council-decision", "project": str(repo), "base_oid": base, "target_oid": target, "goal": task["goal"],
        "profiles": {role: {"profile_id": profile["id"], "model_id": profile["model_id"], "quality_status": "trial"} for role, profile in zip(ROLES, profiles)},
        "planned_roles": {role: {"profile_id": profile["id"], "harness": "codex", "model_id": profile["model_id"],
            "effort": profile["effort"]["value"], "selection_mode": "catalog_trial", "quality_status": "trial"} for role, profile in zip(ROLES, profiles)},
        "selection_reason": "Three distinct available catalog identities; actual entitlement and identity must be verified before quorum; no quality qualification implied",
        "scope": task["scope"], "checks": [item["id"] for item in declared_checks], "check_plan": declared_checks, "task_sha256": digest(task),
        "budget": task["budget"], "lead": task["lead"], "automatic_enabled": False,
        "native_ready": False,
        "limitations": ["Manual only", "One round", "Trial catalog profiles are not quality proof", "Requires verified macOS default-deny isolation"]}
    return task, summary
