"""One transaction-bound current-evidence gate for all lifecycle consumers."""

from __future__ import annotations

from datetime import datetime
import hashlib
import sqlite3
from typing import Any

from .claude_identity import strict_json
from .contracts import ContractError
from .experiment_evidence import read_experiment_chains
from .learning import evaluate_experiment, validate_experiment
from .store import canonical_json


def saved_evaluation(
    connection: sqlite3.Connection, experiment_id: str,
    evaluation_sha256: str | None = None,
) -> dict[str, Any] | None:
    """Decode historical bytes without giving them new execution authority."""
    original = connection.execute(
        "SELECT * FROM experiments WHERE experiment_id=?", (experiment_id,),
    ).fetchone()
    if original is None:
        return None
    record = {**dict(original), "revision_id": None, "previous_evaluation_sha256": None}
    if evaluation_sha256 != original["evaluation_sha256"]:
        if evaluation_sha256 is None:
            revision = connection.execute(
                "SELECT * FROM experiment_evaluation_revisions WHERE experiment_id=? ORDER BY id DESC LIMIT 1",
                (experiment_id,),
            ).fetchone()
        else:
            revision = connection.execute(
                "SELECT * FROM experiment_evaluation_revisions WHERE experiment_id=? AND evaluation_sha256=?",
                (experiment_id, evaluation_sha256),
            ).fetchone()
        if revision is not None:
            record.update({key: revision[key] for key in (
                "revision_id", "previous_evaluation_sha256", "evaluation_json",
                "evaluation_sha256", "verdict", "recorded_at",
            )})
        elif evaluation_sha256 is not None:
            return None
    for field in ("spec", "evaluation"):
        raw = record[f"{field}_json"]
        if hashlib.sha256(raw.encode()).hexdigest() != record[f"{field}_sha256"]:
            raise ContractError(f"saved experiment {field} hash is invalid")
        value = strict_json(raw)
        if not isinstance(value, dict):
            raise ContractError(f"saved experiment {field} is not an object")
        record[field] = value
    evaluation = record["evaluation"]
    if (evaluation.get("experiment_id") != experiment_id
            or evaluation.get("spec_sha256") != record["spec_sha256"]
            or evaluation.get("evaluated_at") != record["recorded_at"]
            or evaluation.get("verdict") != record["verdict"]):
        raise ContractError("saved experiment evaluation identity is invalid")
    return record


def current_evidence(
    connection: sqlite3.Connection, record: dict[str, Any], *, now: datetime,
) -> dict[str, Any]:
    """Recompute authority in the caller's transaction, never from a label."""
    if not connection.in_transaction:
        raise ContractError("current experiment eligibility requires a ledger transaction")
    evaluation = record["evaluation"]
    result = {
        "schema_version": 1, "experiment_id": record["experiment_id"],
        "spec_sha256": record["spec_sha256"],
        "evaluation_sha256": record["evaluation_sha256"],
        "saved_evidence_sha256": evaluation.get("evidence_sha256"),
        "current_evidence_sha256": None, "eligible": False, "reasons": [],
    }
    if record["spec"].get("schema_version") != 2:
        result["reasons"] = ["legacy_unverified_evidence"]
        return result
    project = connection.execute(
        "SELECT git_common_dir FROM projects WHERE id=?", (record["project_id"],),
    ).fetchone()
    if project is None:
        result["reasons"] = ["saved_project_unavailable"]
        return result
    try:
        spec = validate_experiment(record["spec"])
        chains = read_experiment_chains(
            connection, spec=spec, project_id=record["project_id"],
            project_common_dir=project["git_common_dir"], evaluated_at=now.isoformat(),
        )
        fresh = evaluate_experiment(
            spec, chains, evaluated_at=now.isoformat(),
            project_common_dir=project["git_common_dir"],
        )
    except ContractError:
        result["reasons"] = ["invalid_saved_run_evidence"]
        return result
    result["current_evidence_sha256"] = fresh["evidence_sha256"]
    if canonical_json({**fresh, "evaluated_at": record["recorded_at"]}) != canonical_json(evaluation):
        result["reasons"] = ["saved_evaluation_stale_evidence_changed"]
        return result
    result["eligible"] = True
    return result


def require_current_evidence(
    connection: sqlite3.Connection, experiment_id: str, evaluation_sha256: str,
    *, now: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    record = saved_evaluation(connection, experiment_id, evaluation_sha256)
    if record is None:
        raise ContractError("experiment evaluation evidence is unavailable")
    eligibility = current_evidence(connection, record, now=now)
    if not eligibility["eligible"]:
        raise ContractError("experiment evidence is not current: " + ", ".join(eligibility["reasons"]))
    return record, eligibility
