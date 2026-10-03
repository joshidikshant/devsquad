"""Predeclared workflow mechanics comparison, separate from R3 eligibility.

Public fixture runs support observable overhead/failure mechanics, not native
quality. No fabricated score, profile promotion or automatic Council authority
can be produced by this bounded comparison.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from .contracts import ContractError
from .council import digest, exact, sha, text
from .store import canonical_json, ConflictError

ARMS = ("control", "council")
CONTRACT_FIELDS = ("project", "goal", "acceptance", "checks", "scope")


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _task_version(task: dict) -> dict:
    if set(task["routing"]) != {"profiles", "policy"}:
        raise ContractError("Workflow comparison requires embedded frozen routing documents")
    module = "council.py" if task["workflow"] == "council-decision" else "workflows.py"
    return {"task_sha256": digest(task), "profiles_sha256": digest(task["routing"]["profiles"]),
            "policy_sha256": digest(task["routing"]["policy"]), "prompt_module": module,
            "prompt_module_sha256": _file_sha(Path(__file__).with_name(module))}


def predeclare(path: Path, cases: list[dict]) -> dict:
    """Create once before runs. Each case fixes both task versions and split."""
    if not isinstance(cases, list) or not 2 <= len(cases) <= 16:
        raise ContractError("Comparison requires bounded matched and held-out cases")
    declared, identifiers, splits, contracts = [], set(), set(), set()
    for case in cases:
        exact(case, {"id", "split", "control", "council"}, "workflow comparison case")
        text(case["id"], "comparison case ID")
        if case["id"] in identifiers or case["split"] not in {"matched", "heldout"}:
            raise ContractError("Comparison case IDs/splits are invalid")
        identifiers.add(case["id"])
        splits.add(case["split"])
        control, council = case["control"], case["council"]
        if control["workflow"] != "branch-review" or council["workflow"] != "council-decision":
            raise ContractError("Comparison arms must be branch-review and Council")
        contract = {field: control[field] for field in CONTRACT_FIELDS}
        if contract != {field: council[field] for field in CONTRACT_FIELDS}:
            raise ContractError("Comparison arms do not share the declared input/check contract")
        contract_sha256 = digest(contract)
        if contract_sha256 in contracts:
            raise ContractError("Comparison cases must have unique input contracts; repetitions are not held-out independence")
        contracts.add(contract_sha256)
        for task in (control, council):
            for field in ("base_ref", "target_ref"):
                value = task["project"][field]
                if not isinstance(value, str) or len(value) not in {40, 64} or any(c not in "0123456789abcdef" for c in value):
                    raise ContractError("Comparison must pin exact Git commit IDs")
        declared.append({"id": case["id"], "split": case["split"], "input_contract_sha256": contract_sha256,
                         **{arm: _task_version(case[arm]) for arm in ARMS}})
    if splits != {"matched", "heldout"}:
        raise ContractError("Comparison must predeclare both matched and held-out cases")
    plan = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "scope": "public_fixture_mechanics", "automatic_enabled": False,
            "quality_gate": {"accepted_quality": "unmeasured", "escaped_defects": "unmeasured", "rework": "unmeasured"},
            "cases": declared, "decision_rule": "inconclusive_without_independent_native_quality_evidence"}
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical_json(plan).encode())
        stream.flush()
        os.fsync(stream.fileno())
    return {"path": str(path), "sha256": _file_sha(path)}


def report(store, declaration: dict, pairs: list[dict]) -> dict:
    """Validate actual terminal public receipts, then save an immutable report."""
    exact(declaration, {"path", "sha256"}, "comparison declaration reference")
    sha(declaration["sha256"])
    path = Path(declaration["path"])
    if path.is_symlink() or _file_sha(path) != declaration["sha256"]:
        raise ConflictError("Workflow comparison predeclaration changed")
    plan = json.loads(path.read_bytes())
    if plan.get("scope") != "public_fixture_mechanics" or plan.get("automatic_enabled") is not False:
        raise ContractError("Comparison declaration is not the bounded public fixture gate")
    if len({case["input_contract_sha256"] for case in plan["cases"]}) != len(plan["cases"]):
        raise ContractError("Comparison repetitions cannot be relabelled independent matched/held-out cases")
    if not isinstance(pairs, list) or len(pairs) != len(plan["cases"]):
        raise ContractError("Comparison requires every predeclared case")
    assigned = {item["id"]: item for item in pairs}
    if len(assigned) != len(pairs) or set(assigned) != {item["id"] for item in plan["cases"]}:
        raise ContractError("Comparison pairs differ from the predeclared cases")
    created = datetime.fromisoformat(plan["created_at"])
    results, seen = [], set()
    for case in plan["cases"]:
        pair = assigned[case["id"]]
        exact(pair, {"id", "control", "council"}, "comparison pair")
        arms = {}
        for arm in ARMS:
            run_id = pair[arm]
            if run_id in seen:
                raise ContractError("A comparison run cannot be reused across arms/cases")
            seen.add(run_id)
            run = store.run(run_id)
            if datetime.fromisoformat(run["created_at"]) <= created or run["state"] not in {"succeeded", "failed", "cancelled"}:
                raise ConflictError("Comparison run was not terminal after its predeclaration")
            snapshot = json.loads(run["mutable_snapshot"])
            task = snapshot["task"]
            requested = json.loads(run["submitted_request"])["task"]
            if digest(requested) != case[arm]["task_sha256"]:
                raise ConflictError("Comparison run task differs from its declared arm")
            if digest({field: task[field] for field in CONTRACT_FIELDS}) != case["input_contract_sha256"]:
                raise ConflictError("Comparison actual frozen input contract differs")
            if (snapshot["routing"]["profile_registry"]["sha256"] != case[arm]["profiles_sha256"]
                    or snapshot["routing"]["policy"]["sha256"] != case[arm]["policy_sha256"]
                    or _file_sha(Path(run["package_path"]) / "devsquad" / case[arm]["prompt_module"]) != case[arm]["prompt_module_sha256"]):
                raise ConflictError("Comparison frozen profile/policy/prompt version differs")
            if (arm == "council" and snapshot.get("council_fixture") is None
                    or arm == "control" and "internal_review_fixture" not in snapshot):
                raise ContractError("This comparison gate accepts actual public fixture mechanics only")
            artifact = store.artifact_named(run_id, "receipt.json")
            if artifact is None:
                raise ConflictError("Comparison run has no terminal workflow receipt")
            from .council_runtime import verified_artifact
            raw = verified_artifact(store, run_id, "receipt.json", artifact["sha256"])
            receipt = json.loads(raw)
            if receipt["run_id"] != run_id or receipt["state"] != run["state"]:
                raise ConflictError("Comparison receipt identity/state differs")
            attempts = receipt["attempts"]
            accounting = receipt.get("accounting", receipt)
            arms[arm] = {"run_id": run_id, "receipt": {"artifact_id": artifact["id"], "sha256": artifact["sha256"]},
                "state": run["state"], "lead_disposition": receipt["lead"]["disposition"],
                "runtime_package_sha256": run["package_digest"],
                "input_contract_sha256": case["input_contract_sha256"], "versions": case[arm],
                "actual_prompt_sha256": [item.get("prompt_sha256") for item in attempts],
                "candidate": receipt["candidate"], "brief_sha256": receipt.get("brief_sha256"),
                "accepted_quality": None, "escaped_defects": None, "rework": None,
                "quality_missingness": "fixture mechanics and host acceptance are not independent native quality measurements",
                "execution_elapsed_ms": store._execution_elapsed_ms(run_id, run, datetime.now(timezone.utc)),
                "worker_invocations": accounting["worker_invocations"],
                "native_usage": [item.get("usage") for item in attempts],
                "native_model_requests": accounting.get("native_model_requests"), "native_quota": None,
                "host_usage": None, "identity_scope": "all_fixture"}
        results.append({"id": case["id"], "split": case["split"], "arms": arms})
    value = {"schema_version": 1, "predeclaration_sha256": declaration["sha256"], "cases": results,
             "conclusion": "inconclusive", "quality_benefit_supported": False, "automatic_enabled": False,
             "limitations": ["Controlled process mechanics only", "No independent native accepted-quality/escaped-defect/rework observations",
                             "Workflow prompts and invocation count differ; this is not R3 single-binding eligibility"]}
    # Terminal workflow ledgers stay immutable. This separately versioned
    # comparison artifact references their exact receipts, never rewrites them.
    destination = path.parent / (declaration["sha256"] + ".workflow-comparison.json")
    encoded = canonical_json(value).encode()
    if destination.exists():
        if destination.is_symlink() or destination.read_bytes() != encoded:
            raise ConflictError("Workflow comparison report is already frozen differently")
    else:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    return {"report": value, "path": str(destination), "sha256": digest(value)}
