"""Strict, side-effect-free contracts for the two fixed engineering workflows."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any

from .contracts import ContractError
from .store import canonical_json
from .validation import validate_task


MAX_REVIEW_BYTES = 512 * 1024
MAX_LEAD_BYTES = 128 * 1024
MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_FINDINGS = 100
MAX_TEXT_CHARS = 20_000
MAX_PREVIEW_CHARS = 1024
FINDING_SEVERITIES = {"critical", "high", "medium", "low"}
CHECK_STATUSES = {"passed", "failed", "timed_out", "launch_failed"}
REVIEW_MODES = {"standard", "adversarial"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT_OID = re.compile(r"[0-9a-f]{40}\Z")


def review_output_schema() -> dict[str, Any]:
    """Return the strict native structured-output schema for one review."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version", "candidate_sha256", "base_oid", "target_oid",
            "review_mode", "verdict", "summary", "findings",
        ],
        "properties": {
            "schema_version": {"type": "integer", "enum": [1]},
            "candidate_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "base_oid": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
            "target_oid": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
            "review_mode": {
                "type": "string", "enum": ["standard", "adversarial"],
            },
            "verdict": {"type": "string", "enum": ["clean", "findings"]},
            "summary": {"type": "string"},
            "findings": {
                "type": "array",
                "maxItems": MAX_FINDINGS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "id", "severity", "title", "description", "path",
                        "start_line", "end_line", "evidence",
                    ],
                    "properties": {
                        "id": {"type": "string"},
                        "severity": {
                            "type": "string", "enum": sorted(FINDING_SEVERITIES),
                        },
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "path": {"type": "string"},
                        "start_line": {"type": "integer", "minimum": 1},
                        "end_line": {"type": "integer", "minimum": 1},
                        "evidence": {"type": "string"},
                    },
                },
            },
        },
    }


