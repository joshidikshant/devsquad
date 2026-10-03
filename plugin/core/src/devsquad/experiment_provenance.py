"""Pure prelaunch experiment contracts; saved-run authority lives in Store.

An assignment dictionary alone is not execution evidence. It must be frozen
under the preparation fence and checked against actual saved attempts before
it can authorize evaluation or a lifecycle decision.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any

from .contracts import ContractError
from .store import canonical_json
from .validation import validate_profile, validate_task


def require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ContractError(f"experiment {label} SHA-256 is invalid")
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def assignment_for(
    spec: dict[str, Any], case_id: str, arm: str, *, project_common_dir: str,
) -> dict[str, Any]:
    """Construct the exact assignment a fenced prelaunch must persist."""
    from .learning import validate_experiment

    spec = validate_experiment(spec)
    if spec["schema_version"] != 2:
        raise ContractError("experiment assignment requires a v2 specification")
    if not isinstance(arm, str) or arm not in {"control", "candidate"}:
        raise ContractError("experiment assignment arm is invalid")
    if (not isinstance(project_common_dir, str) or not project_common_dir
            or not Path(project_common_dir).is_absolute()
            or ".." in Path(project_common_dir).parts):
        raise ContractError("experiment project common directory is invalid")
    case = next((row for row in spec["cases"] if row["case_id"] == case_id), None)
    if case is None:
        raise ContractError("experiment assignment case is not declared")
    variable = spec["variable"]
    return {
        "schema_version": 1,
        "experiment_id": spec["experiment_id"],
        "spec_sha256": _digest(spec),
        "project_common_dir": project_common_dir,
        "case_id": case["case_id"],
        "split": case["split"],
        "arm": arm,
        "role": variable["role"],
        "profile_id": variable[f"{arm}_profile_id"],
        "profile_sha256": variable[f"{arm}_profile_sha256"],
        "execution_sha256": variable[f"{arm}_execution_sha256"],
        "input_sha256": case["input_sha256"],
        "case_sha256": case["case_sha256"],
        "outcome_id": case[f"{arm}_outcome_id"],
    }


def validate_assignment(
    value: dict[str, Any], *, spec: dict[str, Any], project_common_dir: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int:
        raise ContractError("experiment assignment fields are invalid")
    expected = assignment_for(
        spec, value.get("case_id"), value.get("arm"),
        project_common_dir=project_common_dir,
    )
    if value != expected:
        raise ContractError("experiment assignment does not match its frozen contract")
    return expected


def _profile_identity(selected: Any) -> dict[str, Any]:
    if not isinstance(selected, dict) or not isinstance(selected.get("profile"), dict):
        raise ContractError("experiment frozen profile is missing")
    profile = selected["profile"]
    validate_profile(profile)
    if (selected.get("profile_id") != profile["id"]
            or selected.get("profile_sha256") != _digest(profile)):
        raise ContractError("experiment frozen profile fingerprint is inconsistent")
    return {"profile_id": profile["id"], "profile_sha256": selected["profile_sha256"]}


def _adapter_identity(snapshot: dict[str, Any], role: str, selected: dict[str, Any]) -> Any:
    """Preserve supporting native execution context, never auth paths."""
    if selected["profile"]["harness"] == "fixture":
        return {"harness": "fixture"}
    prefix = {"implementer": "implementation", "reviewer": "review", "lead": "lead"}[role]
    adapters = snapshot.get(f"{prefix}_adapters")
    if not isinstance(adapters, dict):
        raise ContractError("experiment supporting native adapters are missing")
    adapter = adapters.get(selected["profile_id"])
    if not isinstance(adapter, dict):
        raise ContractError("experiment supporting native adapter is missing")
    require_sha256(adapter.get("binary_sha256"), "native binary")
    for key in ("harness", "harness_version", "model_provider", "transport"):
        if not isinstance(adapter.get(key), str) or not adapter[key]:
            raise ContractError("experiment supporting native adapter identity is invalid")
    if adapter["harness"] != selected["profile"]["harness"]:
        raise ContractError("experiment supporting native adapter harness is inconsistent")
    # Frozen adapter schemas already delimit native settings. Exclude only
    # resolved machine-local executable and credential locations, not argv,
    # tool/permission arguments, protocol/output-schema or binary fingerprints.
    return {key: value for key, value in adapter.items() if key not in {"binary", "auth_file"}}


def paired_input_identity(
    snapshot: dict[str, Any], *, role: str, package_digest: str,
) -> dict[str, str]:
    """Hash corpus identity separately from the full controlled pair context.

    No assignment, outcome, produced implementation or mutable runtime field
    participates. Thus predeclaring case hashes cannot create a spec/hash cycle.
    """
    if not isinstance(snapshot, dict):
        raise ContractError("experiment frozen snapshot is missing")
    task = snapshot.get("task")
    validate_task(task)
    if not isinstance(role, str) or role not in {"implementer", "reviewer"}:
        raise ContractError("experiment tested role is invalid")
    if role == "implementer" and task["workflow"] != "issue-delivery":
        raise ContractError("experiment implementer requires issue-delivery")
    require_sha256(package_digest, "runtime package")
    for key in ("base_oid", "target_oid"):
        if (not isinstance(snapshot.get(key), str)
                or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", snapshot[key]) is None):
            raise ContractError("experiment frozen commit identity is invalid")
    source = {key: snapshot[key] for key in ("base_oid", "target_oid")}
    if role == "reviewer":
        workspace = snapshot.get("workspace")
        if not isinstance(workspace, dict):
            raise ContractError("experiment frozen review candidate is missing")
        source["candidate_sha256"] = require_sha256(
            workspace.get("candidate_sha256"), "review candidate",
        )
        # Delivery review workspaces can have a new candidate target. Record
        # their exact commits in addition to the task's original baseline.
        for key in ("base_oid", "target_oid"):
            value = workspace.get(key)
            if not isinstance(value, str) or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is None:
                raise ContractError("experiment review candidate commit is invalid")
            source[f"candidate_{key}"] = value
    else:
        delivery = snapshot.get("delivery_workspace")
        if not isinstance(delivery, dict) or delivery.get("baseline_oid") != snapshot["target_oid"]:
            raise ContractError("experiment frozen implementation baseline is inconsistent")
    routing = snapshot.get("routing")
    if not isinstance(routing, dict) or not isinstance(routing.get("roles"), dict):
        raise ContractError("experiment frozen routing is missing")
    expected_roles = {"reviewer"}
    if task["workflow"] == "issue-delivery":
        expected_roles.add("implementer")
    if task["lead"]["mode"] == "headless":
        expected_roles.add("lead")
    if set(routing["roles"]) != expected_roles or role not in expected_roles:
        raise ContractError("experiment frozen roles do not match the task")
    selected_execution_fingerprint(snapshot, role=role)
    try:
        policy_sha256 = require_sha256(snapshot["configs"]["policy_file"]["sha256"], "policy")
        if policy_sha256 != routing["policy"]["sha256"]:
            raise ContractError("experiment frozen policy fingerprint is inconsistent")
    except (KeyError, TypeError) as exc:
        raise ContractError("experiment frozen policy is missing") from exc
    supporting = {}
    tested_fallbacks = []
    for name, route in routing["roles"].items():
        if (not isinstance(route, dict) or not isinstance(route.get("selected"), dict)
                or not isinstance(route.get("fallbacks"), list)
                or not isinstance(route.get("fallback_mode"), str)
                or route["fallback_mode"] not in {"none", "policy"}
                or (route["fallback_mode"] == "none" and route["fallbacks"])):
            raise ContractError("experiment frozen role selection is invalid")
        identities = []
        for index, selected in enumerate([route["selected"], *route["fallbacks"]]):
            identity = _profile_identity(selected)
            if name != role or index > 0:
                context_identity = {**identity, "adapter": _adapter_identity(snapshot, name, selected)}
                identities.append(context_identity)
                if name == role:
                    tested_fallbacks.append(context_identity)
        if name != role:
            supporting[name] = {"fallback_mode": route["fallback_mode"], "profiles": identities}
    corpus = {
        "schema_version": 1,
        "source": source,
        "task": {key: task[key] for key in (
            "workflow", "goal", "task_class", "acceptance", "checks", "scope",
        )},
        "review": task.get("review", {"mode": "standard"}),
    }
    context = {
        "schema_version": 1, "case_sha256": _digest(corpus),
        "tested_role": role, "lead": task["lead"], "budget": task["budget"],
        "policy_sha256": policy_sha256, "package_digest": package_digest,
        "supporting_roles": supporting,
        "tested_fallback_mode": routing["roles"][role]["fallback_mode"],
        "tested_fallbacks": tested_fallbacks,
    }
    # Canonical hashing rejects non-finite/non-serializable selected context;
    # only these digests cross the evidence boundary.
    return {"case_sha256": context["case_sha256"], "input_sha256": _digest(context)}


def selected_execution_fingerprint(snapshot: dict[str, Any], *, role: str) -> str:
    """Per-arm concrete execution variable, including native version/binary.

    The experiment explicitly declares one fingerprint for each arm. This
    permits an intentional harness change without silently permitting drift.
    """
    if not isinstance(role, str) or role not in {"implementer", "reviewer"}:
        raise ContractError("experiment tested execution role is invalid")
    try:
        selected = snapshot["routing"]["roles"][role]["selected"]
    except (KeyError, TypeError) as exc:
        raise ContractError("experiment tested execution selection is missing") from exc
    profile = _profile_identity(selected)
    return _digest({"profile_sha256": profile["profile_sha256"],
                    "adapter": _adapter_identity(snapshot, role, selected)})


def validate_arm_chain(
    chain: Any, *, spec: dict[str, Any], case: dict[str, Any], arm: str,
    project_common_dir: str, evaluated_at: str,
) -> dict[str, Any]:
    """Check a normalized chain supplied by the saved-run evidence reader.

    These shape/hash checks do not authorize importing arbitrary caller
    provenance. Store must derive the witness from fenced prelaunch records,
    actual attempts and append-only outcomes, not accept a submitted witness.
    """
    from .learning import _timestamp, validate_outcome

    if not isinstance(chain, dict) or set(chain) != {"final", "late_corrections", "provenance"}:
        raise ContractError("experiment arm requires saved-run provenance")
    provenance = chain["provenance"]
    fields = {
        "run_id", "assignment", "profile_sha256", "input_sha256", "case_sha256",
        "attempt_ids", "attempts_sha256", "final_outcome_sha256", "correction_sha256",
        "execution_sha256",
    }
    if not isinstance(provenance, dict) or set(provenance) != fields:
        raise ContractError("experiment arm provenance fields are invalid")
    expected = assignment_for(spec, case["case_id"], arm, project_common_dir=project_common_dir)
    if validate_assignment(provenance["assignment"], spec=spec, project_common_dir=project_common_dir) != expected:
        raise ContractError("experiment arm provenance has a crossed assignment")
    if any(provenance[key] != expected[key] for key in (
        "profile_sha256", "execution_sha256", "input_sha256", "case_sha256",
    )):
        raise ContractError("experiment arm provenance does not match actual profile/input")
    run_id = provenance["run_id"]
    if not isinstance(run_id, str) or not run_id.strip():
        raise ContractError("experiment provenance run id is missing")
    attempt_ids = provenance["attempt_ids"]
    if (not isinstance(attempt_ids, list) or not attempt_ids
            or any(not isinstance(item, str) or not item.strip() for item in attempt_ids)
            or len(set(attempt_ids)) != len(attempt_ids)):
        raise ContractError("experiment provenance requires distinct actual attempt ids")
    require_sha256(provenance["attempts_sha256"], "actual attempts")
    final = validate_outcome(chain["final"], now=_timestamp(evaluated_at, "evaluated_at"))
    if (final["kind"] != "final" or final["selection_mode"] != "experimental"
            or final["outcome_id"] != expected["outcome_id"]):
        raise ContractError("experiment provenance final outcome is inconsistent")
    if provenance["final_outcome_sha256"] != _digest(final):
        raise ContractError("experiment provenance final outcome hash is inconsistent")
    corrections = chain["late_corrections"]
    if not isinstance(corrections, list):
        raise ContractError("experiment provenance corrections must be an array")
    correction_ids = set()
    hashes = []
    for correction in corrections:
        normalized = validate_outcome(correction, now=_timestamp(evaluated_at, "evaluated_at"))
        if (normalized["kind"] != "late_correction" or normalized["selection_mode"] != "experimental"
                or normalized["corrects_outcome_id"] != final["outcome_id"]
                or _timestamp(normalized["observed_at"], "observed_at")
                < _timestamp(final["observed_at"], "observed_at")
                or normalized["outcome_id"] in correction_ids):
            raise ContractError("experiment provenance correction chain is inconsistent")
        correction_ids.add(normalized["outcome_id"])
        hashes.append(_digest(normalized))
    if provenance["correction_sha256"] != hashes:
        raise ContractError("experiment provenance correction hashes are inconsistent")
    return provenance
