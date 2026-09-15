"""Strict, side-effect-free contracts for the two fixed engineering workflows."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
import re
from typing import Any

from .contracts import ContractError
from .store import canonical_json
from .validation import validate_task


MAX_REVIEW_BYTES = 512 * 1024
MAX_FINDINGS = 100
MAX_TEXT_CHARS = 20_000
MAX_PREVIEW_CHARS = 64 * 1024
FINDING_SEVERITIES = {"critical", "high", "medium", "low"}
CHECK_STATUSES = {"passed", "failed", "timed_out", "launch_failed"}
REVIEW_MODES = {"standard", "adversarial"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT_OID = re.compile(r"[0-9a-f]{40}\Z")


def _exact(
    value: Any,
    fields: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    unknown, missing = set(value) - fields, fields - set(value)
    if unknown or missing:
        raise ContractError(
            f"{label} fields invalid: unknown={sorted(unknown)} "
            f"missing={sorted(missing)}"
        )
    return value


def _text(value: Any, label: str, *, maximum: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty string")
    if len(value) > maximum:
        raise ContractError(f"{label} exceeds its size limit")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ContractError(f"{label} must be a lowercase SHA-256")
    return value


def _commit_oid(value: Any, label: str) -> str:
    if not isinstance(value, str) or _COMMIT_OID.fullmatch(value) is None:
        raise ContractError(f"{label} must be a full lowercase commit OID")
    return value


def _relative_path(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value
            or "\0" in value):
        raise ContractError(f"{label} must be a canonical repository-relative path")
    path = PurePosixPath(value)
    normalized = path.as_posix()
    if (path.is_absolute() or ".." in path.parts or normalized in {"", "."}
            or normalized != value):
        raise ContractError(f"{label} must be a canonical repository-relative path")
    return normalized


def _inside_scope(path: str, scopes: list[str]) -> bool:
    for raw_scope in scopes:
        scope = PurePosixPath(raw_scope).as_posix().rstrip("/") or "."
        if scope == "." or path == scope or path.startswith(f"{scope}/"):
            return True
    return False


def _strict_json_object(payload: bytes | str, label: str) -> dict[str, Any]:
    if isinstance(payload, str):
        encoded = payload.encode()
    elif isinstance(payload, bytes):
        encoded = payload
    else:
        raise ContractError(f"{label} must be UTF-8 JSON bytes or text")
    if len(encoded) > MAX_REVIEW_BYTES:
        raise ContractError(f"{label} exceeds its byte limit")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ContractError(f"{label} contains duplicate key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ContractError(f"{label} contains non-finite number: {value}")

    try:
        value = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label} must contain one JSON object")
    return value


def _workspace_identity(workspace: dict[str, Any]) -> tuple[str, str, str]:
    if not isinstance(workspace, dict):
        raise ContractError("workspace snapshot must be an object")
    return (
        _sha256(workspace.get("candidate_sha256"), "workspace candidate_sha256"),
        _commit_oid(workspace.get("base_oid"), "workspace base_oid"),
        _commit_oid(workspace.get("target_oid"), "workspace target_oid"),
    )


def review_mode(task: dict[str, Any]) -> str:
    mode = task.get("review", {}).get("mode", "standard")
    if mode not in REVIEW_MODES:
        raise ContractError("review mode is invalid")
    return mode


def validate_review_document(
    value: dict[str, Any],
    task: dict[str, Any],
    workspace: dict[str, Any],
) -> dict[str, Any]:
    """Validate model output and bind it to the exact frozen candidate."""
    validate_task(task)
    if task["workflow"] != "branch-review":
        raise ContractError("review document requires a branch-review task")
    document = _exact(value, {
        "schema_version", "candidate_sha256", "base_oid", "target_oid",
        "review_mode", "verdict", "summary", "findings",
    }, "review document")
    if document["schema_version"] != 1 or type(document["schema_version"]) is not int:
        raise ContractError("review document schema_version is invalid")
    candidate_sha256, base_oid, target_oid = _workspace_identity(workspace)
    if _sha256(document["candidate_sha256"], "review candidate_sha256") != candidate_sha256:
        raise ContractError("review targets a different candidate hash")
    if _commit_oid(document["base_oid"], "review base_oid") != base_oid:
        raise ContractError("review targets a different base commit")
    if _commit_oid(document["target_oid"], "review target_oid") != target_oid:
        raise ContractError("review targets a different target commit")
    if document["review_mode"] != review_mode(task):
        raise ContractError("review mode does not match the frozen task")
    if document["verdict"] not in {"clean", "findings"}:
        raise ContractError("review verdict must be clean or findings")
    _text(document["summary"], "review summary")
    findings = document["findings"]
    if not isinstance(findings, list) or len(findings) > MAX_FINDINGS:
        raise ContractError("review findings must be a bounded array")
    seen = set()
    for finding in findings:
        item = _exact(finding, {
            "id", "severity", "title", "description", "path",
            "start_line", "end_line", "evidence",
        }, "review finding")
        finding_id = _text(item["id"], "finding id", maximum=200)
        if finding_id in seen:
            raise ContractError(f"review finding id is duplicated: {finding_id}")
        seen.add(finding_id)
        if item["severity"] not in FINDING_SEVERITIES:
            raise ContractError("review finding severity is invalid")
        _text(item["title"], "finding title", maximum=500)
        _text(item["description"], "finding description")
        finding_path = _relative_path(item["path"], "finding path")
        if not _inside_scope(finding_path, task["scope"]["read_paths"]):
            raise ContractError("review finding path is outside the declared read scope")
        if type(item["start_line"]) is not int or item["start_line"] < 1:
            raise ContractError("finding start_line must be a positive integer")
        if type(item["end_line"]) is not int or item["end_line"] < item["start_line"]:
            raise ContractError("finding end_line must not precede start_line")
        _text(item["evidence"], "finding evidence")
    if (document["verdict"] == "clean") != (not findings):
        raise ContractError("review verdict and findings disagree")
    return json.loads(canonical_json(document))


def decode_review_document(
    payload: bytes | str,
    task: dict[str, Any],
    workspace: dict[str, Any],
) -> dict[str, Any]:
    return validate_review_document(
        _strict_json_object(payload, "review output"), task, workspace,
    )


def _validate_stream(value: Any, label: str) -> None:
    stream = _exact(value, {
        "preview", "captured_bytes", "total_bytes", "truncated", "full_sha256",
    }, label)
    if not isinstance(stream["preview"], str) or len(stream["preview"]) > MAX_PREVIEW_CHARS:
        raise ContractError(f"{label} preview exceeds its size limit")
    for field in ("captured_bytes", "total_bytes"):
        if type(stream[field]) is not int or stream[field] < 0:
            raise ContractError(f"{label} {field} must be a non-negative integer")
    if stream["captured_bytes"] > stream["total_bytes"]:
        raise ContractError(f"{label} captured bytes exceed total bytes")
    if type(stream["truncated"]) is not bool:
        raise ContractError(f"{label} truncated must be boolean")
    if stream["truncated"] != (stream["captured_bytes"] < stream["total_bytes"]):
        raise ContractError(f"{label} truncation metadata is inconsistent")
    _sha256(stream["full_sha256"], f"{label} full_sha256")


def validate_check_results(
    values: list[dict[str, Any]],
    task: dict[str, Any],
    workspace: dict[str, Any],
) -> list[dict[str, Any]]:
    """Validate check evidence against the host-supplied immutable check plan."""
    validate_task(task)
    if not isinstance(values, list) or len(values) != len(task["checks"]):
        raise ContractError("check results do not match the declared check count")
    candidate_sha256, _, target_oid = _workspace_identity(workspace)
    normalized = []
    for configured, supplied in zip(task["checks"], values):
        result = _exact(supplied, {
            "schema_version", "candidate_sha256", "target_oid", "id", "argv",
            "cwd", "required_to_pass", "status", "returncode", "error_code",
            "duration_ms", "stdout", "stderr",
        }, "check result")
        if result["schema_version"] != 1 or type(result["schema_version"]) is not int:
            raise ContractError("check result schema_version is invalid")
        if _sha256(result["candidate_sha256"], "check candidate_sha256") != candidate_sha256:
            raise ContractError("check result targets a different candidate hash")
        if _commit_oid(result["target_oid"], "check target_oid") != target_oid:
            raise ContractError("check result targets a different target commit")
        for field in ("id", "argv", "cwd", "required_to_pass"):
            if result[field] != configured[field]:
                raise ContractError(f"check result changes declared field: {field}")
        if result["status"] not in CHECK_STATUSES:
            raise ContractError("check result status is invalid")
        if type(result["duration_ms"]) is not int or result["duration_ms"] < 0:
            raise ContractError("check duration_ms must be a non-negative integer")
        status, returncode, error_code = (
            result["status"], result["returncode"], result["error_code"]
        )
        if status == "passed" and (returncode != 0 or error_code is not None):
            raise ContractError("passing check result is inconsistent")
        if status == "failed" and (
            type(returncode) is not int or returncode == 0 or error_code is not None
        ):
            raise ContractError("failed check result is inconsistent")
        if status == "timed_out" and error_code != "TIMEOUT":
            raise ContractError("timed-out check result is inconsistent")
        if status == "launch_failed" and (
            returncode is not None or error_code != "CLI_ERROR"
        ):
            raise ContractError("launch-failed check result is inconsistent")
        if status in {"timed_out", "launch_failed"} and returncode is not None:
            raise ContractError("incomplete check cannot report a return code")
        _validate_stream(result["stdout"], "check stdout")
        _validate_stream(result["stderr"], "check stderr")
        normalized.append(json.loads(canonical_json(result)))
    return normalized


def evaluate_branch_review(
    task: dict[str, Any],
    workspace: dict[str, Any],
    review: dict[str, Any],
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Derive non-overridable gates without pretending to make the lead decision."""
    normalized_review = validate_review_document(review, task, workspace)
    normalized_checks = validate_check_results(checks, task, workspace)
    required_failures = [
        result["id"] for result in normalized_checks
        if result["required_to_pass"] and result["status"] != "passed"
    ]
    report_only_failures = [
        result["id"] for result in normalized_checks
        if not result["required_to_pass"] and result["status"] != "passed"
    ]
    criteria = []
    for criterion in task["acceptance"]:
        kind = criterion["evidence_kind"]
        if kind == "review":
            status, evidence = "evidence_available", ["review.json"]
        elif kind == "check" and normalized_checks:
            status = "evidence_available"
            evidence = [f"check:{result['id']}" for result in normalized_checks]
        else:
            status, evidence = "pending_lead", []
        criteria.append({
            "id": criterion["id"],
            "evidence_kind": kind,
            "status": status,
            "evidence": evidence,
        })
    candidate_sha256, base_oid, target_oid = _workspace_identity(workspace)
    return {
        "schema_version": 1,
        "candidate_sha256": candidate_sha256,
        "base_oid": base_oid,
        "target_oid": target_oid,
        "review_verdict": normalized_review["verdict"],
        "required_checks_passed": not required_failures,
        "required_failures": required_failures,
        "report_only_failures": report_only_failures,
        "accept_allowed": not required_failures,
        "accept_blockers": [
            f"required_check_failed:{check_id}" for check_id in required_failures
        ],
        "criteria": criteria,
    }