def lead_output_schema() -> dict[str, Any]:
    """Return the strict native structured-output schema for one lead choice."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version", "candidate_sha256", "disposition", "reason",
        ],
        "properties": {
            "schema_version": {"type": "integer", "enum": [1]},
            "candidate_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "disposition": {
                "type": "string", "enum": ["accept", "revise", "reject"],
            },
            "reason": {"type": "string"},
        },
    }


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


def _strict_json_object(
    payload: bytes | str,
    label: str,
    *,
    maximum: int = MAX_REVIEW_BYTES,
) -> dict[str, Any]:
    if isinstance(payload, str):
        encoded = payload.encode()
    elif isinstance(payload, bytes):
        encoded = payload
    else:
        raise ContractError(f"{label} must be UTF-8 JSON bytes or text")
    if len(encoded) > maximum:
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
    if task["workflow"] not in {"branch-review", "issue-delivery"}:
        raise ContractError("review document requires a reviewable workflow")
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
    if task["workflow"] not in {"branch-review", "issue-delivery"}:
        raise ContractError("review prompt requires a reviewable workflow")
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


def _frozen_attempt_selection(
    snapshot: dict[str, Any],
    role: str,
    actual: Any,
) -> dict[str, Any]:
    try:
        routed = snapshot["routing"]["roles"][role]
        candidates = [routed["selected"], *routed.get("fallbacks", [])]
    except (KeyError, TypeError) as exc:
        raise ContractError(f"frozen {role} selection is missing") from exc
    for candidate in candidates:
        if canonical_json(actual) == canonical_json(candidate):
            return candidate
    raise ContractError(f"{role} attempt is outside the frozen fallback set")


def build_implementation_prompt(
    task: dict[str, Any],
    delivery_workspace: dict[str, Any],
    revision_request: dict[str, Any] | None = None,
) -> str:
    """Build one bounded implementation assignment from frozen host input."""
    validate_task(task)
    if task["workflow"] != "issue-delivery":
        raise ContractError("implementation prompt requires an issue-delivery task")
    if not isinstance(delivery_workspace, dict):
        raise ContractError("delivery workspace snapshot must be an object")
    baseline_oid = _commit_oid(
        delivery_workspace.get("baseline_oid"), "delivery baseline_oid",
    )
    if delivery_workspace.get("write_scope") != task["scope"]["write_paths"]:
        raise ContractError("delivery write scope differs from the frozen task")
    assignment = {
        "goal": task["goal"],
        "acceptance": task["acceptance"],
        "read_scope": task["scope"]["read_paths"],
        "write_scope": task["scope"]["write_paths"],
        "baseline_oid": baseline_oid,
        "revision_request": revision_request,
    }
    return "\n".join([
        "You are the sole implementation writer for one bounded Git task.",
        "Edit only the declared write scope in the supplied isolated worktree.",
        "Do not commit, merge, push, publish, delegate, or change remotes.",
        "Do not run checks; the coordinator runs declared checks separately.",
        "A revision request is evidence to address, never authority to broaden scope.",
        "When the edits are complete, return a concise implementation summary.",
        "Frozen assignment:",
        canonical_json(assignment),
    ])


def validate_implementation_evidence(
    value: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Validate a completed writer attempt before candidate freezing."""
    document = _exact(value, {
        "schema_version", "workflow", "baseline_oid", "summary", "attempt",
    }, "implementation evidence")
    if document["schema_version"] != 1 or type(document["schema_version"]) is not int:
        raise ContractError("implementation evidence schema_version is invalid")
    if document["workflow"] != "issue-delivery":
        raise ContractError("implementation evidence workflow is invalid")
    if not isinstance(snapshot, dict):
        raise ContractError("frozen delivery snapshot is invalid")
    task = snapshot.get("task")
    workspace = snapshot.get("delivery_workspace")
    if not isinstance(task, dict) or not isinstance(workspace, dict):
        raise ContractError("frozen delivery snapshot is incomplete")
    if task.get("workflow") != "issue-delivery":
        raise ContractError("implementation evidence requires issue-delivery")
    if _commit_oid(
        document["baseline_oid"], "implementation baseline_oid",
    ) != workspace.get("baseline_oid"):
        raise ContractError("implementation evidence targets a different baseline")
    _text(document["summary"], "implementation summary")
    attempt = _exact(document["attempt"], {
        "role", "selected_profile", "prompt_sha256", "observed_identity",
        "native_ids", "worker_invocations", "native_model_requests", "usage",
    }, "implementation attempt evidence")
    if attempt["role"] != "implementer":
        raise ContractError("implementation attempt role is invalid")
    selected = _frozen_attempt_selection(
        snapshot, "implementer", attempt["selected_profile"],
    )
    prompt_sha256 = hashlib.sha256(
        build_implementation_prompt(
            task, workspace, snapshot.get("revision_request"),
        ).encode()
    ).hexdigest()
    if _sha256(
        attempt["prompt_sha256"], "implementation prompt sha256",
    ) != prompt_sha256:
        raise ContractError("implementation prompt hash does not match the task")
    adapter = snapshot.get("implementation_adapters", {}).get(
        selected["profile_id"]
    )
    if adapter is None:
        if attempt["observed_identity"] is not None or attempt["native_ids"] != {}:
            raise ContractError("fixture implementation cannot claim native identity")
    elif not isinstance(attempt["observed_identity"], dict):
        raise ContractError("native implementation identity is missing")
    if attempt["worker_invocations"] != 1 or type(attempt["worker_invocations"]) is not int:
        raise ContractError("implementation worker invocation accounting is invalid")
    native_requests = attempt["native_model_requests"]
    if native_requests is not None and (
            type(native_requests) is not int or native_requests < 0):
        raise ContractError("implementation native request count is invalid")
    usage = _exact(attempt["usage"], {
        "input_tokens", "output_tokens", "total_tokens", "source",
    }, "implementation usage")
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        if usage[field] is not None and (
                type(usage[field]) is not int or usage[field] < 0):
            raise ContractError("implementation token usage is invalid")
    if usage["source"] not in {"native_reported", "unavailable"}:
        raise ContractError("implementation usage source is invalid")
    if usage["source"] == "unavailable" and any(
            usage[field] is not None
            for field in ("input_tokens", "output_tokens", "total_tokens")
    ):
        raise ContractError("unavailable implementation usage cannot invent tokens")
    return json.loads(canonical_json(document))


