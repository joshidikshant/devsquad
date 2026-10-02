"""Objective projection only; subjective later corrections stay append-only."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .contracts import ContractError


def project_outcome(run: dict[str, Any], receipt: dict[str, Any], attempts: list[dict[str, Any]],
                    artifact_refs: dict[str, str], assignment: dict[str, Any] | None) -> dict[str, Any]:
    snapshot = json.loads(run["mutable_snapshot"] or "null")
    if ("internal_fake_delay" in (snapshot or {}) and set(receipt) == {
            "returncode", "cancelled", "timed_out", "stdout", "stderr", "finished_at"}):
        # The existing generic runner fixture retains its native exit receipt,
        # unlike the managed workflow's semantic receipt. Do not rewrite it.
        if (type(receipt["returncode"]) is not int or type(receipt["cancelled"]) is not bool
                or type(receipt["timed_out"]) is not bool
                or type(receipt["finished_at"]) not in {int, float}):
            raise ContractError("objective generic exit receipt is invalid")
        expected = "cancelled" if receipt["cancelled"] else "failed" if receipt["timed_out"] or receipt["returncode"] != 0 else "succeeded"
        if run["state"] != expected and run["state"] != "cancelled":
            raise ContractError("objective generic exit contradicts terminal state")
        receipt = {**receipt, "run_id": run["id"], "state": run["state"],
                   "finished_at": datetime.fromtimestamp(receipt["finished_at"], timezone.utc).isoformat(),
                   "attempt_id": attempts[-1]["id"] if attempts else None,
                   "error": {"returncode": receipt["returncode"]} if expected == "failed" else None}
    if receipt.get("run_id") != run["id"] or receipt.get("state") != run["state"]:
        raise ContractError("objective receipt does not match the terminal run")
    roles = (snapshot or {}).get("routing", {}).get("roles", {})
    mode = ("experimental" if assignment is not None or (snapshot or {}).get("experiment_assignment") is not None else "pinned"
            if any(role.get("source") == "override" for role in roles.values()) else "automatic")
    terminal_ref = artifact_refs.get("receipt.json", artifact_refs["result-receipt.json"])
    accepted = run["state"] == "succeeded" and receipt.get("lead", {}).get("disposition") == "accept"
    criteria = []
    for criterion in receipt.get("criteria", []):
        # The receipt's evaluation supplies evidence, not a lead verdict. A
        # fenced final acceptance attests the criteria; cite that attestation
        # rather than manufacturing a check-level result from evidence_available.
        criteria.append({
            "criterion_id": criterion["id"],
            "status": "passed" if accepted else "unknown",
            "evidence_refs": [terminal_ref] if accepted else [],
        })
    reported_attempts = {item["id"]: item for item in
                         receipt.get("attempts", []) + receipt.get("lead", {}).get("attempts", [])
                         if item.get("id") is not None}
    failed_roles, contributions = set(), []
    for attempt in attempts:
        # Reservation and prelaunch cancellation are not worker exposure.
        if attempt["status"] != "finished" or attempt.get("pid") is None:
            continue
        metadata = json.loads(attempt["output_metadata"] or "null")
        reported = reported_attempts.get(attempt["id"], {})
        failed = (isinstance(metadata, dict) and metadata.get("failure") is not None
                  or reported.get("status") == "failed" or reported.get("error") is not None
                  or receipt.get("attempt_id") == attempt["id"] and receipt.get("error") is not None)
        role = attempt["role"]
        repaired = role in failed_roles
        if failed:
            result = "failed"
            failed_roles.add(role)
        elif repaired:
            result = "repair"
        elif role == "reviewer" and reported.get("review", {}).get("verdict") == "findings":
            result = "finding"
        elif run["state"] == "succeeded" and criteria and all(c["status"] == "passed" for c in criteria):
            result = "successful"
        else:
            result = "neutral"
        refs = [value for name, value in artifact_refs.items() if attempt["id"] in name] + [terminal_ref]
        contributions.append({"attempt_id": attempt["id"], "role": role, "result": result,
                              "independent_success": result == "successful", "evidence_refs": refs})
    imported_leads = [a["id"] for a in attempts if a["role"] == "lead"
                      and f"lead-attempt-{a['id']}.json" in artifact_refs]
    lead_repairs = []
    for index, decision in enumerate(receipt.get("dispositions", [])):
        if decision.get("disposition") != "revise":
            continue
        lead_id = imported_leads[index] if index < len(imported_leads) else None
        refs = [terminal_ref]
        if lead_id is not None:
            refs.append(artifact_refs[f"lead-attempt-{lead_id}.json"])
        lead_repairs.append({"lead_attempt_id": lead_id,
                             "description": decision.get("reason") or "Explicit lead revision requested.",
                             "evidence_refs": refs})
    if lead_repairs:
        # A successful final repair is not an independently successful original.
        for contribution in contributions:
            if contribution["result"] == "successful":
                contribution.update(result="repair", independent_success=False)
    lead = receipt.get("lead", {}).get("disposition") or "not_reached"
    return {
        "schema_version": 1, "outcome_id": assignment["outcome_id"] if assignment is not None else f"objective-final-{run['id']}",
        "kind": "final", "verdict": run["state"], "selection_mode": mode,
        "observed_at": receipt.get("completed_at") or receipt.get("finished_at") or run["updated_at"],
        "corrects_outcome_id": None, "summary": f"Objective terminal state: {run['state']}; lead disposition: {lead}.",
        "criteria": criteria, "contributions": contributions, "lead_repairs": lead_repairs,
        "evidence_refs": list(artifact_refs.values()),
    }
