"""Authoritative v2 experiment inputs from one consistent saved-ledger read.

The pure evaluator accepts normalized witnesses, but callers cannot supply
them here. Assignment, execution and outcome identity come from the ledger.
The caller owns the transaction, including any subsequent decision mutation.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import sqlite3
from typing import Any

from .contracts import ContractError
from .claude_identity import strict_json
from .experiment_provenance import (
    assignment_for, paired_input_identity, selected_execution_fingerprint,
    validate_arm_chain, validate_assignment,
)
from .learning import validate_outcome
from .store import canonical_json, request_hash


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _object(payload: str, label: str) -> dict[str, Any]:
    try:
        value = strict_json(payload)
    except (ContractError, TypeError, ValueError) as exc:
        raise ContractError(f"experiment saved {label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ContractError(f"experiment saved {label} must be an object")
    return value


def _outcome(row: sqlite3.Row, run: sqlite3.Row, now: datetime) -> dict[str, Any]:
    value = validate_outcome(_object(row["payload_json"], "outcome"), now=now)
    if (row["run_id"] != run["id"] or row["payload_sha256"] != _digest(value)
            or any(row[key] != value[key] for key in (
                "outcome_id", "kind", "verdict", "selection_mode",
                "observed_at", "corrects_outcome_id",
            )) or value["selection_mode"] != "experimental"):
        raise ContractError("experiment saved outcome identity/hash is inconsistent")
    if value["kind"] == "final" and value["verdict"] != run["state"]:
        raise ContractError("experiment final outcome does not match terminal run")
    return value


def _artifact(
    connection: sqlite3.Connection, artifact_id: str, run_id: str,
) -> tuple[dict[str, Any], bytes]:
    row = connection.execute(
        "SELECT id,run_id,name,path,sha256,byte_size FROM artifacts WHERE id=?",
        (artifact_id,),
    ).fetchone()
    if row is None or row["run_id"] != run_id:
        raise ContractError("experiment attempt artifact belongs to another run or is missing")
    try:
        content = Path(row["path"]).read_bytes()
    except OSError as exc:
        raise ContractError("experiment attempt artifact is unavailable") from exc
    if len(content) != row["byte_size"] or hashlib.sha256(content).hexdigest() != row["sha256"]:
        raise ContractError("experiment attempt artifact hash is inconsistent")
    return {key: row[key] for key in ("id", "name", "sha256", "byte_size")}, content


def verify_prelaunch_snapshot(
    connection: sqlite3.Connection, *, run_id: str,
    snapshot: Any, package_digest: str,
) -> None:
    """Fence continued trial execution to its original controlled inputs."""
    frozen = connection.execute(
        "SELECT a.*,r.package_digest AS run_package,p.git_common_dir "
        "FROM experiment_assignments a JOIN runs r ON r.id=a.run_id "
        "JOIN projects p ON p.id=r.project_id WHERE a.run_id=?", (run_id,),
    ).fetchone()
    if frozen is None:
        if isinstance(snapshot, dict) and (
                snapshot.get("experiment_spec") is not None
                or snapshot.get("experiment_assignment") is not None):
            raise ContractError("experiment launch has no immutable prelaunch assignment")
        return
    if not isinstance(snapshot, dict):
        raise ContractError("experiment launch snapshot is missing")
    original = _object(frozen["snapshot_json"], "preparation snapshot")
    spec = original.get("experiment_spec")
    assignment = validate_assignment(
        _object(frozen["assignment_json"], "assignment"), spec=spec,
        project_common_dir=frozen["git_common_dir"],
    )
    if (package_digest != frozen["package_digest"]
            or package_digest != frozen["run_package"]
            or _digest(assignment) != frozen["assignment_sha256"]
            or original.get("experiment_assignment") != assignment
            or snapshot.get("experiment_spec") != spec
            or snapshot.get("experiment_assignment") != assignment):
        raise ContractError("experiment launch differs from its frozen assignment/package")
    role = assignment["role"]
    inputs = paired_input_identity(snapshot, role=role, package_digest=package_digest)
    selected = snapshot["routing"]["roles"][role]["selected"]
    if (any(inputs[key] != assignment[key] for key in inputs)
            or any(selected[key] != assignment[key] for key in ("profile_id", "profile_sha256"))
            or selected_execution_fingerprint(snapshot, role=role) != assignment["execution_sha256"]):
        raise ContractError("experiment launch changed its controlled input/profile/execution")


def _named_artifact(connection, run_id, name, artifacts):
    row = connection.execute("SELECT id FROM artifacts WHERE run_id=? AND name=?", (run_id, name)).fetchone()
    if row is None:
        raise ContractError(f"experiment missing imported artifact: {name}")
    artifact, content = _artifact(connection, row["id"], run_id)
    artifacts.append(artifact)
    return content


def _delivery_revision(connection, run_id, events, claim_version, previous_candidate):
    revisions = [event for event in events if event["type"] == "delivery.revision_queued"
                 and event["run_version"] < claim_version]
    if not revisions:
        if previous_candidate is not None:
            raise ContractError("experiment delivery continuation has no revision fence")
        return None
    event = revisions[-1]
    payload = _object(event["payload"], "delivery revision event")
    row = connection.execute(
        "SELECT h.*,s.submission_id,s.submission_hash,s.decision_json,s.disposition,s.recorded_run_version "
        "FROM handoffs h JOIN handoff_submissions s ON s.handoff_id=h.id "
        "WHERE h.run_id=? AND h.id=? AND s.submission_id=? AND s.outcome='recorded'",
        (run_id, payload.get("handoff_id"), payload.get("submission_id")),
    ).fetchone()
    if row is None:
        raise ContractError("experiment delivery revision has no recorded disposition")
    packet = _object(row["packet_json"], "revision handoff")
    decision = _object(row["decision_json"], "revision disposition")
    body = {key: value for key, value in decision.items() if key != "submission_hash"}
    if (row["packet_sha256"] != _digest(packet) or row["submission_hash"] != request_hash(body)
            or decision.get("submission_hash") != row["submission_hash"]
            or decision.get("submission_id") != row["submission_id"]
            or row["disposition"] != "revise" or decision.get("disposition") != "revise"
            or row["recorded_run_version"] >= event["run_version"]
            or previous_candidate is None
            or packet.get("candidate_sha256") != previous_candidate["candidate_sha256"]
            or payload.get("previous_candidate_sha256") != previous_candidate["candidate_sha256"]):
        raise ContractError("experiment delivery revision identity/hash is inconsistent")
    return {
        "handoff_id": row["id"], "sequence": row["sequence"],
        "submission_id": row["submission_id"], "submission_hash": row["submission_hash"],
        "previous_candidate_sha256": packet["candidate_sha256"], "reason": decision["reason"],
        "review": packet["review"], "checks": packet["checks"], "evidence_refs": decision["evidence_refs"],
    }


def _delivery_candidate(connection, run_id, attempt_id, events, artifacts, snapshot, iteration):
    candidates = connection.execute(
        "SELECT id FROM artifacts WHERE run_id=? AND name GLOB 'candidate-*.json'", (run_id,),
    ).fetchall()
    matches = []
    for row in candidates:
        artifact, content = _artifact(connection, row["id"], run_id)
        candidate = _object(content, "delivery candidate")
        if candidate.get("implementation_artifact") == f"implementation-attempt-{attempt_id}.json":
            matches.append((artifact, candidate))
    ready = [event for event in events if event["type"] == "delivery.candidate_ready"
             and _object(event["payload"], "candidate event").get("attempt_id") == attempt_id]
    if len(matches) != 1 or len(ready) != 1:
        raise ContractError("experiment delivery implementation has no unique saved candidate fence")
    artifact, candidate = matches[0]
    fields = ("schema_version", "baseline_oid", "commit_oid", "tree_oid", "patch_sha256", "changed_paths")
    if any(key not in candidate for key in fields):
        raise ContractError("experiment saved candidate identity is incomplete")
    identity = {key: candidate[key] for key in fields}
    event = _object(ready[0]["payload"], "candidate event")
    if (candidate.get("candidate_sha256") != _digest(identity)
            or candidate["baseline_oid"] != snapshot["delivery_workspace"]["baseline_oid"]
            or candidate.get("iteration") != iteration
            or any(event.get(key) != candidate[key] for key in ("candidate_sha256", "commit_oid", "patch_sha256"))
            or artifact["name"] != f"candidate-{iteration}.json"
            or candidate.get("patch_artifact") != f"candidate-{iteration}.patch"):
        raise ContractError("experiment saved candidate differs from its implementation fence")
    artifacts.append(artifact)
    patch = _named_artifact(connection, run_id, candidate["patch_artifact"], artifacts)
    if hashlib.sha256(patch).hexdigest() != candidate["patch_sha256"] or len(patch) != candidate.get("patch_bytes"):
        raise ContractError("experiment candidate patch differs from its saved identity")
    return candidate, ready[0]["run_version"]


def _attempts(
    connection: sqlite3.Connection, run: sqlite3.Row, frozen: sqlite3.Row,
    snapshot: dict[str, Any], assignment: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Validate *all* reservations/history, not only a final successful arm.

    Unstarted reservations remain in the digest, but do not become exposure.
    A launched attempt without reconciled output keeps the arm unavailable.
    """
    events = connection.execute(
        "SELECT run_version,type,payload FROM events WHERE run_id=? ORDER BY run_version",
        (run["id"],),
    ).fetchall()
    prepared_version = frozen["frozen_run_version"]
    if (type(prepared_version) is not int or prepared_version < 2
            or type(frozen["preparation_fencing_token"]) is not int
            or frozen["preparation_fencing_token"] < 1
            or run["version"] < prepared_version):
        raise ContractError("experiment preparation fence/version is invalid")
    queued = [event for event in events if event["run_version"] == prepared_version]
    if len(queued) != 1 or queued[0]["type"] != "run.queued":
        raise ContractError("experiment preparation has no saved queued fence")
    if not events or events[0]["type"] != "run.preparing":
        raise ContractError("experiment preparation history is unavailable")
    preparation_token = 1
    claimed, launched, unstarted = {}, {}, set()
    for event in events:
        if event["type"] == "run.preparation_reclaimed" and event["run_version"] < prepared_version:
            preparation_token = _object(event["payload"], "preparation event").get("fencing_token")
        if event["type"] not in {"supervisor.claimed", "run.running", "run.unstarted_attempt_recovered"}:
            continue
        payload = _object(event["payload"], "attempt event")
        attempt_id = payload.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise ContractError("experiment attempt event identity is missing")
        if event["type"] == "run.unstarted_attempt_recovered":
            unstarted.add(attempt_id)
            continue
        target = claimed if event["type"] == "supervisor.claimed" else launched
        if attempt_id in target or event["run_version"] <= prepared_version:
            raise ContractError("experiment assignment must precede distinct attempt events")
        target[attempt_id] = {"run_version": event["run_version"], **payload}
    if preparation_token != frozen["preparation_fencing_token"]:
        raise ContractError("experiment preparation fencing token does not match history")
    rows = connection.execute("SELECT * FROM attempts WHERE run_id=?", (run["id"],)).fetchall()
    if set(claimed) != {row["id"] for row in rows} or not set(launched) <= set(claimed):
        raise ContractError("experiment attempt reservations do not match saved history")
    history = []
    exposed = False
    incomplete = False
    delivery_snapshot = dict(snapshot)
    delivery_iterations = []
    candidate_version = None
    for row in sorted(rows, key=lambda item: claimed[item["id"]]["run_version"]):
        role = row["role"]
        route = snapshot["routing"]["roles"].get(role)
        if (row["project_id"] != run["project_id"]
                or row["package_digest"] != frozen["package_digest"]
                or not isinstance(route, dict)):
            raise ContractError("experiment actual attempt project/package/role is inconsistent")
        slots = [route["selected"], *route.get("fallbacks", [])]
        index = row["profile_index"]
        if (type(index) is not int or not 0 <= index < len(slots)
                or row["profile_id"] != slots[index]["profile_id"]
                or row["account_pool_id"] != slots[index]["profile"]["account_pool_id"]):
            raise ContractError("experiment actual attempt profile does not match its frozen slot")
        selected = slots[index]
        started = launched.get(row["id"])
        if (row["pid"] is None) != (started is None):
            raise ContractError("experiment actual attempt launch identity is inconsistent")
        if started is not None:
            if (started["run_version"] <= claimed[row["id"]]["run_version"]
                    or any(started.get(key) != row[key] for key in ("pid", "pgid", "process_start_id"))):
                raise ContractError("experiment actual attempt launch fence is inconsistent")
        if row["status"] not in {"finished", "recovery_required"}:
            incomplete = True
        # A repaired pre-gate crash never became a worker exposure. All other
        # launched tested-role attempts, including failed fallbacks, must be
        # the explicitly declared execution, not merely an eligible profile.
        was_worker = started is not None and row["id"] not in unstarted
        if was_worker and role == assignment["role"]:
            actual = {**snapshot, "routing": {**snapshot["routing"], "roles": {
                **snapshot["routing"]["roles"], role: {**route, "selected": selected},
            }}}
            if (selected["profile_id"] != assignment["profile_id"]
                    or selected["profile_sha256"] != assignment["profile_sha256"]
                    or selected_execution_fingerprint(actual, role=role) != assignment["execution_sha256"]):
                raise ContractError("experiment actual attempt does not execute the declared arm")
        metadata = None
        artifacts = []
        captures = {}
        if row["output_metadata"] is not None:
            metadata = _object(row["output_metadata"], "attempt output")
            if started is None or row["id"] in unstarted:
                raise ContractError("experiment unstarted attempt cannot have worker output")
            for stream in ("stdout", "stderr"):
                artifact, content = _artifact(connection, row[f"{stream}_artifact_id"], run["id"])
                capture = metadata.get(stream)
                if (not isinstance(capture, dict)
                        or capture.get("captured_sha256") != artifact["sha256"]
                        or capture.get("captured_bytes") != artifact["byte_size"]):
                    raise ContractError("experiment attempt output does not match its saved capture")
                artifacts.append(artifact)
                captures[stream] = content
            if was_worker and role == assignment["role"] and row["status"] == "finished":
                exposed = True
        elif was_worker:
            incomplete = True
        # These immutable artifacts retain imported observed identity and
        # native usage; failure diagnostics are retained in output_metadata.
        imports = {}
        for prefix in ("review", "implementation", "lead"):
            artifact_row = connection.execute(
                "SELECT id FROM artifacts WHERE run_id=? AND name=?",
                (run["id"], f"{prefix}-attempt-{row['id']}.json"),
            ).fetchone()
            if artifact_row is not None:
                artifact, content = _artifact(connection, artifact_row["id"], run["id"])
                document = _object(content, "imported attempt evidence")
                evidence = document.get("attempt", document)
                if (not isinstance(evidence, dict) or evidence.get("role") != role
                        or canonical_json(evidence.get("selected_profile")) != canonical_json(selected)):
                    raise ContractError("experiment imported execution differs from its frozen attempt profile")
                artifacts.append(artifact)
                imports[prefix] = document
        delivery = snapshot["task"]["workflow"] == "issue-delivery"
        if captures and (role == "reviewer" or delivery and role == "implementer"):
            prefix = "implementation" if role == "implementer" else "review"
            if prefix in imports:
                from .workflows import (
                    validate_branch_review_evidence, validate_implementation_evidence,
                    require_check_integrity, require_independent_delivery_review,
                )

                context = snapshot
                if delivery and role == "implementer":
                    context = dict(snapshot)
                    previous = delivery_iterations[-1]["candidate"] if delivery_iterations else None
                    revision = _delivery_revision(connection, run["id"], events, claimed[row["id"]]["run_version"], previous)
                    if revision is not None:
                        context["revision_request"] = revision
                    document = validate_implementation_evidence(strict_json(captures["stdout"]), context)
                    if canonical_json(imports[prefix]) != canonical_json(document):
                        raise ContractError("experiment imported implementation differs from captured evidence")
                    candidate, candidate_version = _delivery_candidate(
                        connection, run["id"], row["id"], events, artifacts, snapshot, len(delivery_iterations) + 1,
                    )
                    if candidate_version <= claimed[row["id"]]["run_version"]:
                        raise ContractError("experiment candidate precedes its implementation reservation")
                    iteration = {"candidate": candidate, "implementation": document}
                    if revision is not None:
                        iteration["revision_request"] = revision
                    delivery_iterations.append(iteration)
                    delivery_snapshot = {**snapshot, "delivery_iterations": delivery_iterations, "workspace": {
                        "candidate_sha256": candidate["candidate_sha256"], "base_oid": candidate["baseline_oid"],
                        "target_oid": candidate["commit_oid"],
                    }}
                    if "pending_review_fixture" in snapshot:
                        delivery_snapshot["internal_review_fixture"] = snapshot["pending_review_fixture"]
                else:
                    if delivery:
                        if candidate_version is None or candidate_version >= claimed[row["id"]]["run_version"]:
                            raise ContractError("experiment delivery reviewer has no preceding implementation candidate")
                        context = delivery_snapshot
                    document = validate_branch_review_evidence(strict_json(captures["stdout"]), context)
                    if canonical_json(imports[prefix]) != canonical_json(document["attempt"]):
                        raise ContractError("experiment imported review attempt differs from captured evidence")
                    for name, expected in (
                        (f"review-{row['id']}.json", document["review"]),
                        (f"checks-{row['id']}.json", {"schema_version": 1, "candidate_sha256": document["candidate_sha256"],
                                                   "target_oid": document["target_oid"], "results": document["checks"]}),
                        (f"evaluation-{row['id']}.json", document["evaluation"]),
                    ):
                        imported = _object(_named_artifact(connection, run["id"], name, artifacts), "review import")
                        if canonical_json(imported) != canonical_json(expected):
                            raise ContractError("experiment imported review/check/evaluation differs from captured evidence")
                    require_check_integrity(document["checks"])
                    require_independent_delivery_review(context, document)
            else:
                # Terminal failed/cancelled workers do not publish successful
                # review evidence. Bind their opaque output to the hashed
                # early-terminal receipt, not an absent metadata.failure key.
                receipt_row = connection.execute(
                    "SELECT id FROM artifacts WHERE run_id=? AND name='result-receipt.json'",
                    (run["id"],),
                ).fetchone()
                if receipt_row is None:
                    raise ContractError(f"experiment {role} has no imported evidence or failure receipt")
                artifact, content = _artifact(connection, receipt_row["id"], run["id"])
                receipt = _object(content, "terminal failure receipt")
                receipt_attempts = receipt.get("attempts")
                if not isinstance(receipt_attempts, list):
                    raise ContractError(f"experiment failed {role} receipt attempts are invalid")
                projections = [item for item in receipt_attempts
                               if isinstance(item, dict) and item.get("id") == row["id"]]
                if (receipt.get("run_id") != run["id"]
                        or receipt.get("state") != run["state"]
                        or len(projections) != 1
                        or projections[0].get("status") not in {"failed", "cancelled"}
                        or projections[0].get("role") != role
                        or canonical_json(projections[0].get("selected_profile")) != canonical_json(selected)):
                    raise ContractError(f"experiment failed {role} receipt is inconsistent")
                artifacts.append(artifact)
        history.append({
            **{key: row[key] for key in (
                "id", "run_id", "project_id", "role", "status", "profile_id",
                "profile_index", "account_pool_id", "package_digest", "pid",
                "pgid", "process_start_id", "created_at", "finished_at",
            )},
            "reservation": claimed[row["id"]], "launch": started,
            "unstarted_recovered": row["id"] in unstarted,
            "profile_sha256": selected["profile_sha256"],
            "output_metadata": metadata, "artifacts": artifacts,
        })
    return history, exposed and not incomplete