def make_implementation_evidence(
    snapshot: dict[str, Any],
    summary: str,
) -> dict[str, Any]:
    selected = snapshot["routing"]["roles"]["implementer"]["selected"]
    document = {
        "schema_version": 1,
        "workflow": "issue-delivery",
        "baseline_oid": snapshot["delivery_workspace"]["baseline_oid"],
        "summary": summary,
        "attempt": {
            "role": "implementer",
            "selected_profile": selected,
            "prompt_sha256": hashlib.sha256(
                build_implementation_prompt(
                    snapshot["task"], snapshot["delivery_workspace"],
                    snapshot.get("revision_request"),
                ).encode()
            ).hexdigest(),
            "observed_identity": None,
            "native_ids": {},
            "worker_invocations": 1,
            "native_model_requests": None,
            "usage": {
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "source": "unavailable",
            },
        },
    }
    return validate_implementation_evidence(document, snapshot)


def _frozen_role_adapter(
    snapshot: dict[str, Any],
    role: str,
    selection: dict[str, Any],
) -> Any:
    plural_key = "review_adapters" if role == "reviewer" else "lead_adapters"
    singular_key = "review_adapter" if role == "reviewer" else "lead_adapter"
    adapters = snapshot.get(plural_key)
    if adapters is not None:
        if not isinstance(adapters, dict):
            raise ContractError(f"frozen {role} adapters are invalid")
        adapter = adapters.get(selection["profile_id"])
        if adapter is None:
            raise ContractError(f"frozen {role} fallback adapter is missing")
        return adapter
    return snapshot.get(singular_key)


def validate_headless_lead_choice(
    value: dict[str, Any],
    packet: dict[str, Any],
) -> dict[str, Any]:
    """Validate a headless lead's bounded choice against trusted review gates."""
    choice = _exact(value, {
        "schema_version", "candidate_sha256", "disposition", "reason",
    }, "headless lead choice")
    if choice["schema_version"] != 1 or type(choice["schema_version"]) is not int:
        raise ContractError("headless lead choice schema_version is invalid")
    if _sha256(
        choice["candidate_sha256"], "headless lead candidate_sha256",
    ) != packet.get("candidate_sha256"):
        raise ContractError("headless lead choice targets a different candidate")
    disposition = choice["disposition"]
    if disposition not in {"accept", "revise", "reject"}:
        raise ContractError("headless lead disposition is invalid")
    reason = choice["reason"]
    if not isinstance(reason, str) or len(reason) > MAX_TEXT_CHARS:
        raise ContractError("headless lead reason is invalid")
    if disposition in {"revise", "reject"} and not reason.strip():
        raise ContractError("headless lead revise/reject requires a reason")
    if disposition == "accept" and packet.get("evaluation", {}).get("accept_allowed") is not True:
        raise ContractError("headless lead acceptance is blocked by required evidence")
    return json.loads(canonical_json(choice))


def decode_headless_lead_choice(
    payload: bytes | str,
    packet: dict[str, Any],
) -> dict[str, Any]:
    return validate_headless_lead_choice(
        _strict_json_object(payload, "headless lead output", maximum=MAX_LEAD_BYTES),
        packet,
    )


def build_lead_prompt(task: dict[str, Any], packet: dict[str, Any]) -> str:
    """Build the single frozen evidence-disposition prompt for a headless lead."""
    validate_task(task)
    if (task["workflow"] not in {"branch-review", "issue-delivery"}
            or task["lead"]["mode"] != "headless"):
        raise ContractError("headless lead prompt requires a reviewable workflow")
    if not isinstance(packet, dict):
        raise ContractError("headless lead packet must be an object")
    assignment = {
        "goal": task["goal"],
        "acceptance": task["acceptance"],
        "candidate_sha256": packet.get("candidate_sha256"),
        "review": packet.get("review"),
        "checks": packet.get("checks"),
        "evaluation": packet.get("evaluation"),
    }
    return "\n".join([
        f"You are the single read-only lead for one frozen {task['workflow']} handoff.",
        "Do not edit files, run commands, publish, delegate, or broaden scope.",
        "Choose exactly one disposition: accept, revise, or reject.",
        "Acceptance is forbidden when evaluation.accept_allowed is false.",
        "Return exactly one JSON object and no Markdown or surrounding prose.",
        "The object must contain exactly schema_version, candidate_sha256, disposition, reason.",
        "Frozen handoff evidence:",
        canonical_json(assignment),
    ])