def apply_lead_disposition(
    evaluation: dict[str, Any],
    disposition: str,
    *,
    revisions_used: int,
    max_revisions: int,
) -> dict[str, Any]:
    """Apply the fixed lead gate without allowing prose to bypass evidence."""
    if disposition not in {"accept", "revise", "reject"}:
        raise ContractError("lead disposition is invalid")
    if (type(revisions_used) is not int or revisions_used < 0
            or type(max_revisions) is not int or max_revisions < 0):
        raise ContractError("revision counters must be non-negative integers")
    if disposition == "accept":
        if evaluation.get("accept_allowed") is not True:
            raise ContractError("lead acceptance is blocked by required evidence")
        action, terminal_state = "complete", "succeeded"
    elif disposition == "reject":
        action, terminal_state = "complete", "failed"
    elif revisions_used >= max_revisions:
        action, terminal_state = "budget_exhausted", "failed"
    else:
        action, terminal_state = "repeat_review", None
    return {
        "disposition": disposition,
        "action": action,
        "terminal_state": terminal_state,
        "next_revision": revisions_used + 1 if action == "repeat_review" else revisions_used,
    }


def build_review_prompt(task: dict[str, Any], workspace: dict[str, Any]) -> str:
    """Build a deterministic, read-only prompt from host-authorized fields only."""
    validate_task(task)
    if task["workflow"] != "branch-review":
        raise ContractError("review prompt requires a branch-review task")
    candidate_sha256, base_oid, target_oid = _workspace_identity(workspace)
    mode = review_mode(task)
    focus = task.get("review", {}).get("focus")
    assignment = {
        "goal": task["goal"],
        "acceptance": task["acceptance"],
        "scope": task["scope"]["read_paths"],
        "review_mode": mode,
        "focus": focus,
        "base_oid": base_oid,
        "target_oid": target_oid,
        "candidate_sha256": candidate_sha256,
    }
    finding_shape = {
        "id": "finding-id",
        "severity": "critical|high|medium|low",
        "title": "short title",
        "description": "actionable explanation",
        "path": "repository/relative/path",
        "start_line": 1,
        "end_line": 1,
        "evidence": "specific supporting evidence",
    }
    return "\n".join([
        "You are the read-only reviewer for one frozen Git candidate.",
        "Do not edit files, run mutating commands, publish, delegate, or broaden scope.",
        "Inspect only the declared scope and compare the exact base and target commits.",
        "A valid critical finding is useful output; do not hide findings to claim success.",
        "Return exactly one JSON object and no Markdown or surrounding prose.",
        "The object must contain these exact top-level fields:",
        "schema_version, candidate_sha256, base_oid, target_oid, review_mode, verdict, summary, findings.",
        "verdict must be clean with an empty findings array, or findings with at least one finding.",
        "Each finding must have exactly this shape:",
        canonical_json(finding_shape),
        "Frozen assignment:",
        canonical_json(assignment),
    ])
