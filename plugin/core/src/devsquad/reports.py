"""Deterministic M3 terminal receipt, Markdown, event-export and manifest builders."""

from __future__ import annotations

import hashlib
from typing import Any

from .contracts import ContractError
from .store import canonical_json, request_hash
from .workflows import (
    validate_branch_review_handoff,
    validate_handoff_decision_evidence,
)


TERMINAL_REPORT_NAMES = frozenset({
    "receipt.json",
    "receipt.md",
    "events.jsonl",
    "artifact-manifest.json",
    "result-receipt.json",
})


def _artifact_projection(artifact: dict[str, Any]) -> dict[str, Any]:
    required = {"id", "name", "sha256", "byte_size"}
    if not isinstance(artifact, dict) or not required <= set(artifact):
        raise ContractError("report artifact projection is invalid")
    if (not isinstance(artifact["id"], str) or not artifact["id"]
            or not isinstance(artifact["name"], str) or not artifact["name"]
            or not isinstance(artifact["sha256"], str)
            or len(artifact["sha256"]) != 64
            or any(character not in "0123456789abcdef"
                   for character in artifact["sha256"])
            or type(artifact["byte_size"]) is not int
            or artifact["byte_size"] < 0):
        raise ContractError("report artifact projection values are invalid")
    return {key: artifact[key] for key in ("id", "name", "sha256", "byte_size")}


def _event_export(events: list[dict[str, Any]]) -> tuple[bytes, int, int]:
    if not isinstance(events, list):
        raise ContractError("events export input must be an array")
    lines = []
    last_cursor = 0
    last_version = 0
    for event in events:
        if not isinstance(event, dict):
            raise ContractError("events export entries must be objects")
        cursor, version = event.get("id"), event.get("run_version")
        if (type(cursor) is not int or cursor <= last_cursor
                or type(version) is not int or version <= last_version):
            raise ContractError("events export order is invalid")
        last_cursor, last_version = cursor, version
        lines.append(canonical_json(event))
    content = (("\n".join(lines) + "\n") if lines else "").encode()
    return content, last_cursor, last_version