def validate_headless_lead_evidence(
    value: dict[str, Any],
    snapshot: dict[str, Any],
    handoff: dict[str, Any],
) -> dict[str, Any]:
    """Validate one provider-bound headless lead attempt and its exact handoff."""
    document = _exact(value, {
        "schema_version", "workflow", "candidate_sha256", "handoff_id",
        "packet_sha256", "choice", "attempt",
    }, "headless lead evidence")
    if document["schema_version"] != 1 or type(document["schema_version"]) is not int:
        raise ContractError("headless lead evidence schema_version is invalid")
    if not isinstance(snapshot, dict) or not isinstance(handoff, dict):
        raise ContractError("headless lead frozen inputs are invalid")
    task = snapshot.get("task")
    packet = handoff.get("packet")
    if not isinstance(task, dict) or not isinstance(packet, dict):
        raise ContractError("headless lead frozen inputs are incomplete")
    if (task.get("workflow") not in {"branch-review", "issue-delivery"}
            or document["workflow"] != task["workflow"]
            or packet.get("workflow") != task["workflow"]):
        raise ContractError("headless lead evidence workflow is invalid")
    if task.get("lead", {}).get("mode") != "headless":
        raise ContractError("headless lead evidence requires headless mode")
    handoff_id = _text(document["handoff_id"], "headless lead handoff id", maximum=200)
    if handoff_id != handoff.get("handoff_id"):
        raise ContractError("headless lead evidence targets a different handoff")
    packet_sha256 = hashlib.sha256(canonical_json(packet).encode()).hexdigest()
    if _sha256(document["packet_sha256"], "headless lead packet sha256") != packet_sha256:
        raise ContractError("headless lead evidence changes the handoff packet")
    if handoff.get("packet_sha256") != packet_sha256:
        raise ContractError("headless lead input packet hash is invalid")
    choice = validate_headless_lead_choice(document["choice"], packet)
    if _sha256(
        document["candidate_sha256"], "headless lead evidence candidate_sha256",
    ) != choice["candidate_sha256"]:
        raise ContractError("headless lead evidence changes the candidate")

    attempt = _exact(document["attempt"], {
        "role", "selected_profile", "prompt_sha256", "observed_identity",
        "native_ids", "worker_invocations", "native_model_requests", "usage",
    }, "headless lead attempt evidence")
    if attempt["role"] != "lead":
        raise ContractError("headless lead attempt role is invalid")
    frozen_lead = _frozen_attempt_selection(
        snapshot, "lead", attempt["selected_profile"],
    )
    prompt_sha256 = hashlib.sha256(build_lead_prompt(task, packet).encode()).hexdigest()
    if _sha256(attempt["prompt_sha256"], "headless lead prompt sha256") != prompt_sha256:
        raise ContractError("headless lead prompt hash does not match the handoff")

    adapter = _frozen_role_adapter(snapshot, "lead", frozen_lead)
    observed = attempt["observed_identity"]
    native_ids = attempt["native_ids"]
    if adapter is None:
        if observed is not None or native_ids != {}:
            raise ContractError("fixture lead cannot claim a native observed identity")
    else:
        if not isinstance(adapter, dict):
            raise ContractError("frozen lead adapter is invalid")
        identity = _exact(observed, {
            "harness", "harness_version", "model_provider", "model_id", "effort",
            "permission_policy", "verification",
        }, "observed lead identity")
        expected_identity = {
            "harness": adapter.get("harness"),
            "harness_version": adapter.get("harness_version"),
            "model_provider": adapter.get("model_provider"),
            "model_id": frozen_lead["profile"]["model_id"],
            "effort": frozen_lead["profile"]["effort"]["value"],
            "permission_policy": frozen_lead["profile"]["permission_policy"],
            "verification": "verified",
        }
        if canonical_json(identity) != canonical_json(expected_identity):
            raise ContractError("observed lead identity does not match the frozen adapter")
        ids = _exact(native_ids, {"thread_id", "turn_id"}, "native lead ids")
        for field in ("thread_id", "turn_id"):
            _text(ids[field], f"native lead {field}", maximum=500)
    if attempt["worker_invocations"] != 1 or type(attempt["worker_invocations"]) is not int:
        raise ContractError("headless lead worker invocation accounting is invalid")
    native_requests = attempt["native_model_requests"]
    if native_requests is not None and (
            type(native_requests) is not int or native_requests < 0):
        raise ContractError("headless lead native request count is invalid")
    usage = _exact(attempt["usage"], {
        "input_tokens", "output_tokens", "total_tokens", "source",
    }, "headless lead usage")
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        if usage[field] is not None and (
                type(usage[field]) is not int or usage[field] < 0):
            raise ContractError("headless lead token usage is invalid")
    if usage["source"] not in {"native_reported", "unavailable"}:
        raise ContractError("headless lead usage source is invalid")
    if usage["source"] == "unavailable" and any(
            usage[field] is not None
            for field in ("input_tokens", "output_tokens", "total_tokens")
    ):
        raise ContractError("unavailable lead usage cannot invent token counts")
    return json.loads(canonical_json(document))


