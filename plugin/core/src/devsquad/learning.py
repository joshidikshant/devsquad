"""Strict outcome evidence and comparison primitives for M6 learning."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
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
ROLES = {"worker", "implementer", "reviewer", "lead", "researcher", "proposer_a", "proposer_b", "critic"}
MAX_CLOCK_SKEW = timedelta(minutes=5)
EXPERIMENT_FIELDS = {
    "schema_version", "experiment_id", "project_path", "question", "hypothesis",
    "evidence_availability", "variable", "cases", "gate", "budget",
    "rollback_target",
}
EXPERIMENT_EVALUATION_FIELDS = {
    "schema_version", "experiment_id", "spec_sha256", "evaluated_at",
    "verdict", "active_policy_changed", "reasons", "metrics", "cases",
    "failures", "variable", "rollback_target", "evidence_availability",
}
EXPERIMENT_RECORD_FIELDS = {
    "experiment", "spec_sha256", "evaluation", "evaluation_sha256",
    "recorded_at",
}


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


def validate_experiment(value: dict[str, Any]) -> dict[str, Any]:
    """Validate a predeclared one-variable, paired outcome experiment."""
    if not isinstance(value, dict) or set(value) != EXPERIMENT_FIELDS:
        raise ContractError("experiment fields are invalid")
    if type(value["schema_version"]) is not int or value["schema_version"] not in {1, 2}:
        raise ContractError("experiment schema_version is invalid")
    experiment_id = _identifier(value["experiment_id"], "experiment_id")
    project_path = value["project_path"]
    if (not isinstance(project_path, str) or not project_path
            or not Path(project_path).is_absolute()):
        raise ContractError("experiment project_path must be absolute")
    for field in ("question", "hypothesis"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise ContractError(f"experiment {field} must be a non-empty string")
    if (not isinstance(value["evidence_availability"], str)
            or value["evidence_availability"] not in {
        "local", "tracked_fixture", "unavailable",
    }):
        raise ContractError("experiment evidence_availability is invalid")
    variable = value["variable"]
    variable_fields = {
        "kind", "alias", "control_profile_id", "candidate_profile_id",
    }
    if value["schema_version"] == 2:
        variable_fields.update({
            "role", "control_profile_sha256", "candidate_profile_sha256",
            "control_execution_sha256", "candidate_execution_sha256",
        })
    if not isinstance(variable, dict) or set(variable) != variable_fields:
        raise ContractError("experiment variable fields are invalid")
    if variable["kind"] != "profile_binding":
        raise ContractError("experiment variable kind is invalid")
    for field in ("alias", "control_profile_id", "candidate_profile_id"):
        _identifier(variable[field], f"variable.{field}")
    if variable["control_profile_id"] == variable["candidate_profile_id"]:
        raise ContractError("experiment control and candidate must differ")
    if value["schema_version"] == 2:
        from .experiment_provenance import require_sha256

        if (not isinstance(variable["role"], str)
                or variable["role"] not in {"implementer", "reviewer"}):
            raise ContractError("experiment tested role is invalid")
        for arm in ("control", "candidate"):
            require_sha256(variable[f"{arm}_profile_sha256"], f"{arm} profile")
            require_sha256(variable[f"{arm}_execution_sha256"], f"{arm} execution")

    budget = value["budget"]
    if not isinstance(budget, dict) or set(budget) != {
        "max_cases", "max_worker_invocations", "wall_seconds",
    }:
        raise ContractError("experiment budget fields are invalid")
    for field in ("max_cases", "wall_seconds"):
        if type(budget[field]) is not int or budget[field] < 1:
            raise ContractError(f"experiment budget {field} must be positive")
    if type(budget["max_worker_invocations"]) is not int or budget["max_worker_invocations"] < 0:
        raise ContractError("experiment max_worker_invocations must be non-negative")

    cases = value["cases"]
    if not isinstance(cases, list) or not cases or len(cases) > budget["max_cases"]:
        raise ContractError("experiment cases exceed the bounded case budget")
    case_ids = set()
    outcome_ids = set()
    case_hashes = set()
    normalized_cases = []
    split_counts = {"evaluation": 0, "held_out": 0}
    for case in cases:
        case_fields = {
            "case_id", "split", "control_outcome_id", "candidate_outcome_id",
        }
        if value["schema_version"] == 2:
            case_fields.update({"input_sha256", "case_sha256"})
        if not isinstance(case, dict) or set(case) != case_fields:
            raise ContractError("experiment case fields are invalid")
        case_id = _identifier(case["case_id"], "case_id")
        if case_id in case_ids:
            raise ContractError("experiment case ids must be unique")
        case_ids.add(case_id)
        if not isinstance(case["split"], str) or case["split"] not in split_counts:
            raise ContractError("experiment case split is invalid")
        split_counts[case["split"]] += 1
        control_id = _identifier(case["control_outcome_id"], "control_outcome_id")
        candidate_id = _identifier(
            case["candidate_outcome_id"], "candidate_outcome_id",
        )
        if control_id == candidate_id:
            raise ContractError("experiment paired outcomes must differ")
        if control_id in outcome_ids or candidate_id in outcome_ids:
            raise ContractError("experiment outcome ids must be globally unique")
        outcome_ids.update((control_id, candidate_id))
        if value["schema_version"] == 2:
            require_sha256(case["input_sha256"], "paired input")
            require_sha256(case["case_sha256"], "corpus case")
            if case["case_sha256"] in case_hashes:
                raise ContractError("experiment corpus case identities must be unique")
            case_hashes.add(case["case_sha256"])
        normalized_cases.append({
            **case,
            "case_id": case_id,
            "split": case["split"],
            "control_outcome_id": control_id,
            "candidate_outcome_id": candidate_id,
        })

    gate = value["gate"]
    if not isinstance(gate, dict) or set(gate) != {
        "min_evaluation_pairs", "min_held_out_pairs", "noninferiority_margin",
        "minimum_success_gain", "max_candidate_escaped_defects",
    }:
        raise ContractError("experiment gate fields are invalid")
    for field, split in (
        ("min_evaluation_pairs", "evaluation"),
        ("min_held_out_pairs", "held_out"),
    ):
        if (type(gate[field]) is not int or gate[field] < 1
                or gate[field] > split_counts[split]):
            raise ContractError(f"experiment gate {field} is invalid")
    for field in ("noninferiority_margin", "minimum_success_gain"):
        number = gate[field]
        if (isinstance(number, bool) or not isinstance(number, (int, float))
                or not 0 <= number <= 1):
            raise ContractError(f"experiment gate {field} must be between zero and one")
    if (type(gate["max_candidate_escaped_defects"]) is not int
            or gate["max_candidate_escaped_defects"] < 0):
        raise ContractError("experiment escaped-defect gate is invalid")

    rollback = value["rollback_target"]
    if not isinstance(rollback, dict) or set(rollback) != {
        "profile_id", "binding_version",
    }:
        raise ContractError("experiment rollback target fields are invalid")
    if rollback["profile_id"] != variable["control_profile_id"]:
        raise ContractError("experiment rollback target must be the control profile")
    if type(rollback["binding_version"]) is not int or rollback["binding_version"] < 1:
        raise ContractError("experiment rollback binding_version is invalid")
    return json.loads(canonical_json({
        **value,
        "experiment_id": experiment_id,
        "variable": dict(variable),
        "cases": normalized_cases,
        "gate": dict(gate),
        "budget": dict(budget),
        "rollback_target": dict(rollback),
    }))


def evaluate_experiment(
    experiment: dict[str, Any],
    outcome_chains: dict[str, dict[str, Any]],
    *,
    evaluated_at: str,
    project_common_dir: str | None = None,
) -> dict[str, Any]:
    """Evaluate a frozen paired experiment without changing active policy."""
    spec = validate_experiment(experiment)
    _timestamp(evaluated_at, "evaluated_at")
    if not isinstance(outcome_chains, dict):
        raise ContractError("experiment outcome chains are invalid")
    provenance = {}
    if spec["schema_version"] == 2:
        from .experiment_provenance import validate_arm_chain

        if not isinstance(project_common_dir, str) or not Path(project_common_dir).is_absolute():
            raise ContractError("experiment provenance requires the saved project identity")
        run_ids = set()
        attempt_ids = set()
        # Validate every available arm, even when its partner is missing. A
        # missing partner must not hide reused or crossed evidence.
        for case in spec["cases"]:
            for arm in ("control", "candidate"):
                outcome_id = case[f"{arm}_outcome_id"]
                chain = outcome_chains.get(outcome_id)
                if chain is None:
                    provenance[outcome_id] = None
                    continue
                witness = validate_arm_chain(
                    chain, spec=spec, case=case, arm=arm,
                    project_common_dir=project_common_dir, evaluated_at=evaluated_at,
                )
                if witness["run_id"] in run_ids:
                    raise ContractError("experiment provenance run ids must be globally unique")
                if attempt_ids.intersection(witness["attempt_ids"]):
                    raise ContractError("experiment provenance attempt ids must be globally unique")
                run_ids.add(witness["run_id"])
                attempt_ids.update(witness["attempt_ids"])
                provenance[outcome_id] = witness
    rows = []
    metrics = {
        split: {
            "declared_pairs": 0,
            "available_pairs": 0,
            "control_successes": 0,
            "candidate_successes": 0,
            "candidate_escaped_defects": 0,
            "control_success_rate": None,
            "candidate_success_rate": None,
            "success_gain": None,
        }
        for split in ("evaluation", "held_out")
    }
    failures = []
    for case in spec["cases"]:
        split = case["split"]
        metrics[split]["declared_pairs"] += 1
        control = outcome_chains.get(case["control_outcome_id"])
        candidate = outcome_chains.get(case["candidate_outcome_id"])
        missing = []
        if control is None:
            missing.append("control")
        if candidate is None:
            missing.append("candidate")
        row = {**case, "status": "missing" if missing else "available", "missing": missing}
        if missing:
            failures.append({"case_id": case["case_id"], "reason": "missing_outcome"})
            rows.append(row)
            continue
        for arm, chain in (("control", control), ("candidate", candidate)):
            chain_fields = {"final", "late_corrections"}
            if spec["schema_version"] == 2:
                chain_fields.add("provenance")
            if (not isinstance(chain, dict) or set(chain) != chain_fields
                    or not isinstance(chain["final"], dict)
                    or not isinstance(chain["late_corrections"], list)):
                raise ContractError("experiment outcome chain is invalid")
            if chain["final"].get("kind") != "final":
                raise ContractError("experiment arm must reference a final outcome")
            if chain["final"].get("selection_mode") != "experimental":
                raise ContractError("experiment outcomes must be explicitly experimental")
            if any(not isinstance(correction, dict)
                   for correction in chain["late_corrections"]):
                raise ContractError("experiment late corrections are invalid")
            row[f"{arm}_verdict"] = chain["final"]["verdict"]
            row[f"{arm}_escaped_defects"] = sum(
                correction.get("verdict") == "escaped_defect"
                for correction in chain["late_corrections"]
            )
        metrics[split]["available_pairs"] += 1
        metrics[split]["control_successes"] += int(row["control_verdict"] == "succeeded")
        metrics[split]["candidate_successes"] += int(
            row["candidate_verdict"] == "succeeded",
        )
        metrics[split]["candidate_escaped_defects"] += row[
            "candidate_escaped_defects"
        ]
        if row["candidate_verdict"] != "succeeded":
            failures.append({
                "case_id": case["case_id"], "reason": "candidate_not_successful",
            })
        if row["candidate_escaped_defects"]:
            failures.append({
                "case_id": case["case_id"], "reason": "candidate_escaped_defect",
            })
        rows.append(row)

    reasons = []
    total_candidate_escaped = 0
    for split, row in metrics.items():
        minimum = spec["gate"][
            "min_evaluation_pairs" if split == "evaluation" else "min_held_out_pairs"
        ]
        if row["available_pairs"] < minimum:
            reasons.append(f"insufficient_{split}_pairs")
            continue
        row["control_success_rate"] = row["control_successes"] / row["available_pairs"]
        row["candidate_success_rate"] = (
            row["candidate_successes"] / row["available_pairs"]
        )
        row["success_gain"] = row["candidate_success_rate"] - row["control_success_rate"]
        if (row["candidate_success_rate"] + spec["gate"]["noninferiority_margin"]
                < row["control_success_rate"]):
            reasons.append(f"{split}_noninferiority_failed")
        if row["success_gain"] < spec["gate"]["minimum_success_gain"]:
            reasons.append(f"{split}_minimum_gain_failed")
        total_candidate_escaped += row["candidate_escaped_defects"]
    if total_candidate_escaped > spec["gate"]["max_candidate_escaped_defects"]:
        reasons.append("candidate_escaped_defect_limit_exceeded")

    verdict = "promotion_proposal" if not reasons else "no_change"
    evaluation = {
        "schema_version": spec["schema_version"],
        "experiment_id": spec["experiment_id"],
        "spec_sha256": hashlib.sha256(
            canonical_json(spec).encode(),
        ).hexdigest(),
        "evaluated_at": evaluated_at,
        "verdict": verdict,
        "active_policy_changed": False,
        "reasons": sorted(set(reasons)),
        "metrics": metrics,
        "cases": rows,
        "failures": failures,
        "variable": spec["variable"],
        "rollback_target": spec["rollback_target"],
        "evidence_availability": spec["evidence_availability"],
    }
    if spec["schema_version"] == 2:
        evaluation["evidence_sha256"] = hashlib.sha256(canonical_json(provenance).encode()).hexdigest()
    return evaluation


def build_learning_proposal(
    report: dict[str, Any],
    experiment_record: dict[str, Any] | None,
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Distill saved evidence into a reviewable draft without changing policy."""
    _timestamp(generated_at, "generated_at")
    required_report_fields = {
        "schema_version", "project_id", "project_path", "generated_at",
        "sample_size", "terminal_run_count", "final_successes",
        "escaped_defects", "lead_repairs", "selection_modes", "profiles",
        "missingness", "interpretation",
    }
    if not isinstance(report, dict) or set(report) != required_report_fields:
        raise ContractError("learning proposal report is invalid")
    if (report["schema_version"] != 1
            or not isinstance(report["project_path"], str)
            or not isinstance(report["selection_modes"], dict)
            or not isinstance(report["missingness"], dict)):
        raise ContractError("learning proposal report values are invalid")
    for field in ("sample_size", "terminal_run_count"):
        if type(report[field]) is not int or report[field] < 0:
            raise ContractError("learning proposal report counts are invalid")
    mode_samples = {}
    for mode in sorted(SELECTION_MODES):
        row = report["selection_modes"].get(mode)
        if (not isinstance(row, dict) or type(row.get("sample_size")) is not int
                or row["sample_size"] < 0):
            raise ContractError("learning proposal selection samples are invalid")
        mode_samples[mode] = row["sample_size"]

    report_sha256 = hashlib.sha256(canonical_json(report).encode()).hexdigest()
    experiment_id = None
    question = None
    hypothesis = None
    variable = None
    rollback_target = None
    failures = []
    reasons = ["no_evaluated_experiment"]
    verdict = "no_change"
    evidence_availability = "unavailable"
    experiment_samples = None
    experiment_evidence = None
    if experiment_record is not None:
        if (not isinstance(experiment_record, dict)
                or set(experiment_record) not in (
                    EXPERIMENT_RECORD_FIELDS, EXPERIMENT_RECORD_FIELDS | {"eligibility"},
                )):
            raise ContractError("learning proposal experiment record is invalid")
        raw_spec = experiment_record["experiment"]
        if isinstance(raw_spec, dict) and raw_spec.get("schema_version") == 1:
            # Audit history as saved, including cases whose old labels reused
            # outcomes. Strict new-spec validation is not historical decoding.
            if set(raw_spec) != EXPERIMENT_FIELDS:
                raise ContractError("historical experiment fields are invalid")
            spec = json.loads(canonical_json(raw_spec))
        else:
            spec = validate_experiment(raw_spec)
        evaluation = experiment_record["evaluation"]
        expected_fields = EXPERIMENT_EVALUATION_FIELDS | (
            {"evidence_sha256"} if spec["schema_version"] == 2 else set()
        )
        if (not isinstance(evaluation, dict)
                or set(evaluation) != expected_fields
                or type(evaluation.get("schema_version")) is not int
                or evaluation["schema_version"] != spec["schema_version"]):
            raise ContractError("learning proposal evaluation is invalid")
        if spec["schema_version"] == 2:
            from .experiment_provenance import require_sha256
            require_sha256(evaluation["evidence_sha256"], "evaluated evidence")
        spec_sha256 = hashlib.sha256(canonical_json(spec).encode()).hexdigest()
        evaluation_sha256 = hashlib.sha256(
            canonical_json(evaluation).encode(),
        ).hexdigest()
        if (experiment_record["spec_sha256"] != spec_sha256
                or evaluation.get("spec_sha256") != spec_sha256
                or experiment_record["evaluation_sha256"] != evaluation_sha256):
            raise ContractError("learning proposal evidence hash is invalid")
        if (evaluation.get("experiment_id") != spec["experiment_id"]
                or evaluation.get("verdict") not in {
                    "no_change", "promotion_proposal",
                }
                or evaluation.get("active_policy_changed") is not False
                or evaluation.get("variable") != spec["variable"]
                or evaluation.get("rollback_target") != spec["rollback_target"]
                or not isinstance(evaluation.get("reasons"), list)
                or not isinstance(evaluation.get("failures"), list)
                or not isinstance(evaluation.get("metrics"), dict)):
            raise ContractError("learning proposal evaluation values are invalid")
        _timestamp(experiment_record["recorded_at"], "recorded_at")
        metrics = evaluation["metrics"]
        if set(metrics) != {"evaluation", "held_out"} or any(
                not isinstance(metrics.get(split), dict)
                or type(metrics[split].get("declared_pairs")) is not int
                or type(metrics[split].get("available_pairs")) is not int
                for split in ("evaluation", "held_out")):
            raise ContractError("learning proposal experiment samples are invalid")
        experiment_id = spec["experiment_id"]
        question = spec["question"]
        hypothesis = spec["hypothesis"]
        variable = spec["variable"]
        rollback_target = spec["rollback_target"]
        failures = list(evaluation["failures"])
        reasons = list(evaluation["reasons"])
        verdict = evaluation["verdict"]
        eligibility = experiment_record.get("eligibility")
        if spec["schema_version"] == 1:
            verdict = "no_change"
            reasons = sorted(set([*reasons, "legacy_unverified_evidence"]))
        elif eligibility is None:
            verdict = "no_change"
            reasons = sorted(set([*reasons, "current_evidence_not_checked"]))
        else:
            if (not isinstance(eligibility, dict)
                    or eligibility.get("experiment_id") != spec["experiment_id"]
                    or eligibility.get("spec_sha256") != spec_sha256
                    or eligibility.get("evaluation_sha256") != evaluation_sha256
                    or eligibility.get("saved_evidence_sha256") != evaluation["evidence_sha256"]
                    or type(eligibility.get("eligible")) is not bool
                    or not isinstance(eligibility.get("reasons"), list)
                    or (eligibility["eligible"] and (
                        eligibility.get("current_evidence_sha256") != evaluation["evidence_sha256"]
                        or eligibility["reasons"]))):
                raise ContractError("learning proposal current eligibility is invalid")
            if not eligibility["eligible"]:
                verdict = "no_change"
                reasons = sorted(set([*reasons, *eligibility["reasons"]]))
        evidence_availability = spec["evidence_availability"]
        experiment_samples = {
            split: {
                "declared_pairs": metrics[split]["declared_pairs"],
                "available_pairs": metrics[split]["available_pairs"],
            }
            for split in ("evaluation", "held_out")
        }
        experiment_evidence = {
            "experiment_id": experiment_id,
            "spec_sha256": spec_sha256,
            "evaluation_sha256": evaluation_sha256,
            "recorded_at": experiment_record["recorded_at"],
            "eligibility": eligibility,
        }

    identity = {
        "project_path": report["project_path"],
        "report_sha256": report_sha256,
        "experiment": experiment_evidence,
        "verdict": verdict,
    }
    proposal_id = "proposal-" + hashlib.sha256(
        canonical_json(identity).encode(),
    ).hexdigest()[:24]
    return {
        "schema_version": 1,
        "proposal_id": proposal_id,
        "project_id": report["project_id"],
        "project_path": report["project_path"],
        "generated_at": generated_at,
        "verdict": verdict,
        "active_policy_changed": False,
        "question": question,
        "hypothesis": hypothesis,
        "variable": variable,
        "rollback_target": rollback_target,
        "sample_sizes": {
            "terminal_runs": report["terminal_run_count"],
            "final_outcomes": report["sample_size"],
            "selection_modes": mode_samples,
            "experiment": experiment_samples,
        },
        "missingness": json.loads(canonical_json(report["missingness"])),
        "reasons": reasons,
        "failures": failures,
        "evidence": {
            "availability": evidence_availability,
            "report_sha256": report_sha256,
            "experiment": experiment_evidence,
        },
        "decision": {
            "action": (
                "review_policy_change"
                if verdict == "promotion_proposal"
                else "retain_current_policy"
            ),
            "review_required": verdict == "promotion_proposal",
        },
    }