def _decision(value: Any) -> dict[str, Any]:
    fields = {
        "schema_version", "submission_id", "submission_hash", "disposition",
        "reason", "evidence_refs",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ContractError("report lead decision fields are invalid")
    if value["schema_version"] != 1 or type(value["schema_version"]) is not int:
        raise ContractError("report lead decision schema_version is invalid")
    if (not isinstance(value["submission_id"], str) or not value["submission_id"]
            or value["disposition"] not in {"accept", "revise", "reject"}
            or not isinstance(value["reason"], str)
            or (value["disposition"] == "revise" and not value["reason"])):
        raise ContractError("report lead decision values are invalid")
    body = {key: value[key] for key in fields if key != "submission_hash"}
    if value["submission_hash"] != request_hash(body):
        raise ContractError("report lead decision hash is invalid")
    return value


def _history(
    entries: list[dict[str, Any]],
    snapshot: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(entries, list) or not entries:
        raise ContractError("branch review report history must be non-empty")
    attempts = []
    dispositions = []
    prior_sequence = 0
    candidate = None
    for index, entry in enumerate(entries):
        required = {
            "handoff_id", "sequence", "packet", "packet_sha256", "decision",
            "recorded_run_version",
        }
        if not isinstance(entry, dict) or set(entry) != required:
            raise ContractError("branch review report history entry is invalid")
        if (not isinstance(entry["handoff_id"], str) or not entry["handoff_id"]
                or type(entry["sequence"]) is not int
                or entry["sequence"] <= prior_sequence
                or type(entry["recorded_run_version"]) is not int
                or entry["recorded_run_version"] < 1):
            raise ContractError("branch review report history order is invalid")
        packet_json = canonical_json(entry["packet"])
        if hashlib.sha256(packet_json.encode()).hexdigest() != entry["packet_sha256"]:
            raise ContractError("branch review report handoff hash is invalid")
        packet = validate_branch_review_handoff(entry["packet"], snapshot)
        decision = _decision(entry["decision"])
        validate_handoff_decision_evidence(decision, packet)
        identity = (
            packet["candidate_sha256"], packet["base_oid"], packet["target_oid"],
        )
        if candidate is None:
            candidate = identity
        elif identity != candidate:
            raise ContractError("branch review report history changes the candidate")
        if index < len(entries) - 1 and decision["disposition"] != "revise":
            raise ContractError("only a revision may precede another review attempt")
        attempts.append({
            "id": packet["attempt_id"],
            "sequence": entry["sequence"],
            **packet["attempt"],
            "review": packet["review"],
            "checks": packet["checks"],
            "evaluation": packet["evaluation"],
            "evidence_refs": packet["artifacts"],
        })
        dispositions.append({
            "handoff_id": entry["handoff_id"],
            "sequence": entry["sequence"],
            "submission_id": decision["submission_id"],
            "submission_hash": decision["submission_hash"],
            "disposition": decision["disposition"],
            "reason": decision["reason"],
            "evidence_refs": decision["evidence_refs"],
            "recorded_run_version": entry["recorded_run_version"],
        })
        prior_sequence = entry["sequence"]
    return attempts, dispositions


def _markdown(receipt: dict[str, Any]) -> str:
    review = receipt["review"]
    lines = [
        "# DevSquad branch review",
        "",
        f"- Run: `{receipt['run_id']}`",
        f"- State: `{receipt['state']}`",
        f"- Disposition: `{receipt['lead']['disposition']}`",
        f"- Candidate: `{receipt['candidate']['sha256']}`",
        f"- Base: `{receipt['candidate']['base_oid']}`",
        f"- Target: `{receipt['candidate']['target_oid']}`",
        f"- Review verdict: `{review['verdict']}`",
        f"- Review attempts: `{len(receipt['attempts'])}`",
        "",
        "## Review",
        "",
        review["summary"],
        "",
    ]
    if review["findings"]:
        lines.extend(["## Findings", ""])
        for finding in review["findings"]:
            lines.extend([
                f"- **{finding['severity'].upper()} — {finding['title']}** "
                f"(`{finding['path']}:{finding['start_line']}`): "
                f"{finding['description']}",
                f"  Evidence: {finding['evidence']}",
            ])
        lines.append("")
    lines.extend(["## Checks", ""])
    if receipt["checks"]:
        for check in receipt["checks"]:
            requirement = "required" if check["required_to_pass"] else "report-only"
            lines.append(f"- `{check['id']}`: **{check['status']}** ({requirement})")
    else:
        lines.append("- No checks were declared.")
    lines.extend([
        "",
        "## Lead disposition",
        "",
        receipt["lead"]["reason"] or "No reason supplied.",
        "",
        "## Limitations",
        "",
    ])
    if receipt["limitations"]:
        lines.extend(f"- {item}" for item in receipt["limitations"])
    else:
        lines.append("- None recorded.")
    return "\n".join(lines) + "\n"


def build_terminal_reports(
    *,
    run_id: str,
    state: str,
    snapshot: dict[str, Any],
    history: list[dict[str, Any]],
    run_artifacts: list[dict[str, Any]],
    events: list[dict[str, Any]],
    completed_at: str,
    error: dict[str, Any] | None = None,
) -> dict[str, bytes]:
    if not isinstance(run_id, str) or not run_id:
        raise ContractError("report run id is invalid")
    if state not in {"succeeded", "failed"}:
        raise ContractError("branch review report state is invalid")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("routing"), dict):
        raise ContractError("branch review report snapshot is invalid")
    attempts, dispositions = _history(history, snapshot)
    final_packet = validate_branch_review_handoff(history[-1]["packet"], snapshot)
    final_decision = _decision(history[-1]["decision"])
    if (state == "succeeded") != (final_decision["disposition"] == "accept"):
        raise ContractError("terminal state and lead disposition disagree")
    if not isinstance(completed_at, str) or not completed_at:
        raise ContractError("report completion timestamp is invalid")
    if error is not None and not isinstance(error, dict):
        raise ContractError("report error must be an object or null")

    projected = [_artifact_projection(artifact) for artifact in run_artifacts]
    artifact_by_id = {artifact["id"]: artifact for artifact in projected}
    if len(artifact_by_id) != len(projected):
        raise ContractError("report evidence artifacts are duplicated")
    expected_ids = set()
    for entry in history:
        for reference in entry["packet"]["artifacts"]:
            artifact = artifact_by_id.get(reference["artifact_id"])
            if (artifact is None or artifact["name"] != reference["name"]
                    or artifact["sha256"] != reference["sha256"]):
                raise ContractError(
                    "handoff evidence does not match stored report artifacts"
                )
            expected_ids.add(reference["artifact_id"])
    evidence_projected = [
        artifact for artifact in projected if artifact["id"] in expected_ids
    ]

    events_content, through_cursor, through_version = _event_export(events)
    limitations = []
    if "internal_review_fixture" in snapshot:
        limitations.append(
            "Reviewer output came from the explicit offline fixture; "
            "it is not live-provider evidence."
        )
    native_counts = [attempt["native_model_requests"] for attempt in attempts]
    receipt = {
        "schema_version": 1,
        "run_id": run_id,
        "workflow": "branch-review",
        "state": state,
        "completed_at": completed_at,
        "candidate": {
            "sha256": final_packet["candidate_sha256"],
            "base_oid": final_packet["base_oid"],
            "target_oid": final_packet["target_oid"],
        },
        "routing": snapshot["routing"],
        "review": final_packet["review"],
        "checks": final_packet["checks"],
        "evaluation": final_packet["evaluation"],
        "criteria": final_packet["evaluation"]["criteria"],
        "attempts": attempts,
        "dispositions": dispositions,
        "revisions": {
            "requested": sum(
                item["disposition"] == "revise" for item in dispositions
            ),
            "executed": len(attempts) - 1,
            "maximum": snapshot["task"]["budget"]["max_revisions"],
        },
        "lead": {
            "mode": "host",
            "disposition": final_decision["disposition"],
            "reason": final_decision["reason"],
            "submission_id": final_decision["submission_id"],
            "submission_hash": final_decision["submission_hash"],
            "evidence_refs": final_decision["evidence_refs"],
            "usage": {
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "source": "unavailable",
            },
        },
        "accounting": {
            "worker_invocations": sum(
                attempt["worker_invocations"] for attempt in attempts
            ),
            "native_model_requests": (
                None if any(value is None for value in native_counts)
                else sum(native_counts)
            ),
            "attempt_usage": [attempt["usage"] for attempt in attempts],
            "host_usage_measured": False,
        },
        "artifacts": projected,
        "evidence_artifacts": evidence_projected,
        "events_export": {
            "through_cursor": through_cursor,
            "through_run_version": through_version,
            "includes_terminal_event": False,
            "excludes_terminal_report_artifact_events": True,
        },
        "limitations": limitations,
        "error": error,
    }
    receipt_content = (canonical_json(receipt) + "\n").encode()
    contents = {
        "receipt.json": receipt_content,
        "receipt.md": _markdown(receipt).encode(),
        "events.jsonl": events_content,
        "result-receipt.json": receipt_content,
    }
    manifest_entries = list(projected)
    for name, content in contents.items():
        manifest_entries.append({
            "id": None,
            "name": name,
            "sha256": hashlib.sha256(content).hexdigest(),
            "byte_size": len(content),
        })
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "candidate_sha256": final_packet["candidate_sha256"],
        "artifacts": manifest_entries,
        "self_excluded": True,
    }
    contents["artifact-manifest.json"] = (
        canonical_json(manifest) + "\n"
    ).encode()
    if set(contents) != TERMINAL_REPORT_NAMES:
        raise ContractError("terminal report set is incomplete")
    return contents