def make_headless_lead_evidence(
    snapshot: dict[str, Any],
    handoff: dict[str, Any],
    choice: dict[str, Any],
    *,
    observed_identity: dict[str, Any] | None = None,
    native_ids: dict[str, str] | None = None,
    native_model_requests: int | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    packet = handoff["packet"]
    normalized_choice = validate_headless_lead_choice(choice, packet)
    document = {
        "schema_version": 1,
        "workflow": snapshot["task"]["workflow"],
        "candidate_sha256": normalized_choice["candidate_sha256"],
        "handoff_id": handoff["handoff_id"],
        "packet_sha256": handoff["packet_sha256"],
        "choice": normalized_choice,
        "attempt": {
            "role": "lead",
            "selected_profile": snapshot["routing"]["roles"]["lead"]["selected"],
            "prompt_sha256": hashlib.sha256(
                build_lead_prompt(snapshot["task"], packet).encode()
            ).hexdigest(),
            "observed_identity": observed_identity,
            "native_ids": native_ids if native_ids is not None else {},
            "worker_invocations": 1,
            "native_model_requests": native_model_requests,
            "usage": usage if usage is not None else {
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "source": "unavailable",
            },
        },
    }
    return validate_headless_lead_evidence(document, snapshot, handoff)


def decode_headless_lead_evidence(
    payload: bytes | str,
    snapshot: dict[str, Any],
    handoff: dict[str, Any],
) -> dict[str, Any]:
    return validate_headless_lead_evidence(
        _strict_json_object(
            payload, "headless lead evidence", maximum=MAX_EVIDENCE_BYTES,
        ),
        snapshot,
        handoff,
    )


def validate_branch_review_evidence(
    value: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Recompute all derived gates before a worker result can become evidence."""
    document = _exact(value, {
        "schema_version", "workflow", "candidate_sha256", "base_oid", "target_oid",
        "review", "checks", "evaluation", "attempt",
    }, "branch review evidence")
    if document["schema_version"] != 1 or type(document["schema_version"]) is not int:
        raise ContractError("branch review evidence schema_version is invalid")
    if not isinstance(snapshot, dict):
        raise ContractError("frozen workflow snapshot must be an object")
    task, workspace = snapshot.get("task"), snapshot.get("workspace")
    if not isinstance(task, dict) or not isinstance(workspace, dict):
        raise ContractError("frozen workflow snapshot is incomplete")
    if (task.get("workflow") not in {"branch-review", "issue-delivery"}
            or document["workflow"] != task["workflow"]):
        raise ContractError("review evidence workflow is invalid")
    candidate_sha256, base_oid, target_oid = _workspace_identity(workspace)
    for field, expected, validator in (
        ("candidate_sha256", candidate_sha256, _sha256),
        ("base_oid", base_oid, _commit_oid),
        ("target_oid", target_oid, _commit_oid),
    ):
        if validator(document[field], f"evidence {field}") != expected:
            raise ContractError(f"branch review evidence changes frozen {field}")
    review = validate_review_document(document["review"], task, workspace)
    checks = validate_check_results(document["checks"], task, workspace)
    expected_evaluation = evaluate_branch_review(task, workspace, review, checks)
    if canonical_json(document["evaluation"]) != canonical_json(expected_evaluation):
        raise ContractError("branch review evaluation does not match derived gates")
    attempt = _exact(document["attempt"], {
        "role", "selected_profile", "prompt_sha256", "review_sha256",
        "observed_identity", "native_ids", "worker_invocations",
        "native_model_requests", "usage",
    }, "review attempt evidence")
    if attempt["role"] != "reviewer":
        raise ContractError("review attempt role is invalid")
    frozen_reviewer = _frozen_attempt_selection(
        snapshot, "reviewer", attempt["selected_profile"],
    )
    prompt_sha256 = hashlib.sha256(build_review_prompt(task, workspace).encode()).hexdigest()
    if _sha256(attempt["prompt_sha256"], "review prompt_sha256") != prompt_sha256:
        raise ContractError("review prompt hash does not match the frozen prompt")
    review_sha256 = hashlib.sha256(canonical_json(review).encode()).hexdigest()
    if _sha256(attempt["review_sha256"], "review document sha256") != review_sha256:
        raise ContractError("review document hash is invalid")
    adapter = _frozen_role_adapter(snapshot, "reviewer", frozen_reviewer)
    observed = attempt["observed_identity"]
    native_ids = attempt["native_ids"]
    if adapter is None:
        if observed is not None or native_ids != {}:
            raise ContractError("fixture review cannot claim a native observed identity")
    else:
        if not isinstance(adapter, dict):
            raise ContractError("frozen review adapter is invalid")
        for field in ("harness", "harness_version", "model_provider"):
            if not isinstance(adapter.get(field), str) or not adapter[field]:
                raise ContractError("frozen review adapter identity is invalid")
        identity = _exact(observed, {
            "harness", "harness_version", "model_provider", "model_id", "effort",
            "permission_policy", "verification",
        }, "observed reviewer identity")
        expected_identity = {
            "harness": adapter["harness"],
            "harness_version": adapter["harness_version"],
            "model_provider": adapter["model_provider"],
            "model_id": frozen_reviewer["profile"]["model_id"],
            "effort": frozen_reviewer["profile"]["effort"]["value"],
            "permission_policy": frozen_reviewer["profile"]["permission_policy"],
            "verification": "verified",
        }
        if canonical_json(identity) != canonical_json(expected_identity):
            raise ContractError("observed reviewer identity does not match the frozen adapter")
        ids = _exact(native_ids, {"thread_id", "turn_id"}, "native reviewer ids")
        for field in ("thread_id", "turn_id"):
            _text(ids[field], f"native reviewer {field}", maximum=500)
    if attempt["worker_invocations"] != 1 or type(attempt["worker_invocations"]) is not int:
        raise ContractError("review worker invocation accounting is invalid")
    if attempt["worker_invocations"] > task["budget"]["max_worker_invocations"]:
        raise ContractError("review exceeds max_worker_invocations")
    native_requests = attempt["native_model_requests"]
    if native_requests is not None and (
            type(native_requests) is not int or native_requests < 0):
        raise ContractError("native model request count must be non-negative or unknown")
    usage = _exact(attempt["usage"], {
        "input_tokens", "output_tokens", "total_tokens", "source",
    }, "review usage")
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        if usage[field] is not None and (
                type(usage[field]) is not int or usage[field] < 0):
            raise ContractError("review token usage must be non-negative or unknown")
    if usage["source"] not in {"native_reported", "unavailable"}:
        raise ContractError("review usage source is invalid")
    if usage["source"] == "unavailable" and any(
            usage[field] is not None
            for field in ("input_tokens", "output_tokens", "total_tokens")
    ):
        raise ContractError("unavailable usage cannot invent token counts")
    return json.loads(canonical_json(document))


def make_branch_review_evidence(
    snapshot: dict[str, Any],
    review: dict[str, Any],
    checks: list[dict[str, Any]],
    *,
    observed_identity: dict[str, Any] | None = None,
    native_ids: dict[str, str] | None = None,
    native_model_requests: int | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task, workspace = snapshot["task"], snapshot["workspace"]
    normalized_review = validate_review_document(review, task, workspace)
    normalized_checks = validate_check_results(checks, task, workspace)
    candidate_sha256, base_oid, target_oid = _workspace_identity(workspace)
    document = {
        "schema_version": 1,
        "workflow": task["workflow"],
        "candidate_sha256": candidate_sha256,
        "base_oid": base_oid,
        "target_oid": target_oid,
        "review": normalized_review,
        "checks": normalized_checks,
        "evaluation": evaluate_branch_review(
            task, workspace, normalized_review, normalized_checks,
        ),
        "attempt": {
            "role": "reviewer",
            "selected_profile": snapshot["routing"]["roles"]["reviewer"]["selected"],
            "prompt_sha256": hashlib.sha256(
                build_review_prompt(task, workspace).encode()
            ).hexdigest(),
            "review_sha256": hashlib.sha256(
                canonical_json(normalized_review).encode()
            ).hexdigest(),
            "observed_identity": observed_identity,
            "native_ids": native_ids if native_ids is not None else {},
            "worker_invocations": 1,
            "native_model_requests": native_model_requests,
            "usage": usage if usage is not None else {
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "source": "unavailable",
            },
        },
    }
    return validate_branch_review_evidence(document, snapshot)


def decode_branch_review_evidence(
    payload: bytes | str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    return validate_branch_review_evidence(
        _strict_json_object(
            payload, "branch review evidence", maximum=MAX_EVIDENCE_BYTES,
        ),
        snapshot,
    )


def validate_branch_review_handoff(
    packet: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Validate a saved host packet and reconstruct its trusted evidence."""
    value = _exact(packet, {
        "schema_version", "workflow", "candidate_sha256", "base_oid", "target_oid",
        "review", "checks", "evaluation", "attempt_id", "attempt", "artifacts",
        "instructions",
    }, "branch review handoff")
    evidence = {
        field: value[field]
        for field in (
            "schema_version", "workflow", "candidate_sha256", "base_oid", "target_oid",
            "review", "checks", "evaluation", "attempt",
        )
    }
    validate_branch_review_evidence(evidence, snapshot)
    attempt_id = _text(value["attempt_id"], "handoff attempt id", maximum=200)
    artifacts = value["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != 4:
        raise ContractError("branch review handoff must contain four evidence artifacts")
    names, identifiers = set(), set()
    for reference in artifacts:
        item = _exact(
            reference, {"artifact_id", "name", "sha256"}, "handoff artifact reference",
        )
        artifact_id = _text(item["artifact_id"], "handoff artifact id", maximum=200)
        name = _text(item["name"], "handoff artifact name", maximum=255)
        _sha256(item["sha256"], "handoff artifact sha256")
        if name in names or artifact_id in identifiers:
            raise ContractError("branch review handoff artifact references are duplicated")
        names.add(name)
        identifiers.add(artifact_id)
    expected_names = {
        f"review-{attempt_id}.json",
        f"checks-{attempt_id}.json",
        f"evaluation-{attempt_id}.json",
        f"review-attempt-{attempt_id}.json",
    }
    if names != expected_names:
        raise ContractError("branch review handoff is missing required evidence artifacts")
    _text(value["instructions"], "handoff instructions", maximum=2000)
    return json.loads(canonical_json(value))


def validate_handoff_decision_evidence(
    decision: dict[str, Any],
    packet: dict[str, Any],
) -> None:
    """Require a lead decision to explicitly bind every presented evidence artifact."""
    if not isinstance(decision, dict) or not isinstance(decision.get("evidence_refs"), list):
        raise ContractError("lead decision evidence_refs must be an array")
    expected = {
        (reference["artifact_id"], reference["sha256"])
        for reference in packet["artifacts"]
    }
    supplied = set()
    for reference in decision["evidence_refs"]:
        if (not isinstance(reference, dict)
                or set(reference) != {"artifact_id", "sha256"}
                or not isinstance(reference["artifact_id"], str)
                or not isinstance(reference["sha256"], str)):
            raise ContractError("lead decision evidence reference is invalid")
        supplied.add((reference["artifact_id"], reference["sha256"]))
    if len(decision["evidence_refs"]) != len(supplied) or supplied != expected:
        raise ContractError("lead decision must bind every presented evidence artifact")