def render_learning_proposal_markdown(proposal: dict[str, Any]) -> str:
    """Render a compact local review record for a validated proposal."""
    if not isinstance(proposal, dict) or proposal.get("schema_version") != 1:
        raise ContractError("learning proposal is invalid")
    lines = [
        f"# Learning proposal {proposal['proposal_id']}",
        "",
        f"- Verdict: `{proposal['verdict']}`",
        f"- Project: `{proposal['project_path']}`",
        f"- Generated: `{proposal['generated_at']}`",
        "- Active policy changed: `false`",
        f"- Next action: `{proposal['decision']['action']}`",
        "",
        "## Evidence",
        "",
        f"- Report SHA256: `{proposal['evidence']['report_sha256']}`",
        f"- Final outcomes: {proposal['sample_sizes']['final_outcomes']}",
        f"- Terminal runs: {proposal['sample_sizes']['terminal_runs']}",
    ]
    experiment = proposal["evidence"]["experiment"]
    if experiment is None:
        lines.append("- Experiment: none")
    else:
        lines.extend([
            f"- Experiment: `{experiment['experiment_id']}`",
            f"- Evaluation SHA256: `{experiment['evaluation_sha256']}`",
            f"- Rollback target: `{proposal['rollback_target']['profile_id']}` "
            f"binding version {proposal['rollback_target']['binding_version']}",
        ])
    lines.extend(["", "## Reasons", ""])
    lines.extend(
        [f"- `{reason}`" for reason in proposal["reasons"]]
        or ["- No gate failures were recorded."]
    )
    lines.extend(["", "## Recorded failures", ""])
    lines.extend(
        [f"- `{canonical_json(failure)}`" for failure in proposal["failures"]]
        or ["- None."]
    )
    return "\n".join(lines) + "\n"
