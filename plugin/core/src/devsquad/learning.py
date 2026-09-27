"""Strict outcome evidence and comparison primitives for M6 learning."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any

from .contracts import ContractError
from .store import canonical_json


OUTCOME_FIELDS = {
    "schema_version", "outcome_id", "kind", "verdict", "selection_mode",
    "observed_at", "corrects_outcome_id", "summary", "criteria",
    "contributions", "lead_repairs", "evidence_refs",
}
FINAL_VERDICTS = {"succeeded", "failed", "cancelled"}
LATE_VERDICTS = {"escaped_defect", "corrected"}
SELECTION_MODES = {"automatic", "pinned", "experimental"}
CRITERION_STATES = {"passed", "failed", "unknown"}
CONTRIBUTION_RESULTS = {"failed", "successful", "repair", "finding", "neutral"}
ROLES = {"worker", "implementer", "reviewer", "lead", "researcher"}
MAX_CLOCK_SKEW = timedelta(minutes=5)


def _now(value: datetime | None) -> datetime:
    current = datetime.now(timezone.utc) if value is None else value
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise ContractError("outcome evaluation time must include a timezone")
    return current.astimezone(timezone.utc)


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ContractError(f"outcome {field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"outcome {field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"outcome {field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _identifier(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        suffix = " or null" if nullable else ""
        raise ContractError(f"outcome {field} must be a non-empty string{suffix}")
    return value


def _evidence_refs(value: Any, field: str, *, required: bool = False) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ContractError(f"outcome {field} must be a string array")
    if len(set(value)) != len(value):
        raise ContractError(f"outcome {field} must be unique")
    if required and not value:
        raise ContractError(f"outcome {field} must not be empty")
    return list(value)


def validate_outcome(
    value: dict[str, Any], *, now: datetime | None = None,
) -> dict[str, Any]:
    """Validate and detach one final or late-correction outcome record."""
    if not isinstance(value, dict) or set(value) != OUTCOME_FIELDS:
        unknown = sorted(set(value) - OUTCOME_FIELDS) if isinstance(value, dict) else []
        missing = sorted(OUTCOME_FIELDS - set(value)) if isinstance(value, dict) else []
        raise ContractError(
            f"outcome fields invalid: unknown={unknown} missing={missing}",
        )
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("outcome schema_version is invalid")
    outcome_id = _identifier(value["outcome_id"], "outcome_id")
    kind = value["kind"]
    if not isinstance(kind, str) or kind not in {"final", "late_correction"}:
        raise ContractError("outcome kind is invalid")
    verdict = value["verdict"]
    allowed_verdicts = FINAL_VERDICTS if kind == "final" else LATE_VERDICTS
    if not isinstance(verdict, str) or verdict not in allowed_verdicts:
        raise ContractError("outcome verdict is invalid for its kind")
    selection_mode = value["selection_mode"]
    if not isinstance(selection_mode, str) or selection_mode not in SELECTION_MODES:
        raise ContractError("outcome selection_mode is invalid")
    corrects = _identifier(
        value["corrects_outcome_id"], "corrects_outcome_id", nullable=True,
    )
    if (kind == "final" and corrects is not None) or (
        kind == "late_correction" and corrects is None
    ):
        raise ContractError("outcome correction reference is invalid")
    if not isinstance(value["summary"], str) or not value["summary"].strip():
        raise ContractError("outcome summary must be a non-empty string")
    observed = _timestamp(value["observed_at"], "observed_at")
    if observed > _now(now) + MAX_CLOCK_SKEW:
        raise ContractError("outcome observed_at exceeds allowed clock skew")

    if not isinstance(value["criteria"], list):
        raise ContractError("outcome criteria must be an array")
    criteria = []
    criterion_ids = set()
    for item in value["criteria"]:
        if not isinstance(item, dict) or set(item) != {
            "criterion_id", "status", "evidence_refs",
        }:
            raise ContractError("outcome criterion fields are invalid")
        criterion_id = _identifier(item["criterion_id"], "criterion_id")
        if criterion_id in criterion_ids:
            raise ContractError("outcome criterion ids must be unique")
        criterion_ids.add(criterion_id)
        if not isinstance(item["status"], str) or item["status"] not in CRITERION_STATES:
            raise ContractError("outcome criterion status is invalid")
        criteria.append({
            "criterion_id": criterion_id,
            "status": item["status"],
            "evidence_refs": _evidence_refs(
                item["evidence_refs"], "criterion evidence_refs",
            ),
        })
    if kind == "final" and verdict == "succeeded" and any(
        item["status"] != "passed" for item in criteria
    ):
        raise ContractError("successful outcome criteria must all pass")

    if not isinstance(value["contributions"], list):
        raise ContractError("outcome contributions must be an array")
    contributions = []
    contribution_attempts = set()
    for item in value["contributions"]:
        if not isinstance(item, dict) or set(item) != {
            "attempt_id", "role", "result", "independent_success", "evidence_refs",
        }:
            raise ContractError("outcome contribution fields are invalid")
        attempt_id = _identifier(item["attempt_id"], "attempt_id")
        if attempt_id in contribution_attempts:
            raise ContractError("outcome contribution attempt ids must be unique")
        contribution_attempts.add(attempt_id)
        if not isinstance(item["role"], str) or item["role"] not in ROLES:
            raise ContractError("outcome contribution role is invalid")
        if not isinstance(item["result"], str) or item["result"] not in CONTRIBUTION_RESULTS:
            raise ContractError("outcome contribution result is invalid")
        if type(item["independent_success"]) is not bool:
            raise ContractError("outcome independent_success must be boolean")
        if item["independent_success"] and item["result"] != "successful":
            raise ContractError("only a successful contribution can be independently successful")
        contributions.append({
            "attempt_id": attempt_id,
            "role": item["role"],
            "result": item["result"],
            "independent_success": item["independent_success"],
            "evidence_refs": _evidence_refs(
                item["evidence_refs"], "contribution evidence_refs",
            ),
        })

    if not isinstance(value["lead_repairs"], list):
        raise ContractError("outcome lead_repairs must be an array")
    lead_repairs = []
    for item in value["lead_repairs"]:
        if not isinstance(item, dict) or set(item) != {
            "lead_attempt_id", "description", "evidence_refs",
        }:
            raise ContractError("outcome lead repair fields are invalid")
        lead_attempt_id = _identifier(
            item["lead_attempt_id"], "lead_attempt_id", nullable=True,
        )
        if not isinstance(item["description"], str) or not item["description"].strip():
            raise ContractError("outcome lead repair description is invalid")
        lead_repairs.append({
            "lead_attempt_id": lead_attempt_id,
            "description": item["description"],
            "evidence_refs": _evidence_refs(
                item["evidence_refs"], "lead repair evidence_refs",
            ),
        })

    normalized = {
        **value,
        "outcome_id": outcome_id,
        "corrects_outcome_id": corrects,
        "criteria": criteria,
        "contributions": contributions,
        "lead_repairs": lead_repairs,
        "evidence_refs": _evidence_refs(
            value["evidence_refs"], "evidence_refs", required=True,
        ),
    }
    return json.loads(canonical_json(normalized))


def build_comparison_report(
    *,
    project_id: str | None,
    project_path: str,
    terminal_runs: list[dict[str, Any]],
    outcome_records: list[dict[str, Any]],
    attempt_profiles: dict[str, str | None],
    generated_at: str,
) -> dict[str, Any]:
    """Aggregate outcomes without conflating final success and worker quality."""
    if project_id is not None and (not isinstance(project_id, str) or not project_id):
        raise ContractError("learning report project_id is invalid")
    if not isinstance(project_path, str) or not project_path:
        raise ContractError("learning report project_path is invalid")
    _timestamp(generated_at, "generated_at")
    if not isinstance(terminal_runs, list) or not isinstance(outcome_records, list):
        raise ContractError("learning report inputs are invalid")
    if not isinstance(attempt_profiles, dict):
        raise ContractError("learning report attempt profiles are invalid")

    terminal_by_id = {}
    for run in terminal_runs:
        if (not isinstance(run, dict) or set(run) != {"run_id", "state"}
                or not isinstance(run["run_id"], str)
                or run["state"] not in FINAL_VERDICTS):
            raise ContractError("learning report terminal run is invalid")
        terminal_by_id[run["run_id"]] = run["state"]

    finals: dict[str, dict[str, Any]] = {}
    corrections: dict[str, list[dict[str, Any]]] = {}
    for record in outcome_records:
        if (not isinstance(record, dict) or set(record) != {"run_id", "outcome"}
                or record["run_id"] not in terminal_by_id
                or not isinstance(record["outcome"], dict)):
            raise ContractError("learning report outcome record is invalid")
        outcome = record["outcome"]
        if outcome.get("kind") == "final":
            if record["run_id"] in finals:
                raise ContractError("learning report has duplicate final outcomes")
            finals[record["run_id"]] = outcome
        elif outcome.get("kind") == "late_correction":
            corrections.setdefault(record["run_id"], []).append(outcome)
        else:
            raise ContractError("learning report outcome kind is invalid")

    modes = {
        mode: {
            "sample_size": 0,
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
            "escaped_defects": 0,
            "success_rate": None,
        }
        for mode in sorted(SELECTION_MODES)
    }
    profiles: dict[str, dict[str, Any]] = {}
    lead_repairs = 0
    missing_profile_contributions = 0
    finals_without_contributions = 0
    for run_id, final in finals.items():
        mode = final["selection_mode"]
        mode_row = modes[mode]
        mode_row["sample_size"] += 1
        mode_row[final["verdict"]] += 1
        escaped = sum(
            correction["verdict"] == "escaped_defect"
            for correction in corrections.get(run_id, [])
        )
        mode_row["escaped_defects"] += escaped
        lead_repairs += len(final["lead_repairs"])
        if not final["contributions"]:
            finals_without_contributions += 1
        for contribution in final["contributions"]:
            profile_id = attempt_profiles.get(contribution["attempt_id"])
            if profile_id is None:
                missing_profile_contributions += 1
                continue
            row = profiles.setdefault(profile_id, {
                "contributions": 0,
                "independent_successes": 0,
                "failed": 0,
                "repairs": 0,
                "findings": 0,
            })
            row["contributions"] += 1
            row["independent_successes"] += int(
                contribution["independent_success"],
            )
            if contribution["result"] == "failed":
                row["failed"] += 1
            elif contribution["result"] == "repair":
                row["repairs"] += 1
            elif contribution["result"] == "finding":
                row["findings"] += 1
    for row in modes.values():
        if row["sample_size"]:
            row["success_rate"] = row["succeeded"] / row["sample_size"]

    missing_run_ids = sorted(set(terminal_by_id) - set(finals))
    return {
        "schema_version": 1,
        "project_id": project_id,
        "project_path": project_path,
        "generated_at": generated_at,
        "sample_size": len(finals),
        "terminal_run_count": len(terminal_by_id),
        "final_successes": sum(
            outcome["verdict"] == "succeeded" for outcome in finals.values()
        ),
        "escaped_defects": sum(
            correction["verdict"] == "escaped_defect"
            for history in corrections.values()
            for correction in history
        ),
        "lead_repairs": lead_repairs,
        "selection_modes": modes,
        "profiles": {profile_id: profiles[profile_id] for profile_id in sorted(profiles)},
        "missingness": {
            "terminal_runs_without_final_outcome": len(missing_run_ids),
            "terminal_run_ids_without_final_outcome": missing_run_ids,
            "finals_without_contributions": finals_without_contributions,
            "contributions_without_profile_id": missing_profile_contributions,
        },
        "interpretation": {
            "final_task_success_is_not_profile_success": True,
            "selection_modes_are_not_pooled": True,
        },
    }