def read_experiment_chains(
    connection: sqlite3.Connection, *, spec: dict[str, Any], project_id: str | None,
    project_common_dir: str, evaluated_at: str,
) -> dict[str, dict[str, Any]]:
    """Build v2 witnesses solely from immutable preparation and durable runs."""
    if not connection.in_transaction:
        raise ContractError("experiment evidence requires a consistent ledger transaction")
    if spec["schema_version"] != 2:
        raise ContractError("saved-run provenance requires a v2 experiment")
    declared = connection.execute(
        "SELECT project_id,spec_json,spec_sha256 FROM experiment_specs WHERE experiment_id=?",
        (spec["experiment_id"],),
    ).fetchone()
    if (declared is None or declared["project_id"] != project_id
            or declared["spec_json"] != canonical_json(spec)
            or declared["spec_sha256"] != _digest(spec)):
        raise ContractError("experiment specification is not predeclared for this saved project")
    records = connection.execute(
        "SELECT * FROM experiment_assignments WHERE experiment_id=?", (spec["experiment_id"],),
    ).fetchall()
    expected_keys = {(case["case_id"], arm) for case in spec["cases"] for arm in ("control", "candidate")}
    frozen_by_arm = {(row["case_id"], row["arm"]): row for row in records}
    if len(frozen_by_arm) != len(records) or not set(frozen_by_arm) <= expected_keys:
        raise ContractError("experiment saved assignments have undeclared or duplicate arms")
    chains = {}
    for case in spec["cases"]:
        for arm in ("control", "candidate"):
            expected = assignment_for(spec, case["case_id"], arm, project_common_dir=project_common_dir)
            final_row = connection.execute(
                "SELECT * FROM outcomes WHERE outcome_id=?", (expected["outcome_id"],),
            ).fetchone()
            frozen = frozen_by_arm.get((case["case_id"], arm))
            if frozen is None:
                if final_row is not None:
                    raise ContractError("experiment outcome has no prelaunch assignment")
                continue
            assignment = validate_assignment(
                _object(frozen["assignment_json"], "assignment"), spec=spec,
                project_common_dir=project_common_dir,
            )
            if (assignment != expected or frozen["assignment_sha256"] != _digest(assignment)
                    or frozen["outcome_id"] != expected["outcome_id"]):
                raise ContractError("experiment saved assignment identity/hash is inconsistent")
            run = connection.execute(
                "SELECT r.*,p.git_common_dir FROM runs r JOIN projects p ON p.id=r.project_id WHERE r.id=?",
                (frozen["run_id"],),
            ).fetchone()
            if (run is None or run["project_id"] != project_id
                    or run["git_common_dir"] != project_common_dir
                    or run["package_digest"] != frozen["package_digest"]):
                raise ContractError("experiment assigned run project/package is inconsistent")
            snapshot = _object(frozen["snapshot_json"], "preparation snapshot")
            if (snapshot.get("experiment_spec") != spec
                    or snapshot.get("experiment_assignment") != assignment):
                raise ContractError("experiment immutable preparation witness is inconsistent")
            inputs = paired_input_identity(snapshot, role=assignment["role"], package_digest=frozen["package_digest"])
            selected = snapshot["routing"]["roles"][assignment["role"]]["selected"]
            execution = selected_execution_fingerprint(snapshot, role=assignment["role"])
            if (any(inputs[key] != assignment[key] for key in inputs)
                    or any(selected[key] != assignment[key] for key in ("profile_id", "profile_sha256"))
                    or execution != assignment["execution_sha256"]):
                raise ContractError("experiment frozen profile/execution/input differs from declaration")
            history, exposed = _attempts(connection, run, frozen, snapshot, assignment)
            outcomes = connection.execute(
                "SELECT * FROM outcomes WHERE run_id=? ORDER BY observed_at,id", (run["id"],),
            ).fetchall()
            if any(row["kind"] == "final" and row["outcome_id"] != expected["outcome_id"] for row in outcomes):
                raise ContractError("experiment run final outcome differs from its assigned outcome")
            if final_row is None:
                continue
            if run["state"] not in {"succeeded", "failed", "cancelled"} or final_row["kind"] != "final":
                raise ContractError("experiment outcome requires a terminal assigned run")
            final = _outcome(final_row, run, datetime.fromisoformat(evaluated_at))
            corrections = [
                _outcome(row, run, datetime.fromisoformat(evaluated_at))
                for row in outcomes if row["kind"] == "late_correction"
            ]
            if not exposed:
                continue
            chain = {
                "final": final, "late_corrections": corrections,
                "provenance": {
                    "run_id": run["id"], "assignment": assignment,
                    "profile_sha256": selected["profile_sha256"],
                    "execution_sha256": execution, **inputs,
                    "attempt_ids": [attempt["id"] for attempt in history],
                    "attempts_sha256": _digest(history),
                    "final_outcome_sha256": _digest(final),
                    "correction_sha256": [_digest(value) for value in corrections],
                },
            }
            validate_arm_chain(chain, spec=spec, case=case, arm=arm,
                               project_common_dir=project_common_dir, evaluated_at=evaluated_at)
            chains[expected["outcome_id"]] = chain
    return chains
