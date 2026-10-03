"""Transactional SQLite ledger and atomic artifact storage for M2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from functools import wraps
from importlib.resources import files
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import uuid
from typing import Any

from .contracts import BudgetExhausted, ContractError

SUPPORTED_SCHEMA_VERSION = 17
TERMINAL_STATES = {"succeeded", "failed", "cancelled"}
HOST_LEASE_SECONDS = 10 * 60
BRANCH_REVIEW_TERMINAL_ARTIFACTS = frozenset({
    "receipt.json",
    "receipt.md",
    "events.jsonl",
    "artifact-manifest.json",
    "result-receipt.json",
})


class ConflictError(ContractError):
    code = "CONFLICT"


class SchemaVersionError(ContractError):
    code = "SCHEMA_UNSUPPORTED"


@dataclass(frozen=True)
class StartClaim:
    run_id: str
    project_id: str
    request_hash: str
    version: int
    created: bool
    fencing_token: int | None


@dataclass(frozen=True)
class AttemptReservation:
    run_id: str
    attempt_id: str
    attempt_token: str
    supervisor_token: int
    version: int


@dataclass(frozen=True)
class PreparationClaim:
    run_id: str
    fencing_token: int
    version: int


@dataclass(frozen=True)
class HandoffClaim:
    run_id: str
    handoff_id: str
    owner_id: str
    fencing_token: int
    expires_at: str
    run_version: int
    action: str


@dataclass(frozen=True)
class HandoffSnapshot:
    run_id: str
    run_state: str
    run_phase: str | None
    run_version: int
    handoff_id: str
    sequence: int
    status: str
    packet: dict[str, Any]
    packet_sha256: str
    created_run_version: int
    submitted_run_version: int | None
    created_at: str
    closed_at: str | None
    claim: HandoffClaim | None


@dataclass(frozen=True)
class HandoffSubmission:
    run_id: str
    handoff_id: str
    submission_id: str
    submission_hash: str
    disposition: str
    recorded_run_version: int
    replayed: bool


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _authoritative_now(value: datetime | None = None) -> datetime:
    current = datetime.now(timezone.utc) if value is None else value
    if current.tzinfo is None or current.utcoffset() is None:
        raise ContractError("authoritative time must include a timezone")
    return current.astimezone(timezone.utc)


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ConflictError("persisted handoff lease timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConflictError("persisted handoff lease timestamp is invalid")
    return parsed.astimezone(timezone.utc)


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ContractError("request must be finite JSON") from exc


def request_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def git_common_dir(worktree: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            text=True, capture_output=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ContractError("project path is not a Git worktree") from exc
    return Path(result.stdout.strip()).resolve(strict=True)


def _project_terminal(method):
    @wraps(method)
    def wrapped(self, run_id, *args, **kwargs):
        result = method(self, run_id, *args, **kwargs)
        self.project_final_outcome(run_id)
        return result
    return wrapped


class Store:
    def __init__(self, database: Path, artifacts: Path):
        self.database = database
        self.artifacts = artifacts
        database.parent.mkdir(parents=True, exist_ok=True)
        artifacts.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database, timeout=10, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.create_function("devsquad_connection_schema", 0, lambda: SUPPORTED_SCHEMA_VERSION)
        try:
            self.connection.execute("PRAGMA busy_timeout=10000")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.migrate()
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
        except Exception:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def migrate(self) -> None:
        self.connection.execute("BEGIN EXCLUSIVE")
        try:
            table = self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
            current = self.connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0] if table else 0
            initial_version = current
            if current > SUPPORTED_SCHEMA_VERSION:
                raise SchemaVersionError(f"database schema {current} is newer than supported {SUPPORTED_SCHEMA_VERSION}")
            if 0 < current < SUPPORTED_SCHEMA_VERSION:
                runs_table = self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'").fetchone()
                if runs_table is not None:
                    pending = self.connection.execute(
                        "SELECT id,state,phase FROM runs WHERE state NOT IN ('succeeded','failed','cancelled') ORDER BY created_at",
                    ).fetchall()
                    if pending:
                        identifiers = ", ".join(row["id"] for row in pending)
                        raise SchemaVersionError(
                            f"schema upgrade {current} → {SUPPORTED_SCHEMA_VERSION} deferred: active/recoverable runs {identifiers}; "
                            "finish or cancel them using the previous installed release, then retry the update"
                        )
            while current < SUPPORTED_SCHEMA_VERSION:
                next_version = current + 1
                candidates = [entry for entry in files("devsquad.migrations").iterdir() if entry.name.startswith(f"{next_version:03d}_") and entry.name.endswith(".sql")]
                if len(candidates) != 1:
                    raise SchemaVersionError(f"migration {next_version} is missing or ambiguous")
                sql = candidates[0].read_text()
                statement = ""
                for line in sql.splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        self.connection.execute(statement.strip())
                        statement = ""
                if statement.strip():
                    raise SchemaVersionError(
                        f"migration {next_version} contains incomplete SQL",
                    )
                self.connection.execute("INSERT INTO schema_migrations(version, applied_at) VALUES(?, ?)", (next_version, _utc_now()))
                current = next_version
            if initial_version < SUPPORTED_SCHEMA_VERSION:
                # An old Store opened before the exclusive upgrade can outlive
                # the constructor's version check. Fence its later writes at
                # the database boundary, including admission of a new run.
                # Old packages either report their lower version or lack the
                # function entirely; both fail before mutating a saved row.
                tables = self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='schema_migrations'",
                ).fetchall()
                for table_row in tables:
                    name = table_row["name"].replace('"', '""')
                    for action in ("INSERT", "UPDATE", "DELETE"):
                        trigger = f"devsquad_schema_guard_{name}_{action.lower()}"
                        self.connection.execute(
                            f'CREATE TRIGGER IF NOT EXISTS "{trigger}" BEFORE {action} ON "{name}" BEGIN '
                            "SELECT CASE WHEN devsquad_connection_schema() < (SELECT MAX(version) FROM schema_migrations) "
                            "THEN RAISE(ABORT, 'database schema is newer than connection supports') END; END"
                        )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def _project(self, common_dir: Path) -> str:
        key = str(common_dir)
        row = self.connection.execute("SELECT id FROM projects WHERE git_common_dir=?", (key,)).fetchone()
        if row:
            return row[0]
        project_id = str(uuid.uuid4())
        self.connection.execute("INSERT INTO projects(id, git_common_dir, created_at) VALUES(?,?,?)", (project_id, key, _utc_now()))
        return project_id

    def runs_for_project(self, project: Path) -> list[dict[str, Any]]:
        """Bounded choices from this canonical Git project, without registering it."""
        common_dir = git_common_dir(project)
        return [dict(row) for row in self.connection.execute(
            "SELECT r.id AS run_id,r.state,r.phase FROM runs r "
            "JOIN projects p ON p.id=r.project_id WHERE p.git_common_dir=? "
            "ORDER BY r.id LIMIT 21", (str(common_dir),),
        ).fetchall()]

    def claim_start(self, worktree: Path, idempotency_key: str, submitted_request: Any, owner_id: str, *, objective_outcome: bool = False) -> StartClaim:
        if not idempotency_key or not owner_id:
            raise ContractError("idempotency key and owner are required")
        encoded, digest = canonical_json(submitted_request), request_hash(submitted_request)
        common_dir = git_common_dir(worktree)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            project_id = self._project(common_dir)
            existing = self.connection.execute(
                "SELECT id, request_hash, version FROM runs WHERE project_id=? AND idempotency_key=?",
                (project_id, idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != digest:
                    raise ConflictError("idempotency key was already used with a different request")
                self.connection.execute("COMMIT")
                return StartClaim(existing["id"], project_id, digest, existing["version"], False, None)
            run_id, now = str(uuid.uuid4()), _utc_now()
            self.connection.execute(
                "INSERT INTO runs(id,project_id,idempotency_key,request_hash,submitted_request,state,phase,version,created_at,updated_at,worktree_path) VALUES(?,?,?,?,?,'queued','preparing',1,?,?,?)",
                (run_id, project_id, idempotency_key, digest, encoded, now, now, str(worktree.resolve(strict=True))),
            )
            self.connection.execute(
                "INSERT INTO claims(run_id,kind,fencing_token,owner_id,active,claimed_at) VALUES(?,'preparing',1,?,1,?)",
                (run_id, owner_id, now),
            )
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,1,'run.preparing','{}',?)",
                (run_id, now),
            )
            if objective_outcome:
                self.connection.execute("INSERT INTO objective_outcome_jobs(run_id,requested_at) VALUES(?,?)", (run_id, now))
            self.connection.execute("COMMIT")
            return StartClaim(run_id, project_id, digest, 1, True, 1)
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def reclaim_preparation(self, run_id: str, expected_version: int, owner_id: str) -> PreparationClaim:
        """Take over an interrupted preflight while fencing its former owner."""
        if type(expected_version) is not int or expected_version < 1 or not owner_id:
            raise ContractError("preparation recovery requires a version and owner")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT r.state,r.phase,r.version,c.fencing_token "
                "FROM runs r JOIN claims c ON c.run_id=r.id WHERE r.id=?",
                (run_id,),
            ).fetchone()
            if (not row or row["state"] != "queued" or row["phase"] != "preparing"
                    or row["version"] != expected_version):
                raise ConflictError("preparation is no longer recoverable at that version")
            token, version, now = row["fencing_token"] + 1, expected_version + 1, _utc_now()
            self.connection.execute(
                "UPDATE claims SET fencing_token=?,owner_id=?,active=1,claimed_at=? WHERE run_id=?",
                (token, owner_id, now, run_id),
            )
            self.connection.execute(
                "UPDATE runs SET version=?,updated_at=? WHERE id=?",
                (version, now, run_id),
            )
            payload = canonical_json({"owner_id": owner_id, "fencing_token": token})
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'run.preparation_reclaimed',?,?)",
                (run_id, version, payload, now),
            )
            self.connection.execute("COMMIT")
            return PreparationClaim(run_id, token, version)
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def preparation_worktree(self, run_id: str, fencing_token: int, submitted_path: Path) -> Path:
        """Resolve a submitted path only if it still names the claimed project."""
        row = self.connection.execute(
            "SELECT r.state,r.phase,r.worktree_path,proj.git_common_dir,c.fencing_token,c.active "
            "FROM runs r JOIN projects proj ON proj.id=r.project_id "
            "JOIN claims c ON c.run_id=r.id WHERE r.id=?",
            (run_id,),
        ).fetchone()
        if (not row or row["state"] != "queued" or row["phase"] != "preparing"
                or not row["active"] or row["fencing_token"] != fencing_token):
            raise ConflictError("preparation claim is stale or cancelled")
        try:
            resolved = submitted_path.resolve(strict=True)
        except OSError as exc:
            raise ContractError("claimed project worktree is no longer available") from exc
        if str(resolved) != row["worktree_path"]:
            raise ContractError("project repo_path no longer resolves to the claimed worktree")
        if str(git_common_dir(resolved)) != row["git_common_dir"]:
            raise ContractError("claimed worktree no longer belongs to the persisted project")
        return resolved

    def validate_predecessor(self, run_id: str, fencing_token: int, supersedes_run_id: str | None) -> None:
        if supersedes_run_id is None:
            return
        row = self.connection.execute(
            "SELECT r.project_id,r.state,r.phase,c.fencing_token,c.active,"
            "predecessor.project_id AS predecessor_project,predecessor.state AS predecessor_state "
            "FROM runs r JOIN claims c ON c.run_id=r.id "
            "LEFT JOIN runs predecessor ON predecessor.id=? WHERE r.id=?",
            (supersedes_run_id, run_id),
        ).fetchone()
        if (not row or row["state"] != "queued" or row["phase"] != "preparing"
                or not row["active"] or row["fencing_token"] != fencing_token):
            raise ConflictError("preparation claim is stale or cancelled")
        if (row["predecessor_project"] != row["project_id"]
                or row["predecessor_state"] not in TERMINAL_STATES):
            raise ConflictError("superseded run must be terminal and belong to the same project")

    def _terminal_receipt(
        self,
        run_id: str,
        terminal_state: str,
        phase: str,
        payload: Any,
        *,
        attempt_id: str | None = None,
        now: str | None = None,
    ) -> tuple[Path, str, int, str]:
        finished_at = now or _utc_now()
        receipt = {
            "schema_version": 1,
            "run_id": run_id,
            "state": terminal_state,
            "phase": phase,
            "attempt_id": attempt_id,
            "returncode": None,
            "cancelled": terminal_state == "cancelled",
            "timed_out": False,
            "error": payload if terminal_state == "failed" else None,
            "finished_at": finished_at,
        }
        path, digest, size = self.finalize_artifact(
            run_id, "result-receipt.json", canonical_json(receipt).encode(),
        )
        return path, digest, size, finished_at

    def _reference_terminal_receipt(
        self,
        run_id: str,
        version: int,
        path: Path,
        digest: str,
        size: int,
        now: str,
    ) -> int:
        artifact_id = str(uuid.uuid4())
        self.connection.execute(
            "INSERT INTO artifacts(id,run_id,name,path,sha256,byte_size,created_at) "
            "VALUES(?,?,'result-receipt.json',?,?,?,?)",
            (artifact_id, run_id, str(path), digest, size, now),
        )
        version += 1
        artifact_event = canonical_json({
            "artifact_id": artifact_id,
            "name": "result-receipt.json",
            "sha256": digest,
            "byte_size": size,
        })
        self.connection.execute(
            "INSERT INTO events(run_id,run_version,type,payload,created_at) "
            "VALUES(?,?,'artifact.recorded',?,?)",
            (run_id, version, artifact_event, now),
        )
        return version

    def complete_preparation(
        self,
        run_id: str,
        fencing_token: int,
        mutable_snapshot: Any,
        *,
        package_path: str | None = None,
        package_digest: str | None = None,
        supersedes_run_id: str | None = None,
        worktree_path: str | None = None,
    ) -> int:
        snapshot = canonical_json(mutable_snapshot)
        resolved_worktree = None
        worktree_common = None
        if worktree_path is not None:
            if not isinstance(worktree_path, str) or not worktree_path or not Path(worktree_path).is_absolute():
                raise ContractError("prepared worktree path must be absolute")
            resolved_worktree = str(Path(worktree_path).resolve(strict=True))
            worktree_common = str(git_common_dir(Path(resolved_worktree)))
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT r.state,r.phase,r.version,c.fencing_token,c.active,"
                "p.git_common_dir FROM runs r JOIN claims c ON c.run_id=r.id "
                "JOIN projects p ON p.id=r.project_id WHERE r.id=?",
                (run_id,),
            ).fetchone()
            if not row or row["state"] != "queued" or row["phase"] != "preparing" or not row["active"] or row["fencing_token"] != fencing_token:
                raise ConflictError("preparation claim is stale or cancelled")
            if worktree_common is not None and worktree_common != row["git_common_dir"]:
                raise ContractError("prepared worktree belongs to a different project")
            if supersedes_run_id is not None:
                predecessor = self.connection.execute("SELECT project_id,state FROM runs WHERE id=?", (supersedes_run_id,)).fetchone()
                project = self.connection.execute("SELECT project_id FROM runs WHERE id=?", (run_id,)).fetchone()
                if not predecessor or predecessor["project_id"] != project["project_id"] or predecessor["state"] not in TERMINAL_STATES:
                    raise ConflictError("superseded run must be terminal and belong to the same project")
            version, now = row["version"] + 1, _utc_now()
            self._freeze_experiment_assignment(
                run_id, fencing_token, version, mutable_snapshot, package_digest, now,
            )
            self.connection.execute(
                "UPDATE runs SET mutable_snapshot=?,package_path=?,package_digest=?,"
                "supersedes_run_id=?,worktree_path=COALESCE(?,worktree_path),phase=NULL,"
                "version=?,updated_at=? WHERE id=?",
                (snapshot, package_path, package_digest, supersedes_run_id,
                 resolved_worktree, version, now, run_id),
            )
            self.connection.execute("UPDATE claims SET active=0 WHERE run_id=?", (run_id,))
            self.connection.execute("INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,'run.queued','{}',?)", (run_id, version, now))
            self.connection.execute("COMMIT")
            return version
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def _freeze_experiment_assignment(
        self, run_id: str, fencing_token: int, run_version: int,
        snapshot: Any, package_digest: str | None, recorded_at: str,
    ) -> None:
        """Inside complete_preparation's transaction, before any launch is possible."""
        from .experiment_provenance import (
            paired_input_identity, selected_execution_fingerprint, validate_assignment,
        )
        from .learning import validate_experiment

        if not isinstance(snapshot, dict):
            return
        fields = {"experiment_spec", "experiment_assignment"} & set(snapshot)
        if not fields:
            return
        if fields != {"experiment_spec", "experiment_assignment"}:
            raise ContractError("experiment preparation requires both specification and assignment")
        spec = validate_experiment(snapshot["experiment_spec"])
        project = self.connection.execute(
            "SELECT r.project_id,p.git_common_dir FROM runs r "
            "JOIN projects p ON p.id=r.project_id WHERE r.id=?", (run_id,),
        ).fetchone()
        if str(git_common_dir(Path(spec["project_path"]))) != project["git_common_dir"]:
            raise ContractError("experiment specification belongs to another project")
        assignment = validate_assignment(
            snapshot["experiment_assignment"], spec=spec,
            project_common_dir=project["git_common_dir"],
        )
        identity = paired_input_identity(
            snapshot, role=assignment["role"], package_digest=package_digest,
        )
        if any(identity[key] != assignment[key] for key in identity):
            raise ContractError("experiment paired input does not match its declaration")
        selected = snapshot["routing"]["roles"][assignment["role"]]["selected"]
        if any(selected[key] != assignment[key] for key in ("profile_id", "profile_sha256")):
            raise ContractError("experiment selected profile does not match its arm")
        if selected_execution_fingerprint(snapshot, role=assignment["role"]) != assignment["execution_sha256"]:
            raise ContractError("experiment native execution does not match its declared arm")
        if self.connection.execute(
            "SELECT 1 FROM attempts WHERE run_id=? LIMIT 1", (run_id,),
        ).fetchone() is not None:
            raise ConflictError("experiment assignment must precede every attempt")
        spec_json = canonical_json(spec)
        spec_sha256 = hashlib.sha256(spec_json.encode()).hexdigest()
        existing = self.connection.execute(
            "SELECT project_id,spec_json,spec_sha256 FROM experiment_specs WHERE experiment_id=?",
            (spec["experiment_id"],),
        ).fetchone()
        if existing is not None:
            if (existing["project_id"] != project["project_id"]
                    or existing["spec_json"] != spec_json or existing["spec_sha256"] != spec_sha256):
                raise ConflictError("experiment specification is already frozen differently")
        else:
            # An evaluation created before this contract cannot be retroactively
            # upgraded into a predeclared trial with the same identifier.
            if self.connection.execute(
                "SELECT 1 FROM experiments WHERE experiment_id=?", (spec["experiment_id"],),
            ).fetchone() is not None:
                raise ConflictError("historical experiment cannot acquire new assignments")
            self.connection.execute(
                "INSERT INTO experiment_specs(experiment_id,project_id,spec_json,spec_sha256,recorded_at) "
                "VALUES(?,?,?,?,?)",
                (spec["experiment_id"], project["project_id"], spec_json, spec_sha256, recorded_at),
            )
        if self.connection.execute(
            "SELECT 1 FROM experiment_assignments WHERE run_id=? OR "
            "(experiment_id=? AND (outcome_id=? OR (case_id=? AND arm=?)))",
            (run_id, spec["experiment_id"], assignment["outcome_id"],
             assignment["case_id"], assignment["arm"]),
        ).fetchone() is not None:
            raise ConflictError("experiment run or arm is already assigned")
        payload = canonical_json(assignment)
        self.connection.execute(
            "INSERT INTO experiment_assignments(run_id,experiment_id,case_id,arm,outcome_id,"
            "assignment_json,assignment_sha256,snapshot_json,package_digest,frozen_run_version,"
            "preparation_fencing_token,recorded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, spec["experiment_id"], assignment["case_id"], assignment["arm"],
             assignment["outcome_id"], payload, hashlib.sha256(payload.encode()).hexdigest(),
             canonical_json(snapshot), package_digest, run_version, fencing_token, recorded_at),
        )

    @_project_terminal
    def fail_preparation(
        self,
        run_id: str,
        fencing_token: int,
        error: Any,
        *,
        mutable_snapshot: Any | None = None,
        supersedes_run_id: str | None = None,
        terminal_artifacts: list[dict[str, Any]] | None = None,
    ) -> int:
        encoded = canonical_json(error)
        prepared = (
            self._prepare_exact_artifacts(
                run_id, terminal_artifacts, BRANCH_REVIEW_TERMINAL_ARTIFACTS,
            )
            if terminal_artifacts is not None else None
        )
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT r.project_id,r.state,r.phase,r.version,c.fencing_token,c.active "
                "FROM runs r JOIN claims c ON c.run_id=r.id WHERE r.id=?",
                (run_id,),
            ).fetchone()
            if not row or row["state"] != "queued" or row["phase"] != "preparing" or not row["active"] or row["fencing_token"] != fencing_token:
                raise ConflictError("preparation failure is stale or cancelled")
            if supersedes_run_id is not None:
                predecessor = self.connection.execute(
                    "SELECT project_id,state FROM runs WHERE id=?", (supersedes_run_id,),
                ).fetchone()
                if (not predecessor or predecessor["project_id"] != row["project_id"]
                        or predecessor["state"] not in TERMINAL_STATES):
                    raise ConflictError("superseded run must be terminal and belong to the same project")
            now = _utc_now()
            if prepared is None:
                path, digest, size, now = self._terminal_receipt(
                    run_id, "failed", "preparing", error, now=now,
                )
                version = self._reference_terminal_receipt(
                    run_id, row["version"], path, digest, size, now,
                )
            else:
                version, _ = self._reference_prepared_artifacts(
                    run_id, row["version"], prepared,
                )
            version += 1
            snapshot = canonical_json(mutable_snapshot) if mutable_snapshot is not None else None
            self.connection.execute(
                "UPDATE runs SET mutable_snapshot=COALESCE(?,mutable_snapshot),supersedes_run_id=?,"
                "state='failed',phase=NULL,version=?,updated_at=? WHERE id=?",
                (snapshot, supersedes_run_id, version, now, run_id),
            )
            self.connection.execute("UPDATE claims SET active=0 WHERE run_id=?", (run_id,))
            terminal_payload = dict(error) if isinstance(error, dict) else {"error": error}
            terminal_payload["receipt"] = "result-receipt.json"
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'run.failed',?,?)",
                (run_id, version, canonical_json(terminal_payload), now),
            )
            self.connection.execute("COMMIT")
            return version
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    @_project_terminal
    def cancel_preparing(self, run_id: str) -> int:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT state,phase,version FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise ContractError("run does not exist")
            if row["state"] in TERMINAL_STATES:
                self.connection.execute("COMMIT")
                return row["version"]
            if row["state"] != "queued" or row["phase"] != "preparing":
                raise ConflictError("run is no longer preparing")
            path, digest, size, now = self._terminal_receipt(run_id, "cancelled", "preparing", None)
            version = self._reference_terminal_receipt(
                run_id, row["version"], path, digest, size, now,
            ) + 1
            self.connection.execute("UPDATE runs SET state='cancelled',phase=NULL,version=?,updated_at=? WHERE id=?", (version, now, run_id))
            self.connection.execute("UPDATE claims SET active=0,fencing_token=fencing_token+1 WHERE run_id=?", (run_id,))
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'run.cancelled',?,?)",
                (run_id, version, canonical_json({"receipt": "result-receipt.json"}), now),
            )
            self.connection.execute("COMMIT")
            return version
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def append_event(self, run_id: str, expected_version: int, event_type: str, payload: Any, state: str | None = None) -> int:
        if state is not None:
            raise ConflictError("generic events cannot change run state")
        encoded = canonical_json(payload)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT state,phase,version FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row or row["version"] != expected_version:
                raise ConflictError("run version changed")
            if row["state"] in TERMINAL_STATES:
                raise ConflictError("terminal run is immutable")
            if row["phase"] is not None:
                raise ConflictError("run phase is owned by a fenced operation")
            version, now = expected_version + 1, _utc_now()
            self.connection.execute("UPDATE runs SET version=?,updated_at=? WHERE id=?", (version, now, run_id))
            self.connection.execute("INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,?,?,?)", (run_id, version, event_type, encoded, now))
            self.connection.execute("COMMIT")
            return version
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def finalize_artifact(self, run_id: str, name: str, content: bytes) -> tuple[Path, str, int]:
        if not name or Path(name).name != name:
            raise ContractError("artifact name must be a single path component")
        row = self.connection.execute("SELECT id FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise ContractError("run does not exist")
        digest = hashlib.sha256(content).hexdigest()
        directory = self.artifacts / run_id
        directory.mkdir(parents=True, exist_ok=True)
        name_key = hashlib.sha256(name.encode()).hexdigest()[:16]
        destination = directory / f"{digest}.{name_key}.blob"
        descriptor, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content); stream.flush(); os.fsync(stream.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                    raise ConflictError("content-addressed artifact path is corrupt")
            directory_fd = os.open(directory, os.O_RDONLY)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
        return destination, digest, len(content)

    def reference_artifact(self, run_id: str, name: str, path: Path, expected_sha256: str) -> str:
        if not name or Path(name).name != name:
            raise ContractError("artifact name must be a single path component")
        expected_parent = (self.artifacts / run_id).resolve()
        if path.resolve().parent != expected_parent:
            raise ContractError("artifact path is outside the run-owned store")
        content = path.read_bytes()
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected_sha256:
            raise ConflictError("artifact hash changed before database reference")
        artifact_id = str(uuid.uuid4())
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT state,version FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise ContractError("run does not exist")
            if row["state"] in TERMINAL_STATES:
                raise ConflictError("terminal run is immutable")
            version, now = row["version"] + 1, _utc_now()
            self.connection.execute("INSERT INTO artifacts(id,run_id,name,path,sha256,byte_size,created_at) VALUES(?,?,?,?,?,?,?)", (artifact_id, run_id, name, str(path), actual, len(content), _utc_now()))
            self.connection.execute("UPDATE runs SET version=?,updated_at=? WHERE id=?", (version, now, run_id))
            event = canonical_json({"artifact_id": artifact_id, "name": name, "sha256": actual, "byte_size": len(content)})
            self.connection.execute("INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,'artifact.recorded',?,?)", (run_id, version, event, now))
            self.connection.execute("COMMIT")
            return artifact_id
        except sqlite3.IntegrityError as exc:
            self.connection.execute("ROLLBACK")
            raise ConflictError("artifact name is already referenced for this run") from exc
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def store_artifact(self, run_id: str, name: str, content: bytes) -> str:
        path, digest, _ = self.finalize_artifact(run_id, name, content)
        return self.reference_artifact(run_id, name, path, digest)

    @staticmethod
    def _wall_seconds_from_run(run: sqlite3.Row | dict[str, Any]) -> int | None:
        documents = (run.get("mutable_snapshot"), run.get("submitted_request")) \
            if isinstance(run, dict) else (run["mutable_snapshot"], run["submitted_request"])
        for encoded in documents:
            if not encoded:
                continue
            try:
                document = json.loads(encoded)
                task = document.get("task") if isinstance(document, dict) else None
                budget = task.get("budget") if isinstance(task, dict) else None
                wall_seconds = budget.get("wall_seconds") if isinstance(budget, dict) else None
            except (TypeError, json.JSONDecodeError):
                continue
            if type(wall_seconds) is int and wall_seconds > 0:
                return wall_seconds
        return None

    def _execution_elapsed_ms(
        self,
        run_id: str,
        run: sqlite3.Row | dict[str, Any],
        current: datetime,
    ) -> int:
        """Count preflight and worker intervals while excluding saved host waits."""
        now = current.astimezone(timezone.utc)
        created_at = _parse_utc(run["created_at"])
        queued = self.connection.execute(
            "SELECT created_at FROM events WHERE run_id=? AND type='run.queued' "
            "ORDER BY id LIMIT 1",
            (run_id,),
        ).fetchone()
        if queued is not None:
            preflight_end = _parse_utc(queued["created_at"])
        elif run["state"] == "queued" and run["phase"] == "preparing":
            preflight_end = now
        else:
            preflight_end = _parse_utc(run["updated_at"])
        elapsed = max(0, int((preflight_end - created_at).total_seconds() * 1000))
        for attempt in self.connection.execute(
            "SELECT status,created_at,finished_at FROM attempts WHERE run_id=?",
            (run_id,),
        ):
            started = _parse_utc(attempt["created_at"])
            if (attempt["status"] == "ownership_ambiguous"
                    or attempt["finished_at"] is None):
                finished = now
            else:
                finished = _parse_utc(attempt["finished_at"])
            elapsed += max(0, int((finished - started).total_seconds() * 1000))
        return elapsed

    def remaining_wall_seconds(
        self,
        run_id: str,
        *,
        now: datetime | None = None,
    ) -> int | None:
        run = self.connection.execute(
            "SELECT state,phase,created_at,updated_at,mutable_snapshot,submitted_request "
            "FROM runs WHERE id=?",
            (run_id,),
        ).fetchone()
        if run is None:
            raise ContractError("run does not exist")
        wall_seconds = self._wall_seconds_from_run(run)
        current = _authoritative_now(now)
        experiment_remaining = self._experiment_remaining_wall_seconds(run_id, current)
        if wall_seconds is None:
            return experiment_remaining
        elapsed_ms = self._execution_elapsed_ms(
            run_id, run, current,
        )
        remaining = max(0, (wall_seconds * 1000 - elapsed_ms) // 1000)
        return remaining if experiment_remaining is None else min(remaining, experiment_remaining)

    def _experiment_remaining_wall_seconds(self, run_id: str, current: datetime) -> int | None:
        row = self.connection.execute(
            "SELECT s.spec_json,s.recorded_at FROM experiment_assignments a "
            "JOIN experiment_specs s ON s.experiment_id=a.experiment_id WHERE a.run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        wall_seconds = json.loads(row["spec_json"])["budget"]["wall_seconds"]
        elapsed_ms = max(0, int((current - _parse_utc(row["recorded_at"])).total_seconds() * 1000))
        return max(0, (wall_seconds * 1000 - elapsed_ms) // 1000)

    def _enforce_attempt_budget(
        self,
        run_id: str,
        run: sqlite3.Row,
    ) -> None:
        try:
            snapshot = json.loads(run["mutable_snapshot"] or "null")
            max_invocations = snapshot["task"]["budget"]["max_worker_invocations"]
        except (KeyError, TypeError, json.JSONDecodeError):
            max_invocations = None
        if type(max_invocations) is int:
            launched = self.worker_invocations(run_id)
            if launched >= max_invocations:
                raise BudgetExhausted("worker invocation budget is exhausted")
        wall_seconds = self._wall_seconds_from_run(run)
        if wall_seconds is not None:
            elapsed_ms = self._execution_elapsed_ms(
                run_id, run, _authoritative_now(),
            )
            if wall_seconds * 1000 - elapsed_ms < 1000:
                raise BudgetExhausted("run wall-time budget is exhausted")

    @staticmethod
    def _pool_observation(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "observation_id": row["observation_id"],
            "pool_id": row["pool_id"],
            "window_id": row["window_id"],
            "applies_to": json.loads(row["applies_to_json"]),
            "observed_at": row["observed_at"],
            "expires_at": row["expires_at"],
            "source": row["source"],
            "used": row["used"],
            "limit": row["limit_value"],
            "unit": row["unit"],
            "resets_at": row["resets_at"],
            "confidence": row["confidence"],
        }

    def _pool_observations(self, pool_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT observation_id,pool_id,window_id,applies_to_json,observed_at,"
            "expires_at,source,used,limit_value,unit,resets_at,confidence "
            "FROM pool_observations WHERE pool_id=? "
            "ORDER BY observed_at,observation_id",
            (pool_id,),
        ).fetchall()
        return [self._pool_observation(row) for row in rows]

    def record_pool_observation(
        self, observation: dict[str, Any], *, now: datetime | None = None,
    ) -> dict[str, Any]:
        """Persist one immutable, replay-safe capacity observation."""
        from .capacity import validate_observation

        current = _authoritative_now(now)
        normalized = validate_observation(observation, now=current)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                "SELECT observation_id,pool_id,window_id,applies_to_json,observed_at,"
                "expires_at,source,used,limit_value,unit,resets_at,confidence,recorded_at "
                "FROM pool_observations WHERE observation_id=?",
                (normalized["observation_id"],),
            ).fetchone()
            if existing is not None:
                stored = self._pool_observation(existing)
                if stored != normalized:
                    raise ConflictError(
                        "capacity observation id was already used with different evidence",
                    )
                self.connection.execute("COMMIT")
                return {
                    "observation": stored,
                    "recorded_at": existing["recorded_at"],
                    "replayed": True,
                }
            recorded_at = current.isoformat()
            self.connection.execute(
                "INSERT INTO pool_observations("
                "observation_id,pool_id,window_id,applies_to_json,observed_at,expires_at,"
                "source,used,limit_value,unit,resets_at,confidence,recorded_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    normalized["observation_id"], normalized["pool_id"],
                    normalized["window_id"], canonical_json(normalized["applies_to"]),
                    normalized["observed_at"], normalized["expires_at"],
                    normalized["source"], normalized["used"], normalized["limit"],
                    normalized["unit"], normalized["resets_at"],
                    normalized["confidence"], recorded_at,
                ),
            )
            self.connection.execute("COMMIT")
            return {
                "observation": normalized,
                "recorded_at": recorded_at,
                "replayed": False,
            }
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def capacity_snapshot(
        self,
        pool_id: str,
        *,
        target: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return current evidence and local reservations for one pool/target."""
        from .capacity import derive_pool_capacity

        current = _authoritative_now(now)
        in_flight = self.connection.execute(
            "SELECT COUNT(*) FROM pool_reservations "
            "WHERE pool_id=? AND reconciled_at IS NULL",
            (pool_id,),
        ).fetchone()[0]
        return derive_pool_capacity(
            pool_id,
            self._pool_observations(pool_id),
            target=target,
            in_flight=in_flight,
            now=current,
        )

    @staticmethod
    def _capacity_target(profile: dict[str, Any]) -> dict[str, str] | None:
        fields = ("harness", "model_family", "model_id")
        if all(isinstance(profile.get(field), str) and profile[field] for field in fields):
            return {field: profile[field] for field in fields}
        return None

    def _reserve_pool_capacity_locked(
        self,
        *,
        run_id: str,
        pool_id: str,
        purpose: str,
        profile_id: str | None,
        target: dict[str, Any] | None,
        max_concurrency: int,
        unknown_capacity_policy: str,
        frozen_status: str | None,
        attempt_id: str | None,
        now: datetime,
    ) -> dict[str, Any]:
        from .capacity import derive_pool_capacity

        if purpose not in {"attempt", "qualification", "classifier"}:
            raise ContractError("pool reservation purpose is invalid")
        if type(max_concurrency) is not int or max_concurrency < 1:
            raise ContractError("pool max_concurrency must be a positive integer")
        if unknown_capacity_policy not in {"allow_bounded", "block"}:
            raise ContractError("pool unknown_capacity_policy is invalid")
        if profile_id is not None and (not isinstance(profile_id, str) or not profile_id):
            raise ContractError("pool reservation profile_id is invalid")
        if frozen_status is not None and frozen_status not in {
            "available", "exhausted", "unknown",
        }:
            raise ContractError("frozen pool capacity status is invalid")
        in_flight = self.connection.execute(
            "SELECT COUNT(*) FROM pool_reservations "
            "WHERE pool_id=? AND reconciled_at IS NULL",
            (pool_id,),
        ).fetchone()[0]
        snapshot = derive_pool_capacity(
            pool_id,
            self._pool_observations(pool_id),
            target=target,
            in_flight=in_flight,
            now=now,
        )
        live_status = snapshot["status"]
        status = "exhausted" if "exhausted" in {frozen_status, live_status} else live_status
        if status == "exhausted":
            raise ConflictError("account pool capacity is exhausted")
        if status == "unknown" and unknown_capacity_policy == "block":
            raise ConflictError("unknown account pool capacity is blocked")
        effective_concurrency = 1 if status == "unknown" else max_concurrency
        if in_flight >= effective_concurrency:
            raise ConflictError("account pool concurrency is full")
        reservation_id = str(uuid.uuid4())
        self.connection.execute(
            "INSERT INTO pool_reservations("
            "id,pool_id,run_id,attempt_id,purpose,profile_id,reserved_at"
            ") VALUES(?,?,?,?,?,?,?)",
            (
                reservation_id, pool_id, run_id, attempt_id, purpose, profile_id,
                now.isoformat(),
            ),
        )
        return {
            "schema_version": 1,
            "reservation_id": reservation_id,
            "pool_id": pool_id,
            "run_id": run_id,
            "attempt_id": attempt_id,
            "purpose": purpose,
            "profile_id": profile_id,
            "reserved_at": now.isoformat(),
            "capacity_status": status,
            "capacity_evidence": snapshot,
        }

    def reserve_pool_capacity(
        self,
        run_id: str,
        pool_id: str,
        purpose: str,
        *,
        profile_id: str | None = None,
        target: dict[str, Any] | None = None,
        max_concurrency: int = 1,
        unknown_capacity_policy: str = "allow_bounded",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Reserve qualification/classifier capacity under one SQLite write lock."""
        if purpose == "attempt":
            raise ContractError("attempt capacity is reserved with its attempt")
        current = _authoritative_now(now)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute(
                "SELECT state,mutable_snapshot FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            if run is None:
                raise ContractError("run does not exist")
            if run["state"] in TERMINAL_STATES:
                raise ConflictError("terminal run cannot reserve pool capacity")
            reservation = self._reserve_pool_capacity_locked(
                run_id=run_id,
                pool_id=pool_id,
                purpose=purpose,
                profile_id=profile_id,
                target=target,
                max_concurrency=max_concurrency,
                unknown_capacity_policy=unknown_capacity_policy,
                frozen_status=None,
                attempt_id=None,
                now=current,
            )
            self.connection.execute("COMMIT")
            return reservation
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def reconcile_pool_reservation(
        self,
        reservation_id: str,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if not isinstance(reservation_id, str) or not reservation_id:
            raise ContractError("pool reservation id is invalid")
        if not isinstance(reason, str) or not reason:
            raise ContractError("pool reconciliation reason is invalid")
        current = _authoritative_now(now)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT pr.*,a.status AS attempt_status FROM pool_reservations pr "
                "LEFT JOIN attempts a ON a.id=pr.attempt_id WHERE pr.id=?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise ContractError("pool reservation does not exist")
            if row["reconciled_at"] is not None:
                if row["reconcile_reason"] != reason:
                    raise ConflictError("pool reservation was reconciled differently")
                self.connection.execute("COMMIT")
                return {**dict(row), "replayed": True}
            if row["attempt_id"] is not None and row["attempt_status"] in {
                "reserved", "running", "cancelling", "ownership_ambiguous",
            }:
                raise ConflictError("attempt ownership is not reconciled")
            reconciled_at = current.isoformat()
            self.connection.execute(
                "UPDATE pool_reservations SET reconciled_at=?,reconcile_reason=? "
                "WHERE id=? AND reconciled_at IS NULL",
                (reconciled_at, reason, reservation_id),
            )
            self.connection.execute("COMMIT")
            return {
                **dict(row),
                "reconciled_at": reconciled_at,
                "reconcile_reason": reason,
                "replayed": False,
            }
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def active_pool_counts(self) -> dict[str, int]:
        return {
            row["pool_id"]: row["in_flight"]
            for row in self.connection.execute(
                "SELECT pool_id,COUNT(*) AS in_flight FROM pool_reservations "
                "WHERE reconciled_at IS NULL GROUP BY pool_id"
            )
        }

    @staticmethod
    def _decision_observation_payload(
        row: sqlite3.Row, *, action: str, replayed: bool,
    ) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "purpose_id": row["purpose_id"],
            "mode": row["mode"],
            "cache_key": row["cache_key"],
            "status": row["status"],
            "billable_calls": row["billable_calls"],
            "request": json.loads(row["request_json"]),
            "response": (
                json.loads(row["response_json"])
                if row["response_json"] is not None else None
            ),
            "response_sha256": row["response_sha256"],
            "usage": (
                json.loads(row["usage_json"])
                if row["usage_json"] is not None else None
            ),
            "error": row["error"],
            "applied": bool(row["applied"]),
            "effect": (
                json.loads(row["effect_json"])
                if row["effect_json"] is not None else None
            ),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "recorded_at": row["recorded_at"],
            "action": action,
            "replayed": replayed,
        }

    def _decision_observation_row(
        self, run_id: str, purpose_id: str,
    ) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT o.run_id,o.purpose_id,o.mode,o.applied,o.effect_json,"
            "o.recorded_at,c.cache_key,c.request_json,c.status,c.response_json,"
            "c.response_sha256,c.billable_calls,c.usage_json,c.owner_id,c.error,"
            "c.created_at,c.updated_at FROM run_decision_observations o "
            "JOIN decision_cache c ON c.cache_key=o.cache_key "
            "WHERE o.run_id=? AND o.purpose_id=?",
            (run_id, purpose_id),
        ).fetchone()

    def claim_decision_observation(
        self,
        run_id: str,
        mode: str,
        request: dict[str, Any],
        owner_id: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Claim at most one decision call or reuse its content-addressed result."""
        from .decision import decision_cache_key, validate_decision_request

        normalized = validate_decision_request(request)
        if mode not in {"shadow", "advisory"}:
            raise ContractError("decision observation mode is invalid")
        if not isinstance(owner_id, str) or not owner_id:
            raise ContractError("decision observation owner is invalid")
        purpose_id = normalized["purpose"]["id"]
        cache_key = decision_cache_key(normalized)
        request_json = canonical_json(normalized)
        recorded_at = _authoritative_now(now).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute(
                "SELECT state FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            if run is None:
                raise ContractError("decision observation run does not exist")
            if run["state"] in TERMINAL_STATES:
                raise ConflictError(
                    "terminal run cannot claim a decision observation",
                )
            existing_link = self._decision_observation_row(run_id, purpose_id)
            if existing_link is not None:
                if (existing_link["cache_key"] != cache_key
                        or existing_link["mode"] != mode
                        or existing_link["request_json"] != request_json):
                    raise ConflictError(
                        "run decision purpose was already bound differently",
                    )
                status = existing_link["status"]
                action = "cached"
                if status == "reserved":
                    self.connection.execute(
                        "UPDATE decision_cache SET owner_id=?,updated_at=? "
                        "WHERE cache_key=? AND status='reserved' AND billable_calls=0",
                        (owner_id, recorded_at, cache_key),
                    )
                    action = "claimed"
                elif status == "running":
                    if existing_link["owner_id"] == owner_id:
                        action = "in_flight"
                    else:
                        self.connection.execute(
                            "UPDATE decision_cache SET status='indeterminate',"
                            "error=?,updated_at=? WHERE cache_key=? AND status='running'",
                            (
                                "prior launched decision call outcome is unknown",
                                recorded_at, cache_key,
                            ),
                        )
                        action = "abstain"
                elif status not in {"succeeded", "abstained"}:
                    action = "abstain"
                row = self._decision_observation_row(run_id, purpose_id)
                self.connection.execute("COMMIT")
                return self._decision_observation_payload(
                    row, action=action, replayed=True,
                )

            cached = self.connection.execute(
                "SELECT * FROM decision_cache WHERE cache_key=?", (cache_key,),
            ).fetchone()
            if cached is None:
                self.connection.execute(
                    "INSERT INTO decision_cache(cache_key,request_json,status,"
                    "billable_calls,owner_id,created_at,updated_at) "
                    "VALUES(?,?,'reserved',0,?,?,?)",
                    (
                        cache_key, request_json, owner_id, recorded_at,
                        recorded_at,
                    ),
                )
                action = "claimed"
                replayed = False
            else:
                if cached["request_json"] != request_json:
                    raise ConflictError(
                        "decision cache key has conflicting request evidence",
                    )
                action = (
                    "cached" if cached["status"] in {"succeeded", "abstained"}
                    else "in_flight" if cached["status"] in {"reserved", "running"}
                    else "abstain"
                )
                replayed = True
            self.connection.execute(
                "INSERT INTO run_decision_observations(run_id,purpose_id,cache_key,"
                "mode,applied,effect_json,recorded_at) VALUES(?,?,?,?,0,NULL,?)",
                (run_id, purpose_id, cache_key, mode, recorded_at),
            )
            row = self._decision_observation_row(run_id, purpose_id)
            self.connection.execute("COMMIT")
            return self._decision_observation_payload(
                row, action=action, replayed=replayed,
            )
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def launch_decision_call(
        self, cache_key: str, owner_id: str, *, now: datetime | None = None,
    ) -> dict[str, Any]:
        """Fence a possibly billable call before crossing the adapter boundary."""
        if not isinstance(cache_key, str) or len(cache_key) != 64:
            raise ContractError("decision cache key is invalid")
        if not isinstance(owner_id, str) or not owner_id:
            raise ContractError("decision observation owner is invalid")
        launched_at = _authoritative_now(now).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT status,billable_calls,owner_id FROM decision_cache "
                "WHERE cache_key=?", (cache_key,),
            ).fetchone()
            if row is None:
                raise ContractError("decision cache entry does not exist")
            if (row["status"] != "reserved" or row["billable_calls"] != 0
                    or row["owner_id"] != owner_id):
                raise ConflictError("decision call is not launchable")
            self.connection.execute(
                "UPDATE decision_cache SET status='running',billable_calls=1,"
                "updated_at=? WHERE cache_key=? AND status='reserved' "
                "AND billable_calls=0 AND owner_id=?",
                (launched_at, cache_key, owner_id),
            )
            if self.connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ConflictError("decision call launch fence changed")
            self.connection.execute("COMMIT")
            return {
                "cache_key": cache_key, "status": "running",
                "billable_calls": 1, "launched_at": launched_at,
            }
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def complete_decision_call(
        self,
        cache_key: str,
        owner_id: str,
        response: dict[str, Any],
        config: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Persist a valid typed response, or a redacted invalid verdict."""
        from .decision import validate_decision_response

        row = self.connection.execute(
            "SELECT request_json FROM decision_cache WHERE cache_key=?",
            (cache_key,),
        ).fetchone()
        if row is None:
            raise ContractError("decision cache entry does not exist")
        request = json.loads(row["request_json"])
        error = None
        try:
            normalized = validate_decision_response(request, response, config)
        except ContractError as exc:
            normalized = None
            error = str(exc)
        completed_at = _authoritative_now(now).isoformat()
        if normalized is None:
            status = "invalid"
            response_json = None
            response_sha256 = None
            usage = {
                "source": "unavailable", "billable_requests": 1,
                "input_tokens": None, "output_tokens": None, "cost_usd": None,
            }
        else:
            response_json = canonical_json(normalized)
            response_sha256 = hashlib.sha256(response_json.encode()).hexdigest()
            usage = normalized["usage"]
            abstained = (
                normalized["truncation"]["occurred"]
                or all(
                    recommendation["abstain_reason"] is not None
                    for recommendation in normalized["recommendations"].values()
                )
            )
            status = "abstained" if abstained else "succeeded"
        usage_json = canonical_json(usage)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.connection.execute(
                "SELECT status,owner_id,billable_calls FROM decision_cache "
                "WHERE cache_key=?", (cache_key,),
            ).fetchone()
            if (current is None or current["status"] != "running"
                    or current["owner_id"] != owner_id
                    or current["billable_calls"] != 1):
                raise ConflictError("decision call completion is fenced")
            self.connection.execute(
                "UPDATE decision_cache SET status=?,response_json=?,"
                "response_sha256=?,usage_json=?,error=?,updated_at=? "
                "WHERE cache_key=? AND status='running' AND owner_id=?",
                (
                    status, response_json, response_sha256, usage_json, error,
                    completed_at, cache_key, owner_id,
                ),
            )
            self.connection.execute("COMMIT")
            return {
                "cache_key": cache_key,
                "status": status,
                "response": normalized,
                "response_sha256": response_sha256,
                "usage": usage,
                "error": error,
                "billable_calls": 1,
                "completed_at": completed_at,
            }
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def finish_decision_without_call(
        self,
        cache_key: str,
        owner_id: str,
        status: str,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Record unavailable/cancelled preprocessing without inventing usage."""
        if status not in {"unavailable", "cancelled"}:
            raise ContractError("decision no-call status is invalid")
        if not isinstance(reason, str) or not reason:
            raise ContractError("decision no-call reason is invalid")
        recorded_at = _authoritative_now(now).isoformat()
        usage = {
            "source": "unavailable", "billable_requests": 0,
            "input_tokens": None, "output_tokens": None, "cost_usd": None,
        }
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT status,owner_id,billable_calls FROM decision_cache "
                "WHERE cache_key=?", (cache_key,),
            ).fetchone()
            if (row is None or row["status"] != "reserved"
                    or row["owner_id"] != owner_id or row["billable_calls"] != 0):
                raise ConflictError("decision no-call completion is fenced")
            self.connection.execute(
                "UPDATE decision_cache SET status=?,usage_json=?,error=?,updated_at=? "
                "WHERE cache_key=? AND status='reserved' AND owner_id=?",
                (
                    status, canonical_json(usage), reason, recorded_at,
                    cache_key, owner_id,
                ),
            )
            self.connection.execute("COMMIT")
            return {
                "cache_key": cache_key, "status": status,
                "billable_calls": 0, "usage": usage, "error": reason,
                "recorded_at": recorded_at,
            }
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def cancel_decision_observation(
        self, cache_key: str, owner_id: str, *, now: datetime | None = None,
    ) -> dict[str, Any]:
        """Cancel before launch, or preserve uncertainty after a launched call."""
        cancelled_at = _authoritative_now(now).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT status,billable_calls,owner_id FROM decision_cache "
                "WHERE cache_key=?", (cache_key,),
            ).fetchone()
            if row is None:
                raise ContractError("decision cache entry does not exist")
            if row["owner_id"] != owner_id:
                raise ConflictError("decision cancellation owner changed")
            if row["status"] not in {"reserved", "running"}:
                self.connection.execute("COMMIT")
                return {
                    "cache_key": cache_key, "status": row["status"],
                    "billable_calls": row["billable_calls"], "replayed": True,
                }
            status = "cancelled" if row["status"] == "reserved" else "indeterminate"
            reason = (
                "decision call cancelled before launch"
                if status == "cancelled"
                else "decision call cancelled after launch; outcome is unknown"
            )
            usage = {
                "source": "unavailable",
                "billable_requests": row["billable_calls"],
                "input_tokens": None, "output_tokens": None, "cost_usd": None,
            }
            self.connection.execute(
                "UPDATE decision_cache SET status=?,usage_json=?,error=?,updated_at=? "
                "WHERE cache_key=? AND status IN ('reserved','running')",
                (
                    status, canonical_json(usage), reason, cancelled_at,
                    cache_key,
                ),
            )
            self.connection.execute("COMMIT")
            return {
                "cache_key": cache_key, "status": status,
                "billable_calls": row["billable_calls"], "replayed": False,
            }
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def record_decision_effect(
        self,
        run_id: str,
        purpose_id: str,
        cache_key: str,
        effect: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Bind the frozen routing effect to this run without changing cache data."""
        if not isinstance(effect, dict):
            raise ContractError("run decision effect must be an object")
        effect_json = canonical_json(effect)
        applied = bool(effect.get("applied_roles"))
        recorded_at = _authoritative_now(now).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT cache_key,applied,effect_json FROM run_decision_observations "
                "WHERE run_id=? AND purpose_id=?",
                (run_id, purpose_id),
            ).fetchone()
            if row is None or row["cache_key"] != cache_key:
                raise ConflictError("run decision observation binding changed")
            if row["effect_json"] is not None:
                if (row["effect_json"] != effect_json
                        or bool(row["applied"]) != applied):
                    raise ConflictError("run decision effect was recorded differently")
                observation = self._decision_observation_row(run_id, purpose_id)
                self.connection.execute("COMMIT")
                return self._decision_observation_payload(
                    observation, action="cached", replayed=True,
                )
            self.connection.execute(
                "UPDATE run_decision_observations SET applied=?,effect_json=?,"
                "recorded_at=? WHERE run_id=? AND purpose_id=?",
                (
                    int(applied), effect_json, recorded_at, run_id, purpose_id,
                ),
            )
            observation = self._decision_observation_row(run_id, purpose_id)
            self.connection.execute("COMMIT")
            return self._decision_observation_payload(
                observation, action="recorded", replayed=False,
            )
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def decision_observation(
        self, run_id: str, purpose_id: str,
    ) -> dict[str, Any] | None:
        row = self._decision_observation_row(run_id, purpose_id)
        if row is None:
            return None
        return self._decision_observation_payload(
            row, action="read", replayed=True,
        )

    def decision_observations_for_run(self, run_id: str) -> list[dict[str, Any]]:
        purposes = self.connection.execute(
            "SELECT purpose_id FROM run_decision_observations "
            "WHERE run_id=? ORDER BY purpose_id",
            (run_id,),
        ).fetchall()
        return [
            self._decision_observation_payload(
                self._decision_observation_row(run_id, row["purpose_id"]),
                action="read", replayed=True,
            )
            for row in purposes
        ]

    def cancel_run_decision_observations(
        self, run_id: str, *, now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Fence every pending helper call when its owning run is cancelled."""
        cancelled_at = _authoritative_now(now).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            rows = self.connection.execute(
                "SELECT DISTINCT c.cache_key,c.status,c.billable_calls "
                "FROM run_decision_observations o JOIN decision_cache c "
                "ON c.cache_key=o.cache_key WHERE o.run_id=? "
                "AND c.status IN ('reserved','running')",
                (run_id,),
            ).fetchall()
            results = []
            for row in rows:
                status = (
                    "cancelled" if row["status"] == "reserved"
                    else "indeterminate"
                )
                reason = (
                    "owning run cancelled before decision launch"
                    if status == "cancelled"
                    else "owning run cancelled after decision launch; outcome is unknown"
                )
                usage = {
                    "source": "unavailable",
                    "billable_requests": row["billable_calls"],
                    "input_tokens": None,
                    "output_tokens": None,
                    "cost_usd": None,
                }
                self.connection.execute(
                    "UPDATE decision_cache SET status=?,usage_json=?,error=?,"
                    "updated_at=? WHERE cache_key=? AND status=?",
                    (
                        status, canonical_json(usage), reason, cancelled_at,
                        row["cache_key"], row["status"],
                    ),
                )
                results.append({
                    "cache_key": row["cache_key"],
                    "status": status,
                    "billable_calls": row["billable_calls"],
                })
            self.connection.execute("COMMIT")
            return results
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def project_final_outcome(self, run_id: str) -> dict[str, Any] | None:
        """Idempotently drain a new run's durable terminal projection request."""
        from .objective_outcomes import project_outcome
        job = self.connection.execute("SELECT completed_outcome_id FROM objective_outcome_jobs WHERE run_id=?", (run_id,)).fetchone()
        if job is None or job["completed_outcome_id"] is not None:
            return None
        self.connection.execute("BEGIN")
        try:
            run = self.run(run_id)
            if run["state"] not in TERMINAL_STATES:
                self.connection.execute("COMMIT")
                return None
            refs, documents = {}, {}
            for artifact in self.artifacts_for_run(run_id):
                content = Path(artifact["path"]).read_bytes()
                if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
                    raise ConflictError("objective projection artifact integrity is invalid")
                refs[artifact["name"]] = f"artifact:{artifact['id']}:{artifact['sha256']}"
                if artifact["name"] in {"receipt.json", "result-receipt.json"}:
                    documents[artifact["name"]] = json.loads(content)
            receipt = documents.get("receipt.json", documents.get("result-receipt.json"))
            if not isinstance(receipt, dict):
                raise ConflictError("objective projection requires a durable terminal receipt")
            row = self.connection.execute("SELECT assignment_json FROM experiment_assignments WHERE run_id=?", (run_id,)).fetchone()
            assignment = json.loads(row["assignment_json"]) if row else None
            outcome = project_outcome(run, receipt, self.attempts_for_run(run_id), refs, assignment)
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        result = self.record_outcome(run_id, outcome, _objective_projection=True)
        self.connection.execute("UPDATE objective_outcome_jobs SET completed_outcome_id=? WHERE run_id=? AND completed_outcome_id IS NULL", (outcome["outcome_id"], run_id))
        return result

    def record_outcome(
        self,
        run_id: str,
        outcome: dict[str, Any],
        *,
        now: datetime | None = None,
        _objective_projection: bool = False,
    ) -> dict[str, Any]:
        """Append one replay-safe final outcome or late correction."""
        from .learning import validate_outcome

        current = _authoritative_now(now)
        normalized = validate_outcome(outcome, now=current)
        payload = canonical_json(normalized)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                "SELECT run_id,payload_json,recorded_at FROM outcomes WHERE outcome_id=?",
                (normalized["outcome_id"],),
            ).fetchone()
            if existing is not None:
                if existing["run_id"] != run_id or existing["payload_json"] != payload:
                    raise ConflictError(
                        "outcome id was already used with different evidence",
                    )
                self.connection.execute("COMMIT")
                return {
                    "run_id": run_id,
                    "outcome": normalized,
                    "recorded_at": existing["recorded_at"],
                    "replayed": True,
                }
            run = self.connection.execute(
                "SELECT state,mutable_snapshot FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            if run is None:
                raise ContractError("run does not exist")
            if run["state"] not in TERMINAL_STATES:
                raise ConflictError("outcomes require a terminal run")
            try:
                snapshot = json.loads(run["mutable_snapshot"] or "null")
                roles = snapshot["routing"]["roles"].values()
                expected_selection_mode = (
                    "experimental"
                    if snapshot.get("experiment_assignment") is not None
                    else "pinned"
                    if any(role.get("source") == "override" for role in roles)
                    else "automatic"
                )
            except (KeyError, TypeError, json.JSONDecodeError):
                expected_selection_mode = None
            if (expected_selection_mode is not None
                    and normalized["selection_mode"] != expected_selection_mode):
                raise ConflictError("outcome selection mode does not match frozen routing")
            if normalized["kind"] == "final":
                if not _objective_projection and self.connection.execute("SELECT 1 FROM objective_outcome_jobs WHERE run_id=?", (run_id,)).fetchone():
                    raise ConflictError("public final outcomes are objective projections; use an explicit late correction")
                if normalized["verdict"] != run["state"]:
                    raise ConflictError("final outcome verdict does not match run state")
            else:
                corrected = self.connection.execute(
                    "SELECT run_id,kind,selection_mode,observed_at FROM outcomes "
                    "WHERE outcome_id=?",
                    (normalized["corrects_outcome_id"],),
                ).fetchone()
                if (corrected is None or corrected["run_id"] != run_id
                        or corrected["kind"] != "final"):
                    raise ConflictError("late outcome must correct this run's final outcome")
                if corrected["selection_mode"] != normalized["selection_mode"]:
                    raise ConflictError("late outcome selection mode changed")
                if datetime.fromisoformat(normalized["observed_at"]) < datetime.fromisoformat(
                    corrected["observed_at"],
                ):
                    raise ConflictError("late outcome predates the final outcome")

            attempts = {
                row["id"]: row
                for row in self.connection.execute(
                    "SELECT id,role,status,output_metadata FROM attempts WHERE run_id=?",
                    (run_id,),
                )
            }
            for contribution in normalized["contributions"]:
                attempt = attempts.get(contribution["attempt_id"])
                if attempt is None or attempt["role"] != contribution["role"]:
                    raise ConflictError("outcome contribution does not match run attempt")
                if attempt["status"] != "finished":
                    raise ConflictError("outcome contribution attempt is not finished")
                if attempt["output_metadata"]:
                    try:
                        metadata = json.loads(attempt["output_metadata"])
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise ConflictError("attempt output metadata is invalid") from exc
                    if (isinstance(metadata, dict) and metadata.get("failure") is not None
                            and (contribution["result"] != "failed"
                                 or contribution["independent_success"])):
                        raise ConflictError(
                            "failed attempt cannot receive successful contribution credit",
                        )
            for repair in normalized["lead_repairs"]:
                attempt_id = repair["lead_attempt_id"]
                if attempt_id is None:
                    continue
                attempt = attempts.get(attempt_id)
                if attempt is None or attempt["role"] != "lead" or attempt["status"] != "finished":
                    raise ConflictError("lead repair does not match a finished lead attempt")

            recorded_at = current.isoformat()
            self.connection.execute(
                "INSERT INTO outcomes(outcome_id,run_id,kind,verdict,selection_mode,"
                "observed_at,corrects_outcome_id,payload_json,payload_sha256,recorded_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    normalized["outcome_id"], run_id, normalized["kind"],
                    normalized["verdict"], normalized["selection_mode"],
                    normalized["observed_at"], normalized["corrects_outcome_id"],
                    payload, digest, recorded_at,
                ),
            )
            self.connection.execute("COMMIT")
            return {
                "run_id": run_id,
                "outcome": normalized,
                "recorded_at": recorded_at,
                "replayed": False,
            }
        except sqlite3.IntegrityError as exc:
            self.connection.execute("ROLLBACK")
            raise ConflictError("run already has a final outcome") from exc
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def outcomes_for_run(self, run_id: str) -> list[dict[str, Any]]:
        # Repair only the requested run. Corrupt evidence in another pending
        # projection must not prevent opening the ledger or observing good runs.
        self.project_final_outcome(run_id)
        if not self.connection.execute(
            "SELECT 1 FROM runs WHERE id=?", (run_id,),
        ).fetchone():
            raise ContractError("run does not exist")
        return [
            {
                "run_id": row["run_id"],
                "outcome": json.loads(row["payload_json"]),
                "payload_sha256": row["payload_sha256"],
                "recorded_at": row["recorded_at"],
            }
            for row in self.connection.execute(
                "SELECT run_id,payload_json,payload_sha256,recorded_at "
                "FROM outcomes WHERE run_id=? ORDER BY observed_at,id",
                (run_id,),
            )
        ]

    def repair_project_outcomes(self, project: Path) -> None:
        """Repair before, never inside, a caller's consistent read transaction."""
        if self.connection.in_transaction:
            raise ConflictError("project outcome repair requires an independent transaction")
        common = str(git_common_dir(project))
        for job in self.connection.execute(
                "SELECT j.run_id FROM objective_outcome_jobs j JOIN runs r ON r.id=j.run_id "
                "JOIN projects p ON p.id=r.project_id WHERE p.git_common_dir=? "
                "AND j.completed_outcome_id IS NULL AND r.state IN ('succeeded','failed','cancelled')",
                (common,)).fetchall():
            self.project_final_outcome(job["run_id"])

    def learning_report(
        self, project: Path, *, now: datetime | None = None, _repair_pending: bool = True,
    ) -> dict[str, Any]:
        """Repair pending public projections and compare with explicit missingness."""
        from .learning import build_comparison_report

        if _repair_pending:
            self.repair_project_outcomes(project)
        common_dir = git_common_dir(project)
        project_row = self.connection.execute(
            "SELECT id FROM projects WHERE git_common_dir=?", (str(common_dir),),
        ).fetchone()
        if project_row is None:
            terminal_runs = []
            outcome_records = []
            attempt_profiles = {}
            project_id = None
        else:
            project_id = project_row["id"]
            terminal_runs = [
                {"run_id": row["id"], "state": row["state"]}
                for row in self.connection.execute(
                    "SELECT id,state FROM runs WHERE project_id=? "
                    "AND state IN ('succeeded','failed','cancelled') ORDER BY created_at,id",
                    (project_id,),
                )
            ]
            outcome_records = [
                {"run_id": row["run_id"], "outcome": json.loads(row["payload_json"])}
                for row in self.connection.execute(
                    "SELECT o.run_id,o.payload_json FROM outcomes o "
                    "JOIN runs r ON r.id=o.run_id WHERE r.project_id=? "
                    "ORDER BY o.observed_at,o.id",
                    (project_id,),
                )
            ]
            attempt_profiles = {
                row["id"]: row["profile_id"]
                for row in self.connection.execute(
                    "SELECT a.id,a.profile_id FROM attempts a "
                    "JOIN runs r ON r.id=a.run_id WHERE r.project_id=?",
                    (project_id,),
                )
            }
        return build_comparison_report(
            project_id=project_id,
            project_path=str(project.resolve(strict=True)),
            terminal_runs=terminal_runs,
            outcome_records=outcome_records,
            attempt_profiles=attempt_profiles,
            generated_at=_authoritative_now(now).isoformat(),
        )

    def evaluate_learning_experiment(
        self, experiment: dict[str, Any], *, now: datetime | None = None,
        revision_id: str | None = None,
        previous_evaluation_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Persist an original evaluation or an explicit append-only review."""
        from .experiment_eligibility import current_evidence, saved_evaluation
        from .experiment_provenance import require_sha256
        from .learning import evaluate_experiment, validate_experiment

        if (revision_id is None) != (previous_evaluation_sha256 is None):
            raise ContractError("evaluation revision id and predecessor must be supplied together")
        if revision_id is not None:
            if not isinstance(revision_id, str) or not revision_id.strip() or len(revision_id) > 128:
                raise ContractError("evaluation revision id is invalid")
            require_sha256(previous_evaluation_sha256, "previous evaluation")
        spec = validate_experiment(experiment)
        project_path = Path(spec["project_path"]).resolve(strict=True)
        # V2 hashes the exact predeclared specification. Resolving a symlink
        # for project lookup must not rewrite that declaration after launch.
        if spec["schema_version"] == 1:
            spec = {**spec, "project_path": str(project_path)}
        spec_json = canonical_json(spec)
        spec_sha256 = hashlib.sha256(spec_json.encode()).hexdigest()
        current = _authoritative_now(now)
        common_dir = git_common_dir(project_path)
        for assigned in self.connection.execute(
                "SELECT run_id FROM experiment_assignments WHERE experiment_id=?",
                (spec["experiment_id"],)).fetchall():
            self.project_final_outcome(assigned["run_id"])
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = saved_evaluation(self.connection, spec["experiment_id"])
            if existing is not None and existing["spec_json"] != spec_json:
                raise ConflictError(
                    "experiment id was already used with a different specification",
                )
            if revision_id is not None:
                if existing is None or spec["schema_version"] != 2:
                    raise ContractError("explicit evaluation review requires an original v2 evaluation")
                revision = self.connection.execute(
                    "SELECT experiment_id,previous_evaluation_sha256,evaluation_sha256 "
                    "FROM experiment_evaluation_revisions WHERE revision_id=?", (revision_id,),
                ).fetchone()
                if revision is not None:
                    if (revision["experiment_id"] != spec["experiment_id"]
                            or revision["previous_evaluation_sha256"] != previous_evaluation_sha256):
                        raise ConflictError("evaluation revision id was already used with different evidence")
                    existing = saved_evaluation(self.connection, spec["experiment_id"], revision["evaluation_sha256"])
                else:
                    if existing["evaluation_sha256"] != previous_evaluation_sha256:
                        raise ConflictError("evaluation revision predecessor is no longer the latest evaluation")
                    existing = None
            if existing is not None:
                eligibility = current_evidence(self.connection, existing, now=current)
                if spec["schema_version"] == 2 and not eligibility["eligible"]:
                    raise ContractError("experiment evidence changed; saved evaluation is stale and requires explicit review/revision: " + ", ".join(eligibility["reasons"]))
                self.connection.execute("COMMIT")
                return {
                    "experiment": spec,
                    "evaluation": existing["evaluation"],
                    "evaluation_sha256": existing["evaluation_sha256"],
                    "recorded_at": existing["recorded_at"],
                    "revision_id": existing["revision_id"],
                    "previous_evaluation_sha256": existing["previous_evaluation_sha256"],
                    "eligibility": eligibility,
                    "replayed": True,
                }
            project = self.connection.execute(
                "SELECT id FROM projects WHERE git_common_dir=?", (str(common_dir),),
            ).fetchone()
            project_id = project["id"] if project is not None else None
            chains: dict[str, dict[str, Any]] = {}
            if spec["schema_version"] == 2:
                from .experiment_evidence import read_experiment_chains

                chains = read_experiment_chains(
                    self.connection, spec=spec, project_id=project_id,
                    project_common_dir=str(common_dir), evaluated_at=current.isoformat(),
                )
            else:
                # Historical v1 decoding remains separate from v2 authority.
                records = []
                if project_id is not None:
                    records = self.connection.execute(
                        "SELECT o.payload_json FROM outcomes o "
                        "JOIN runs r ON r.id=o.run_id WHERE r.project_id=? "
                        "ORDER BY o.observed_at,o.id",
                        (project_id,),
                    ).fetchall()
                corrections: dict[str, list[dict[str, Any]]] = {}
                for record in records:
                    outcome = json.loads(record["payload_json"])
                    if outcome["kind"] == "final":
                        chains[outcome["outcome_id"]] = {
                            "final": outcome, "late_corrections": [],
                        }
                    else:
                        corrections.setdefault(
                            outcome["corrects_outcome_id"], [],
                        ).append(outcome)
                for outcome_id, history in corrections.items():
                    if outcome_id in chains:
                        chains[outcome_id]["late_corrections"] = history
            evaluation = evaluate_experiment(
                spec, chains, evaluated_at=current.isoformat(),
                project_common_dir=str(common_dir),
            )
            evaluation_json = canonical_json(evaluation)
            evaluation_sha256 = hashlib.sha256(evaluation_json.encode()).hexdigest()
            recorded_at = current.isoformat()
            if revision_id is None:
                self.connection.execute(
                    "INSERT INTO experiments(experiment_id,project_id,project_path,spec_json,"
                    "spec_sha256,evaluation_json,evaluation_sha256,verdict,recorded_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        spec["experiment_id"], project_id, spec["project_path"],
                        spec_json, spec_sha256, evaluation_json, evaluation_sha256,
                        evaluation["verdict"], recorded_at,
                    ),
                )
            else:
                if saved_evaluation(self.connection, spec["experiment_id"], evaluation_sha256) is not None:
                    raise ConflictError("evaluation review must create a distinct immutable receipt")
                self.connection.execute(
                    "INSERT INTO experiment_evaluation_revisions(revision_id,experiment_id,"
                    "previous_evaluation_sha256,evaluation_json,evaluation_sha256,verdict,recorded_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (revision_id, spec["experiment_id"], previous_evaluation_sha256,
                     evaluation_json, evaluation_sha256, evaluation["verdict"], recorded_at),
                )
            saved = saved_evaluation(self.connection, spec["experiment_id"], evaluation_sha256)
            eligibility = current_evidence(self.connection, saved, now=current)
            self.connection.execute("COMMIT")
            return {
                "experiment": spec,
                "evaluation": evaluation,
                "evaluation_sha256": evaluation_sha256,
                "recorded_at": recorded_at,
                "revision_id": revision_id,
                "previous_evaluation_sha256": previous_evaluation_sha256,
                "eligibility": eligibility,
                "replayed": False,
            }
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def learning_proposal_inputs(
        self, project: Path, *, now: datetime | None = None,
    ) -> dict[str, Any]:
        """Read a consistent report and the latest frozen project experiment."""
        from .experiment_eligibility import current_evidence, saved_evaluation
        project_path = project.resolve(strict=True)
        current = _authoritative_now(now)
        self.repair_project_outcomes(project_path)
        self.connection.execute("BEGIN")
        try:
            report = self.learning_report(project_path, now=current, _repair_pending=False)
            if report["project_id"] is None:
                row = self.connection.execute(
                    "SELECT experiment_id FROM experiments WHERE project_path=? "
                    "ORDER BY recorded_at DESC,id DESC LIMIT 1",
                    (str(project_path),),
                ).fetchone()
            else:
                row = self.connection.execute(
                    "SELECT experiment_id FROM experiments WHERE project_id=? OR "
                    "(project_id IS NULL AND project_path=?) "
                    "ORDER BY recorded_at DESC,id DESC LIMIT 1",
                    (report["project_id"], str(project_path)),
                ).fetchone()
            experiment = None
            if row is not None:
                saved = saved_evaluation(self.connection, row["experiment_id"])
                experiment = {
                    "experiment": saved["spec"], "spec_sha256": saved["spec_sha256"],
                    "evaluation": saved["evaluation"], "evaluation_sha256": saved["evaluation_sha256"],
                    "recorded_at": saved["recorded_at"],
                    "eligibility": current_evidence(self.connection, saved, now=current),
                }
            self.connection.execute("COMMIT")
            return {"report": report, "experiment": experiment}
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _profile_record(profile: dict[str, Any]) -> tuple[str, str]:
        from .lifecycle import profile_fingerprint

        payload = canonical_json(profile)
        return payload, profile_fingerprint(profile)

    def _insert_concrete_profile(
        self, profile: dict[str, Any], recorded_at: str,
    ) -> tuple[str, str]:
        payload, digest = self._profile_record(profile)
        existing = self.connection.execute(
            "SELECT profile_json,profile_sha256 FROM concrete_profiles "
            "WHERE profile_id=?",
            (profile["id"],),
        ).fetchone()
        if existing is not None:
            if (existing["profile_json"] != payload
                    or existing["profile_sha256"] != digest):
                raise ConflictError(
                    "profile id was already used with different concrete settings",
                )
            return payload, digest
        self.connection.execute(
            "INSERT INTO concrete_profiles(profile_id,profile_json,profile_sha256,"
            "recorded_at) VALUES(?,?,?,?)",
            (profile["id"], payload, digest, recorded_at),
        )
        return payload, digest

    def _insert_profile_template(
        self, template: dict[str, Any], recorded_at: str,
    ) -> tuple[str, str]:
        payload = canonical_json(template)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        existing = self.connection.execute(
            "SELECT payload_json,payload_sha256 FROM profile_templates "
            "WHERE template_id=?",
            (template["template_id"],),
        ).fetchone()
        if existing is not None:
            if (existing["payload_json"] != payload
                    or existing["payload_sha256"] != digest):
                raise ConflictError(
                    "template id was already used with a different policy",
                )
            return payload, digest
        self.connection.execute(
            "INSERT INTO profile_templates(template_id,alias,update_mode,policy_id,"
            "policy_version,payload_json,payload_sha256,recorded_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                template["template_id"], template["alias"],
                template["update_mode"], template["policy"]["id"],
                template["policy"]["version"], payload, digest, recorded_at,
            ),
        )
        return payload, digest

    def register_profile_template(
        self, template: dict[str, Any], *, now: datetime | None = None,
    ) -> dict[str, Any]:
        """Register an immutable reviewed lifecycle template."""
        from .lifecycle import validate_profile_template

        normalized = validate_profile_template(template)
        recorded_at = _authoritative_now(now).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            before = self.connection.execute(
                "SELECT 1 FROM profile_templates WHERE template_id=?",
                (normalized["template_id"],),
            ).fetchone()
            _, digest = self._insert_profile_template(normalized, recorded_at)
            self.connection.execute("COMMIT")
            return {
                "template": normalized,
                "template_sha256": digest,
                "recorded_at": recorded_at,
                "replayed": before is not None,
            }
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def bootstrap_profile_binding(
        self,
        template: dict[str, Any],
        profile: dict[str, Any],
        *,
        version: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Import one reviewed baseline binding without fabricating qualification."""
        from .lifecycle import (
            profile_template_violation,
            validate_profile_template,
        )

        normalized_template = validate_profile_template(template)
        profile_payload, profile_digest = self._profile_record(profile)
        profile = json.loads(profile_payload)
        if type(version) is not int or version < 1:
            raise ContractError("baseline binding version must be positive")
        violation = profile_template_violation(profile, normalized_template)
        if violation is not None:
            raise ContractError(f"baseline profile violates template: {violation}")
        if profile["quality_status"] != "proven":
            raise ContractError("baseline profile must already be proven")
        recorded_at = _authoritative_now(now).isoformat()
        alias = normalized_template["alias"]
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self._insert_profile_template(normalized_template, recorded_at)
            self._insert_concrete_profile(profile, recorded_at)
            existing = self.connection.execute(
                "SELECT template_id,profile_id,qualification_id,version,updated_at "
                "FROM profile_bindings WHERE alias=?",
                (alias,),
            ).fetchone()
            if existing is not None:
                if (existing["template_id"] != normalized_template["template_id"]
                        or existing["profile_id"] != profile["id"]
                        or existing["qualification_id"] is not None
                        or existing["version"] != version):
                    raise ConflictError(
                        "baseline alias is already bound differently",
                    )
                self.connection.execute("COMMIT")
                return {
                    "binding": dict(existing),
                    "profile_sha256": profile_digest,
                    "replayed": True,
                }
            self.connection.execute(
                "INSERT INTO profile_bindings(alias,template_id,profile_id,"
                "qualification_id,version,updated_at) VALUES(?,?,?,NULL,?,?)",
                (
                    alias, normalized_template["template_id"], profile["id"],
                    version, recorded_at,
                ),
            )
            self.connection.execute(
                "INSERT INTO profile_binding_versions(alias,version,template_id,"
                "profile_id,qualification_id,decision_id,recorded_at) "
                "VALUES(?,?,?,?,NULL,NULL,?)",
                (
                    alias, version, normalized_template["template_id"],
                    profile["id"], recorded_at,
                ),
            )
            self.connection.execute("COMMIT")
            return {
                "binding": {
                    "alias": alias,
                    "template_id": normalized_template["template_id"],
                    "profile_id": profile["id"],
                    "qualification_id": None,
                    "version": version,
                    "updated_at": recorded_at,
                },
                "profile_sha256": profile_digest,
                "replayed": False,
            }
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def _qualification_evidence(
        self, record: dict[str, Any], *, now: datetime,
    ) -> tuple[list[str], dict[str, Any] | None]:
        """The same current-evidence and context checks for every consumer."""
        from .experiment_eligibility import require_current_evidence
        from .lifecycle import qualification_gate_failures, validate_profile_template, profile_fingerprint

        template_row = self.connection.execute(
            "SELECT payload_json,payload_sha256 FROM profile_templates WHERE template_id=?",
            (record["template_id"],),
        ).fetchone()
        if template_row is None:
            raise ContractError("qualification template is not registered")
        if hashlib.sha256(template_row["payload_json"].encode()).hexdigest() != template_row["payload_sha256"]:
            raise ContractError("qualification template hash is invalid")
        template = validate_profile_template(json.loads(template_row["payload_json"]))
        failures = qualification_gate_failures(record, template)
        eligibility = None
        if record["experiment_id"] is not None:
            experiment, eligibility = require_current_evidence(
                self.connection, record["experiment_id"], record["evaluation_sha256"], now=now,
            )
            spec, evaluation = experiment["spec"], experiment["evaluation"]
            variable = spec["variable"]
            if (variable["alias"] != record["alias"]
                    or variable["candidate_profile_id"] != record["candidate_profile"]["id"]
                    or variable["candidate_profile_sha256"] != profile_fingerprint(record["candidate_profile"])):
                raise ContractError("qualification candidate fingerprint does not match the tested experiment")
            contexts = self.connection.execute(
                "SELECT assignment_json,snapshot_json FROM experiment_assignments "
                "WHERE experiment_id=? AND arm='candidate'", (record["experiment_id"],),
            ).fetchall()
            if not contexts or any(
                    json.loads(row["assignment_json"])["role"] != variable["role"]
                    or json.loads(row["snapshot_json"])["task"]["task_class"] != record["task_class"]
                    for row in contexts):
                raise ContractError("qualification role/task-class context does not match actual frozen tasks")
            metrics = evaluation["metrics"]
            escaped = sum(metrics[split]["candidate_escaped_defects"] for split in ("evaluation", "held_out"))
            if (record["measured"]["evaluation_pairs"] != metrics["evaluation"]["available_pairs"]
                    or record["measured"]["held_out_pairs"] != metrics["held_out"]["available_pairs"]
                    or record["measured"]["critical_defects"] != escaped):
                raise ContractError("qualification measurements do not match saved evaluation")
            # Saved evaluations currently contain no verified paired whole-run
            # latency or usage measurement. UTC attempt timestamps are not
            # monotonic latency, and partial native token reports are not a
            # complete paired usage measure. Preserve unknown rather than
            # letting caller-supplied ratios grant lifecycle authority.
            if any(record["measured"][field] is not None for field in ("latency_ratio", "usage_ratio")):
                raise ContractError("qualification measurements contain unmeasured latency/usage ratios")
            if evaluation["verdict"] != "promotion_proposal":
                failures.append("experiment_did_not_propose_promotion")
        failures = sorted(set(failures))
        if record["verdict"] == "qualified" and failures:
            raise ContractError("qualification gate did not pass: " + ", ".join(failures))
        return failures, eligibility

    def _require_current_qualification(
        self, qualification_id: str, *, now: datetime,
    ) -> dict[str, Any]:
        from .lifecycle import validate_qualification

        row = self.connection.execute(
            "SELECT * FROM qualification_runs WHERE qualification_id=?", (qualification_id,),
        ).fetchone()
        if row is None or row["verdict"] != "qualified":
            raise ContractError("binding change requires a qualified candidate")
        if hashlib.sha256(row["payload_json"].encode()).hexdigest() != row["payload_sha256"]:
            raise ContractError("saved qualification hash is invalid")
        record = validate_qualification(json.loads(row["payload_json"]))
        if any(row[key] != record[key] for key in (
                "qualification_id", "alias", "template_id", "experiment_id", "evaluation_sha256", "verdict")) or row["profile_id"] != record["candidate_profile"]["id"]:
            raise ContractError("saved qualification identity is invalid")
        failures, _ = self._qualification_evidence(record, now=now)
        if failures or canonical_json(failures) != row["gate_failures_json"]:
            raise ContractError("saved qualification gates are inconsistent")
        profile_row = self.connection.execute(
            "SELECT profile_json,profile_sha256 FROM concrete_profiles WHERE profile_id=?", (row["profile_id"],),
        ).fetchone()
        payload, digest = self._profile_record(record["candidate_profile"])
        if profile_row is None or profile_row["profile_json"] != payload or profile_row["profile_sha256"] != digest:
            raise ContractError("saved qualification concrete profile differs from tested evidence")
        return record

    def record_profile_qualification(
        self, qualification: dict[str, Any], *, now: datetime | None = None,
    ) -> dict[str, Any]:
        """Persist bounded qualification evidence after checking saved evaluation."""
        from .lifecycle import validate_qualification

        record = validate_qualification(qualification)
        payload = canonical_json(record)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        recorded_at = _authoritative_now(now).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                "SELECT payload_json,payload_sha256,gate_failures_json,recorded_at "
                "FROM qualification_runs WHERE qualification_id=?",
                (record["qualification_id"],),
            ).fetchone()
            if existing is not None:
                if existing["payload_json"] != payload:
                    raise ConflictError(
                        "qualification id was already used with different evidence",
                    )
                failures, eligibility = self._qualification_evidence(record, now=_authoritative_now(now))
                if (existing["payload_sha256"] != digest
                        or canonical_json(failures) != existing["gate_failures_json"]):
                    raise ContractError("saved qualification evidence hash/gates are invalid")
                self.connection.execute("COMMIT")
                return {
                    "qualification": record,
                    "qualification_sha256": existing["payload_sha256"],
                    "gate_failures": json.loads(existing["gate_failures_json"]),
                    "recorded_at": existing["recorded_at"],
                    "eligibility": eligibility,
                    "replayed": True,
                }
            failures, eligibility = self._qualification_evidence(record, now=_authoritative_now(now))
            self._insert_concrete_profile(
                record["candidate_profile"], recorded_at,
            )
            self.connection.execute(
                "INSERT INTO qualification_runs(qualification_id,alias,template_id,"
                "profile_id,experiment_id,evaluation_sha256,verdict,payload_json,"
                "payload_sha256,gate_failures_json,recorded_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record["qualification_id"], record["alias"],
                    record["template_id"], record["candidate_profile"]["id"],
                    record["experiment_id"], record["evaluation_sha256"],
                    record["verdict"], payload, digest,
                    canonical_json(failures), recorded_at,
                ),
            )
            self.connection.execute("COMMIT")
            return {
                "qualification": record,
                "qualification_sha256": digest,
                "gate_failures": failures,
                "recorded_at": recorded_at,
                "eligibility": eligibility,
                "replayed": False,
            }
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def profile_binding(self, alias: str) -> dict[str, Any] | None:
        if not isinstance(alias, str) or not alias:
            raise ContractError("profile binding alias is invalid")
        row = self.connection.execute(
            "SELECT b.alias,b.template_id,b.profile_id,b.qualification_id,b.version,"
            "b.updated_at,p.profile_json,p.profile_sha256,t.payload_json AS template_json,"
            "t.payload_sha256 AS template_sha256 FROM profile_bindings b "
            "JOIN concrete_profiles p ON p.profile_id=b.profile_id "
            "JOIN profile_templates t ON t.template_id=b.template_id "
            "WHERE b.alias=?",
            (alias,),
        ).fetchone()
        if row is None:
            return None
        return {
            "alias": row["alias"],
            "template_id": row["template_id"],
            "profile_id": row["profile_id"],
            "qualification_id": row["qualification_id"],
            "version": row["version"],
            "updated_at": row["updated_at"],
            "profile": json.loads(row["profile_json"]),
            "profile_sha256": row["profile_sha256"],
            "template": json.loads(row["template_json"]),
            "template_sha256": row["template_sha256"],
        }

    def change_profile_binding(
        self, change: dict[str, Any], *, now: datetime | None = None,
    ) -> dict[str, Any]:
        """CAS-promote or roll back one alias and persist its decision receipt."""
        from .lifecycle import (
            guarded_change_violation,
            validate_binding_change,
        )

        request = validate_binding_change(change)
        request_json = canonical_json(request)
        request_sha256 = hashlib.sha256(request_json.encode()).hexdigest()
        recorded_at = _authoritative_now(now).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                "SELECT request_sha256,receipt_json,receipt_sha256,recorded_at "
                "FROM binding_decisions WHERE decision_id=?",
                (request["decision_id"],),
            ).fetchone()
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise ConflictError(
                        "decision id was already used with a different request",
                    )
                self.connection.execute("COMMIT")
                return {
                    "receipt": json.loads(existing["receipt_json"]),
                    "receipt_sha256": existing["receipt_sha256"],
                    "recorded_at": existing["recorded_at"],
                    "replayed": True,
                }
            current = self.connection.execute(
                "SELECT b.template_id,b.profile_id,b.qualification_id,b.version,"
                "p.profile_json,p.profile_sha256,t.payload_json,t.payload_sha256 "
                "FROM profile_bindings b "
                "JOIN concrete_profiles p ON p.profile_id=b.profile_id "
                "JOIN profile_templates t ON t.template_id=b.template_id "
                "WHERE b.alias=?",
                (request["alias"],),
            ).fetchone()
            if current is None:
                raise ContractError("profile binding does not exist")
            if current["version"] != request["expected_binding_version"]:
                raise ConflictError("profile binding version changed")
            current_template = json.loads(current["payload_json"])
            current_profile = json.loads(current["profile_json"])
            if (request["actor"] == "guarded_auto"
                    and current_template["update_mode"] != "guarded_auto"):
                raise ContractError(
                    "guarded automatic promotion is not enabled",
                )
            qualification_payload = None
            rollback_evaluation = None
            if request["action"] == "promote":
                qualification = self.connection.execute(
                    "SELECT q.alias,q.template_id,q.profile_id,q.verdict,"
                    "q.payload_json,q.payload_sha256,q.gate_failures_json,"
                    "p.profile_json,p.profile_sha256,t.payload_json AS template_json,"
                    "t.payload_sha256 AS template_sha256 FROM qualification_runs q "
                    "JOIN concrete_profiles p ON p.profile_id=q.profile_id "
                    "JOIN profile_templates t ON t.template_id=q.template_id "
                    "WHERE q.qualification_id=?",
                    (request["qualification_id"],),
                ).fetchone()
                if (qualification is None or qualification["alias"] != request["alias"]
                        or qualification["verdict"] != "qualified"
                        or json.loads(qualification["gate_failures_json"])):
                    raise ContractError(
                        "binding promotion requires a qualified candidate",
                    )
                target_template = json.loads(qualification["template_json"])
                target_profile = json.loads(qualification["profile_json"])
                target_template_id = qualification["template_id"]
                target_profile_sha256 = qualification["profile_sha256"]
                target_template_sha256 = qualification["template_sha256"]
                target_qualification_id = request["qualification_id"]
                qualification_payload = self._require_current_qualification(
                    request["qualification_id"], now=_authoritative_now(now),
                )
                from .experiment_eligibility import saved_evaluation

                tested = saved_evaluation(
                    self.connection, qualification_payload["experiment_id"],
                    qualification_payload["evaluation_sha256"],
                )["spec"]["variable"]
                if (tested["control_profile_id"] != current["profile_id"]
                        or tested["control_profile_sha256"] != current["profile_sha256"]):
                    raise ContractError("promotion evidence does not compare the current incumbent")
                if (request["actor"] == "guarded_auto"
                        and target_template_id != current["template_id"]):
                    raise ContractError(
                        "guarded automation cannot change lifecycle policy",
                    )
                guarded_violation = (
                    guarded_change_violation(current_profile, target_profile)
                    if request["actor"] == "guarded_auto" else None
                )
                if guarded_violation is not None:
                    raise ContractError(guarded_violation)
            else:
                rollback = request["rollback_target"]
                target = self.connection.execute(
                    "SELECT v.template_id,v.profile_id,v.qualification_id,"
                    "p.profile_json,p.profile_sha256,t.payload_json AS template_json,"
                    "t.payload_sha256 AS template_sha256 FROM profile_binding_versions v "
                    "JOIN concrete_profiles p ON p.profile_id=v.profile_id "
                    "JOIN profile_templates t ON t.template_id=v.template_id "
                    "WHERE v.alias=? AND v.version=?",
                    (request["alias"], rollback["binding_version"]),
                ).fetchone()
                if (target is None or target["profile_id"] != rollback["profile_id"]
                        or rollback["binding_version"] >= current["version"]):
                    raise ContractError("rollback target is not a prior binding")
                target_profile = json.loads(target["profile_json"])
                if target_profile["quality_status"] == "suspended":
                    raise ContractError("rollback target is suspended")
                target_template = json.loads(target["template_json"])
                target_template_id = target["template_id"]
                target_profile_sha256 = target["profile_sha256"]
                target_template_sha256 = target["template_sha256"]
                target_qualification_id = target["qualification_id"]
                if target_qualification_id is not None:
                    qualification_payload = self._require_current_qualification(
                        target_qualification_id, now=_authoritative_now(now),
                    )
                if (request["actor"] == "guarded_auto"
                        and target_template_id != current["template_id"]):
                    raise ContractError(
                        "guarded automation cannot change lifecycle policy",
                    )
                from .experiment_eligibility import require_current_evidence

                regression, regression_eligibility = require_current_evidence(
                    self.connection, request["experiment_id"], request["evaluation_sha256"],
                    now=_authoritative_now(now),
                )
                if regression["verdict"] != "no_change":
                    raise ContractError(
                        "rollback requires saved no-change regression evidence",
                    )
                regression_spec = regression["spec"]
                regression_evaluation = regression["evaluation"]
                variable = regression_spec["variable"]
                if (variable["alias"] != request["alias"]
                        or variable["candidate_profile_id"] != current["profile_id"]
                        or variable["control_profile_id"] != target_profile["id"]
                        or variable["candidate_profile_sha256"] != current["profile_sha256"]
                        or variable["control_profile_sha256"] != target_profile_sha256):
                    raise ContractError(
                        "rollback experiment does not compare the active and target profiles",
                    )
                if any(
                        regression_evaluation["metrics"][split]["available_pairs"] < regression_spec["gate"][minimum]
                        for split, minimum in (("evaluation", "min_evaluation_pairs"), ("held_out", "min_held_out_pairs"))):
                    raise ContractError("rollback requires complete evaluation and held-out regression pairs")
                rollback_evaluation = {
                    "experiment_id": request["experiment_id"],
                    "evaluation_sha256": request["evaluation_sha256"],
                    "verdict": regression["verdict"],
                    "reasons": regression_evaluation["reasons"],
                    "metrics": regression_evaluation["metrics"],
                    "failures": regression_evaluation["failures"],
                    "eligibility": regression_eligibility,
                }
            if target_profile["id"] == current["profile_id"]:
                raise ConflictError("binding already targets the requested profile")
            new_version = current["version"] + 1
            receipt = {
                "schema_version": 1,
                "decision_id": request["decision_id"],
                "action": request["action"],
                "alias": request["alias"],
                "actor": request["actor"],
                "reason": request["reason"],
                "evidence_refs": request["evidence_refs"],
                "from": {
                    "binding_version": current["version"],
                    "profile_id": current["profile_id"],
                    "profile_sha256": current["profile_sha256"],
                    "template_id": current["template_id"],
                    "template_sha256": current["payload_sha256"],
                },
                "to": {
                    "binding_version": new_version,
                    "profile_id": target_profile["id"],
                    "profile_sha256": target_profile_sha256,
                    "template_id": target_template_id,
                    "template_sha256": target_template_sha256,
                },
                "qualification_id": target_qualification_id,
                "qualification": qualification_payload,
                "policy": target_template["policy"],
                "rollback_target": {
                    "profile_id": current["profile_id"],
                    "binding_version": current["version"],
                },
                "requested_rollback_target": (
                    request["rollback_target"]
                    if request["action"] == "rollback" else None
                ),
                "rollback_evaluation": rollback_evaluation,
                "effective_at": recorded_at,
                "affects_new_runs_only": True,
            }
            receipt_json = canonical_json(receipt)
            receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
            self.connection.execute(
                "UPDATE profile_bindings SET template_id=?,profile_id=?,"
                "qualification_id=?,version=?,updated_at=? WHERE alias=? AND version=?",
                (
                    target_template_id, target_profile["id"],
                    target_qualification_id, new_version, recorded_at,
                    request["alias"], current["version"],
                ),
            )
            if self.connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ConflictError("profile binding version changed")
            self.connection.execute(
                "INSERT INTO profile_binding_versions(alias,version,template_id,"
                "profile_id,qualification_id,decision_id,recorded_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    request["alias"], new_version, target_template_id,
                    target_profile["id"], target_qualification_id,
                    request["decision_id"], recorded_at,
                ),
            )
            self.connection.execute(
                "INSERT INTO binding_decisions(decision_id,alias,action,actor,"
                "from_version,to_version,from_profile_id,to_profile_id,"
                "qualification_id,request_sha256,receipt_json,receipt_sha256,"
                "recorded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    request["decision_id"], request["alias"], request["action"],
                    request["actor"], current["version"], new_version,
                    current["profile_id"], target_profile["id"],
                    target_qualification_id, request_sha256, receipt_json,
                    receipt_sha256, recorded_at,
                ),
            )
            self.connection.execute("COMMIT")
            return {
                "receipt": receipt,
                "receipt_sha256": receipt_sha256,
                "recorded_at": recorded_at,
                "replayed": False,
            }
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def fallback_unavailable_profile_binding(
        self, change: dict[str, Any], *, now: datetime | None = None,
    ) -> dict[str, Any]:
        """Roll back an unavailable incumbent to the latest safe predecessor."""
        from .lifecycle import (
            guarded_change_violation,
            profile_template_violation,
            validate_catalog_fallback,
        )

        request = validate_catalog_fallback(change)
        request_json = canonical_json(request)
        request_sha256 = hashlib.sha256(request_json.encode()).hexdigest()
        recorded_at = _authoritative_now(now).isoformat()
        catalog_change = request["catalog_change"]
        catalog_change_sha256 = hashlib.sha256(
            canonical_json(catalog_change).encode(),
        ).hexdigest()
        unavailable = set(catalog_change["unavailable_profile_ids"])
        removed_models = set(catalog_change["removed_model_ids"])
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                "SELECT request_sha256,receipt_json,receipt_sha256,recorded_at "
                "FROM binding_decisions WHERE decision_id=?",
                (request["decision_id"],),
            ).fetchone()
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise ConflictError(
                        "decision id was already used with a different request",
                    )
                self.connection.execute("COMMIT")
                return {
                    "receipt": json.loads(existing["receipt_json"]),
                    "receipt_sha256": existing["receipt_sha256"],
                    "recorded_at": existing["recorded_at"],
                    "replayed": True,
                }
            current = self.connection.execute(
                "SELECT b.template_id,b.profile_id,b.qualification_id,b.version,"
                "p.profile_json,p.profile_sha256,t.payload_json AS template_json,"
                "t.payload_sha256 AS template_sha256 FROM profile_bindings b "
                "JOIN concrete_profiles p ON p.profile_id=b.profile_id "
                "JOIN profile_templates t ON t.template_id=b.template_id "
                "WHERE b.alias=?",
                (request["alias"],),
            ).fetchone()
            if current is None:
                raise ContractError("profile binding does not exist")
            if current["version"] != request["expected_binding_version"]:
                raise ConflictError("profile binding version changed")
            current_profile = json.loads(current["profile_json"])
            current_template = json.loads(current["template_json"])
            if (current_profile["id"] not in unavailable
                    or current_profile["harness"] != catalog_change["harness"]
                    or current_profile["model_id"] not in removed_models):
                raise ContractError(
                    "catalog evidence does not prove the incumbent unavailable",
                )
            if (request["actor"] == "guarded_auto"
                    and current_template["update_mode"] != "guarded_auto"):
                raise ContractError(
                    "guarded automatic promotion is not enabled",
                )

            target = None
            target_profile = None
            qualification_payload = None
            for candidate in self.connection.execute(
                    "SELECT v.version,v.template_id,v.profile_id,v.qualification_id,"
                    "p.profile_json,p.profile_sha256,t.payload_json AS template_json,"
                    "t.payload_sha256 AS template_sha256,q.verdict AS qualification_verdict,"
                    "q.payload_json AS qualification_json,"
                    "q.gate_failures_json AS qualification_failures "
                    "FROM profile_binding_versions v "
                    "JOIN concrete_profiles p ON p.profile_id=v.profile_id "
                    "JOIN profile_templates t ON t.template_id=v.template_id "
                    "LEFT JOIN qualification_runs q "
                    "ON q.qualification_id=v.qualification_id "
                    "WHERE v.alias=? AND v.version<? ORDER BY v.version DESC",
                    (request["alias"], current["version"]),
            ).fetchall():
                profile = json.loads(candidate["profile_json"])
                if candidate["template_id"] != current["template_id"]:
                    continue
                if (profile["id"] in unavailable
                        or (profile["harness"] == catalog_change["harness"]
                            and profile["model_id"] in removed_models)):
                    continue
                if profile["quality_status"] != "proven":
                    continue
                if profile_template_violation(profile, current_template) is not None:
                    continue
                if candidate["qualification_id"] is not None:
                    if (candidate["qualification_verdict"] != "qualified"
                            or json.loads(candidate["qualification_failures"])):
                        continue
                    try:
                        candidate_qualification = self._require_current_qualification(
                            candidate["qualification_id"], now=_authoritative_now(now),
                        )
                    except ContractError:
                        continue
                else:
                    candidate_qualification = None
                if (request["actor"] == "guarded_auto"
                        and guarded_change_violation(
                            current_profile, profile,
                        ) is not None):
                    continue
                target = candidate
                target_profile = profile
                qualification_payload = candidate_qualification
                break
            if target is None or target_profile is None:
                raise ContractError(
                    "no available qualified predecessor for catalog fallback",
                )

            new_version = current["version"] + 1
            receipt = {
                "schema_version": 1,
                "decision_id": request["decision_id"],
                "action": "rollback",
                "alias": request["alias"],
                "actor": request["actor"],
                "reason": request["reason"],
                "evidence_refs": request["evidence_refs"],
                "from": {
                    "binding_version": current["version"],
                    "profile_id": current["profile_id"],
                    "profile_sha256": current["profile_sha256"],
                    "template_id": current["template_id"],
                    "template_sha256": current["template_sha256"],
                },
                "to": {
                    "binding_version": new_version,
                    "profile_id": target_profile["id"],
                    "profile_sha256": target["profile_sha256"],
                    "template_id": target["template_id"],
                    "template_sha256": target["template_sha256"],
                },
                "qualification_id": target["qualification_id"],
                "qualification": qualification_payload,
                "policy": current_template["policy"],
                "rollback_target": {
                    "profile_id": current["profile_id"],
                    "binding_version": current["version"],
                },
                "requested_rollback_target": None,
                "rollback_evaluation": {
                    "kind": "catalog_unavailable",
                    "catalog_change_sha256": catalog_change_sha256,
                    "catalog_change": catalog_change,
                },
                "effective_at": recorded_at,
                "affects_new_runs_only": True,
            }
            receipt_json = canonical_json(receipt)
            receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
            self.connection.execute(
                "UPDATE profile_bindings SET template_id=?,profile_id=?,"
                "qualification_id=?,version=?,updated_at=? WHERE alias=? AND version=?",
                (
                    target["template_id"], target_profile["id"],
                    target["qualification_id"], new_version, recorded_at,
                    request["alias"], current["version"],
                ),
            )
            if self.connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ConflictError("profile binding version changed")
            self.connection.execute(
                "INSERT INTO profile_binding_versions(alias,version,template_id,"
                "profile_id,qualification_id,decision_id,recorded_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    request["alias"], new_version, target["template_id"],
                    target_profile["id"], target["qualification_id"],
                    request["decision_id"], recorded_at,
                ),
            )
            self.connection.execute(
                "INSERT INTO binding_decisions(decision_id,alias,action,actor,"
                "from_version,to_version,from_profile_id,to_profile_id,"
                "qualification_id,request_sha256,receipt_json,receipt_sha256,"
                "recorded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    request["decision_id"], request["alias"], "rollback",
                    request["actor"], current["version"], new_version,
                    current["profile_id"], target_profile["id"],
                    target["qualification_id"], request_sha256, receipt_json,
                    receipt_sha256, recorded_at,
                ),
            )
            self.connection.execute("COMMIT")
            return {
                "receipt": receipt,
                "receipt_sha256": receipt_sha256,
                "recorded_at": recorded_at,
                "replayed": False,
            }
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def profile_binding_decisions(self, alias: str) -> list[dict[str, Any]]:
        if not isinstance(alias, str) or not alias:
            raise ContractError("profile binding alias is invalid")
        return [
            {
                "receipt": json.loads(row["receipt_json"]),
                "receipt_sha256": row["receipt_sha256"],
                "recorded_at": row["recorded_at"],
            }
            for row in self.connection.execute(
                "SELECT receipt_json,receipt_sha256,recorded_at "
                "FROM binding_decisions WHERE alias=? ORDER BY to_version",
                (alias,),
            )
        ]

    def effective_profile_registry(
        self,
        profiles_payload: bytes | str,
        policy_payload: bytes | str,
    ) -> dict[str, Any]:
        """Overlay policy-matched local bindings without editing project files."""
        from .router import _strict_json
        from .validation import validate_policy, validate_profile_registry

        registry, source_sha256 = _strict_json(
            profiles_payload, "profiles file",
        )
        policy, policy_sha256 = _strict_json(policy_payload, "policy file")
        validate_profile_registry(registry)
        validate_policy(policy)
        aliases = sorted({
            reference["id"]
            for candidates in policy["roles"].values()
            for reference in candidates
            if reference["kind"] == "alias"
        })
        if not aliases:
            return {
                "profiles_payload": profiles_payload,
                "source_sha256": source_sha256,
                "policy_sha256": policy_sha256,
                "lifecycle_bindings": [],
            }
        placeholders = ",".join("?" for _ in aliases)
        rows = self.connection.execute(
            "SELECT b.alias,b.profile_id,b.version,b.qualification_id,b.updated_at,"
            "p.profile_json,p.profile_sha256,t.template_id,t.payload_sha256,"
            "t.policy_id,t.policy_version FROM profile_bindings b "
            "JOIN concrete_profiles p ON p.profile_id=b.profile_id "
            "JOIN profile_templates t ON t.template_id=b.template_id "
            f"WHERE b.alias IN ({placeholders}) ORDER BY b.alias",
            tuple(aliases),
        ).fetchall()
        applicable = [
            row for row in rows
            if (row["policy_id"] == policy["id"]
                and row["policy_version"] == policy["version"])
        ]
        if not applicable:
            return {
                "profiles_payload": profiles_payload,
                "source_sha256": source_sha256,
                "policy_sha256": policy_sha256,
                "lifecycle_bindings": [],
            }
        effective = json.loads(canonical_json(registry))
        profiles = {profile["id"]: profile for profile in effective["profiles"]}
        lifecycle_bindings = []
        for row in applicable:
            profile = json.loads(row["profile_json"])
            existing = profiles.get(profile["id"])
            if existing is not None and canonical_json(existing) != canonical_json(profile):
                raise ConflictError(
                    "runtime profile conflicts with the project profile registry",
                )
            if existing is None:
                effective["profiles"].append(profile)
                profiles[profile["id"]] = profile
            effective["bindings"][row["alias"]] = {
                "profile_id": row["profile_id"],
                "version": row["version"],
            }
            lifecycle_bindings.append({
                "alias": row["alias"],
                "profile_id": row["profile_id"],
                "profile_sha256": row["profile_sha256"],
                "version": row["version"],
                "template_id": row["template_id"],
                "template_sha256": row["payload_sha256"],
                "qualification_id": row["qualification_id"],
                "updated_at": row["updated_at"],
            })
        effective["profiles"].sort(key=lambda profile: profile["id"])
        return {
            "profiles_payload": (canonical_json(effective) + "\n").encode(),
            "source_sha256": source_sha256,
            "policy_sha256": policy_sha256,
            "lifecycle_bindings": lifecycle_bindings,
        }

    def worker_invocations(self, run_id: str) -> int:
        """Count attempts whose durable runner actually crossed the launch fence."""
        return self.connection.execute(
            "SELECT COUNT(*) FROM attempts WHERE run_id=? AND pid IS NOT NULL",
            (run_id,),
        ).fetchone()[0]

    def _enforce_experiment_budget(self, run_id: str) -> None:
        """Shared experiment fence; caller holds the reservation write lock."""
        row = self.connection.execute(
            "SELECT s.spec_json,s.recorded_at FROM experiment_assignments a "
            "JOIN experiment_specs s ON s.experiment_id=a.experiment_id WHERE a.run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return
        spec = json.loads(row["spec_json"])
        # Count every durable reservation, including failed/fallback/revision
        # slots and concurrent not-yet-launched work. Never trust caller counters.
        consumed = self.connection.execute(
            "SELECT COUNT(*) FROM attempts t JOIN experiment_assignments a ON a.run_id=t.run_id "
            "WHERE a.experiment_id=?", (spec["experiment_id"],),
        ).fetchone()[0]
        if consumed >= spec["budget"]["max_worker_invocations"]:
            raise BudgetExhausted("experiment worker reservation budget is exhausted")
        if self._experiment_remaining_wall_seconds(run_id, _authoritative_now()) == 0:
            raise BudgetExhausted("experiment wall-time budget is exhausted")

    def reserve_attempt(
        self,
        run_id: str,
        expected_version: int,
        owner_id: str,
        package_digest: str,
        role: str = "worker",
        *,
        account_pool_id: str | None = None,
        profile_id: str | None = None,
        profile_index: int | None = None,
    ) -> AttemptReservation:
        if not owner_id or not package_digest:
            raise ContractError("supervisor owner and package digest are required")
        if role not in {"worker", "implementer", "reviewer", "lead", "researcher", "proposer_a", "proposer_b", "critic"}:
            raise ContractError("attempt role is invalid")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute(
                "SELECT project_id,worktree_path,state,phase,version,created_at,updated_at,"
                "mutable_snapshot,submitted_request FROM runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if not run or run["version"] != expected_version or run["state"] != "queued" or run["phase"] is not None:
                raise ConflictError("run is not available for supervisor claim")
            active = self.connection.execute("SELECT 1 FROM supervisor_claims WHERE run_id=? AND active=1", (run_id,)).fetchone()
            if active:
                raise ConflictError("run already has a supervisor claim")
            if not run["worktree_path"]:
                raise ContractError("run has no canonical worktree identity")
            from .experiment_evidence import verify_prelaunch_snapshot

            try:
                current_snapshot = json.loads(run["mutable_snapshot"] or "{}")
            except (TypeError, ValueError) as exc:
                raise ContractError("frozen run snapshot is invalid") from exc
            verify_prelaunch_snapshot(
                self.connection, run_id=run_id, snapshot=current_snapshot,
                package_digest=package_digest,
            )
            self._enforce_attempt_budget(run_id, run)
            self._enforce_experiment_budget(run_id)
            if account_pool_id is not None:
                if not isinstance(account_pool_id, str) or not account_pool_id:
                    raise ContractError("attempt account pool is invalid")
                try:
                    snapshot = json.loads(run["mutable_snapshot"])
                    routed_role = snapshot["routing"]["roles"][role]
                    candidates = [
                        routed_role["selected"], *routed_role.get("fallbacks", []),
                    ]
                    if profile_id is None and profile_index is None:
                        selected = candidates[0]
                    elif (type(profile_index) is int
                            and 0 <= profile_index < len(candidates)
                            and profile_id == candidates[profile_index]["profile_id"]):
                        selected = candidates[profile_index]
                    else:
                        raise ConflictError(
                            "attempt profile does not match frozen routing order"
                        )
                    capacity = snapshot["routing"]["capacity"][account_pool_id]
                    profile_capacity = capacity.get("profiles", {}).get(
                        selected.get("profile_id"), capacity,
                    )
                    expected_pool = selected["profile"]["account_pool_id"]
                    max_concurrency = capacity["max_concurrency"]
                    capacity_status = profile_capacity.get("status", "available")
                    unknown_policy = capacity.get(
                        "unknown_capacity_policy", "allow_bounded",
                    )
                except ConflictError:
                    raise
                except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
                    raise ConflictError(
                        "frozen account-pool reservation is invalid"
                    ) from exc
                if (expected_pool != account_pool_id
                        or type(max_concurrency) is not int
                        or max_concurrency < 1
                        or capacity_status not in {
                            "available", "exhausted", "unknown",
                        }
                        or unknown_policy not in {"allow_bounded", "block"}):
                    raise ConflictError(
                        "attempt account pool does not match frozen routing"
                    )
                if capacity_status == "exhausted":
                    raise ConflictError("account pool capacity is exhausted")
                if (capacity_status == "unknown"
                        and unknown_policy == "block"):
                    raise ConflictError("unknown account pool capacity is blocked")
                target = self._capacity_target(selected["profile"])
            elif profile_id is not None or profile_index is not None:
                raise ContractError("attempt profile requires an account pool")
            old = self.connection.execute("SELECT COALESCE(MAX(fencing_token),0) FROM supervisor_claims WHERE run_id=?", (run_id,)).fetchone()[0]
            supervisor_token, attempt_id, attempt_token = old + 1, str(uuid.uuid4()), uuid.uuid4().hex
            now, version = _utc_now(), expected_version + 1
            self.connection.execute("INSERT OR REPLACE INTO supervisor_claims(run_id,owner_id,fencing_token,package_digest,heartbeat_at,active) VALUES(?,?,?,?,?,1)", (run_id, owner_id, supervisor_token, package_digest, now))
            self.connection.execute(
                "INSERT INTO attempts(id,run_id,project_id,worktree_path,attempt_token,"
                "status,heartbeat_at,package_digest,created_at,role,account_pool_id,"
                "profile_id,profile_index) VALUES(?,?,?,?,?,'reserved',?,?,?,?,?,?,?)",
                (attempt_id, run_id, run["project_id"], run["worktree_path"],
                 attempt_token, now, package_digest, now, role, account_pool_id,
                 profile_id, profile_index),
            )
            if account_pool_id is not None:
                self._reserve_pool_capacity_locked(
                    run_id=run_id,
                    pool_id=account_pool_id,
                    purpose="attempt",
                    profile_id=profile_id,
                    target=target,
                    max_concurrency=max_concurrency,
                    unknown_capacity_policy=unknown_policy,
                    frozen_status=capacity_status,
                    attempt_id=attempt_id,
                    now=datetime.fromisoformat(now),
                )
            self.connection.execute("UPDATE runs SET phase='launching',version=?,updated_at=? WHERE id=?", (version, now, run_id))
            event = canonical_json({"attempt_id": attempt_id, "supervisor_token": supervisor_token})
            self.connection.execute("INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,'supervisor.claimed',?,?)", (run_id, version, event, now))
            self.connection.execute("COMMIT")
            return AttemptReservation(run_id, attempt_id, attempt_token, supervisor_token, version)
        except sqlite3.IntegrityError as exc:
            self.connection.execute("ROLLBACK")
            raise ConflictError("worktree already has an active writer") from exc
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def mark_attempt_running(self, reservation: AttemptReservation, pid: int, pgid: int, process_start_id: str, durable_paths: dict[str, str] | None = None) -> int:
        if any(type(value) is not int or value <= 0 for value in (pid, pgid)) or not process_start_id:
            raise ContractError("valid process identity is required")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT r.version,r.phase,a.status,a.attempt_token,s.fencing_token,s.active FROM runs r JOIN attempts a ON a.run_id=r.id JOIN supervisor_claims s ON s.run_id=r.id WHERE r.id=? AND a.id=?", (reservation.run_id, reservation.attempt_id)).fetchone()
            if not row or row["version"] != reservation.version or row["phase"] != "launching" or row["status"] != "reserved" or row["attempt_token"] != reservation.attempt_token or row["fencing_token"] != reservation.supervisor_token or not row["active"]:
                raise ConflictError("attempt reservation is stale")
            version, now = row["version"] + 1, _utc_now()
            durable_paths = durable_paths or {}
            self.connection.execute("UPDATE attempts SET status='running',pid=?,pgid=?,process_start_id=?,heartbeat_at=?,stdout_spool=?,stderr_spool=?,stdout_meta=?,stderr_meta=?,exit_record=?,child_record=? WHERE id=?", (pid, pgid, process_start_id, now, durable_paths.get("stdout_spool"), durable_paths.get("stderr_spool"), durable_paths.get("stdout_meta"), durable_paths.get("stderr_meta"), durable_paths.get("exit_record"), durable_paths.get("child_record"), reservation.attempt_id))
            self.connection.execute("UPDATE runs SET state='running',phase=NULL,version=?,updated_at=? WHERE id=?", (version, now, reservation.run_id))
            event = canonical_json({"attempt_id": reservation.attempt_id, "pid": pid, "pgid": pgid, "process_start_id": process_start_id})
            self.connection.execute("INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,'run.running',?,?)", (reservation.run_id, version, event, now))
            self.connection.execute("COMMIT")
            return version
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def heartbeat_attempt(self, run_id: str, attempt_token: str, supervisor_token: int) -> None:
        now = _utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            attempt = self.connection.execute("UPDATE attempts SET heartbeat_at=? WHERE run_id=? AND attempt_token=? AND status IN ('running','cancelling')", (now, run_id, attempt_token)).rowcount
            claim = self.connection.execute("UPDATE supervisor_claims SET heartbeat_at=? WHERE run_id=? AND fencing_token=? AND active=1", (now, run_id, supervisor_token)).rowcount
            if attempt != 1 or claim != 1:
                raise ConflictError("attempt heartbeat is fenced")
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def request_cancel(self, run_id: str) -> tuple[int, dict[str, Any] | None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute("SELECT state,version FROM runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                raise ContractError("run does not exist")
            if run["state"] in TERMINAL_STATES:
                self.connection.execute("COMMIT")
                return run["version"], None
            if run["state"] == "cancelling":
                attempt = self.connection.execute("SELECT * FROM attempts WHERE run_id=? AND status='cancelling' ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
                self.connection.execute("COMMIT")
                return run["version"], dict(attempt) if attempt else None
            if run["state"] != "running":
                raise ConflictError("run is not cancellable by the supervisor")
            attempt = self.connection.execute("SELECT * FROM attempts WHERE run_id=? AND status='running'", (run_id,)).fetchone()
            if not attempt:
                raise ConflictError("running run has no active attempt")
            version, now = run["version"] + 1, _utc_now()
            self.connection.execute("UPDATE runs SET state='cancelling',version=?,updated_at=? WHERE id=?", (version, now, run_id))
            self.connection.execute("UPDATE attempts SET status='cancelling',heartbeat_at=? WHERE id=?", (now, attempt["id"]))
            self.connection.execute("INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,'run.cancelling','{}',?)", (run_id, version, now))
            self.connection.execute("COMMIT")
            return version, dict(attempt)
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    @_project_terminal
    def finish_attempt(self, run_id: str, attempt_token: str, terminal_state: str, payload: Any) -> int:
        if terminal_state not in TERMINAL_STATES:
            raise ContractError("invalid terminal state")
        encoded = canonical_json(payload)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute("SELECT state,version FROM runs WHERE id=?", (run_id,)).fetchone()
            attempt = self.connection.execute("SELECT id,status FROM attempts WHERE run_id=? AND attempt_token=?", (run_id, attempt_token)).fetchone()
            if not run or not attempt or attempt["status"] not in {"running", "cancelling"}:
                raise ConflictError("attempt completion is fenced")
            if run["state"] in TERMINAL_STATES:
                self.connection.execute("COMMIT")
                return run["version"]
            version, now = run["version"] + 1, _utc_now()
            self.connection.execute("UPDATE attempts SET status='finished',finished_at=? WHERE id=?", (now, attempt["id"]))
            self.connection.execute("UPDATE supervisor_claims SET active=0 WHERE run_id=?", (run_id,))
            terminal_state = "cancelled" if run["state"] == "cancelling" else terminal_state
            self.connection.execute("UPDATE runs SET state=?,phase=NULL,version=?,updated_at=? WHERE id=?", (terminal_state, version, now, run_id))
            self.connection.execute("INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,?,?,?)", (run_id, version, f"run.{terminal_state}", encoded, now))
            self.connection.execute("COMMIT")
            return version
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def record_attempt_output(self, run_id: str, attempt_token: str, stdout_id: str, stderr_id: str, metadata: Any) -> int:
        encoded = canonical_json(metadata)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute("SELECT state,version FROM runs WHERE id=?", (run_id,)).fetchone()
            attempt = self.connection.execute("SELECT id,status FROM attempts WHERE run_id=? AND attempt_token=?", (run_id, attempt_token)).fetchone()
            if not run or not attempt or attempt["status"] not in {"running", "cancelling"} or run["state"] in TERMINAL_STATES:
                raise ConflictError("attempt output is fenced")
            version, now = run["version"] + 1, _utc_now()
            self.connection.execute("UPDATE attempts SET stdout_artifact_id=?,stderr_artifact_id=?,output_metadata=? WHERE id=?", (stdout_id, stderr_id, encoded, attempt["id"]))
            self.connection.execute("UPDATE runs SET version=?,updated_at=? WHERE id=?", (version, now, run_id))
            self.connection.execute("INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,'attempt.output',?,?)", (run_id, version, encoded, now))
            self.connection.execute("COMMIT")
            return version
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def _prepare_durable_artifacts(
        self,
        run_id: str,
        artifacts: list[dict[str, Any]],
        *,
        require_result_receipt: bool,
    ) -> tuple[list[tuple[str, str, str, int]], str, str]:
        expected_parent = (self.artifacts / run_id).resolve()
        prepared = []
        names = set()
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise ContractError("durable artifacts must be objects")
            name = artifact.get("name")
            path = Path(artifact.get("path", ""))
            digest = artifact.get("sha256")
            size = artifact.get("byte_size")
            if not name or Path(name).name != name or name in names:
                raise ContractError("durable artifact names must be unique path components")
            names.add(name)
            if path.resolve().parent != expected_parent:
                raise ContractError("durable artifact path is outside the run-owned store")
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != digest or len(content) != size:
                raise ConflictError("durable artifact changed before database import")
            prepared.append((name, str(path), digest, size))
        stdout_names = [name for name in names if name.endswith(".stdout")]
        stderr_names = [name for name in names if name.endswith(".stderr")]
        if (len(stdout_names) != 1 or len(stderr_names) != 1
                or (require_result_receipt and "result-receipt.json" not in names)):
            requirement = "stdout, stderr, and result receipt" if require_result_receipt else "stdout and stderr"
            raise ContractError(f"durable import requires exactly one {requirement}")
        return prepared, stdout_names[0], stderr_names[0]

    def _prepare_exact_artifacts(
        self,
        run_id: str,
        artifacts: list[dict[str, Any]],
        expected_names: frozenset[str],
    ) -> list[tuple[str, str, str, int]]:
        """Verify a complete named artifact set before entering a write transaction."""
        if not isinstance(artifacts, list):
            raise ContractError("artifacts must be an array")
        expected_parent = (self.artifacts / run_id).resolve()
        prepared = []
        names = set()
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise ContractError("artifacts must be objects")
            name = artifact.get("name")
            path = Path(artifact.get("path", ""))
            digest = artifact.get("sha256")
            size = artifact.get("byte_size")
            if (not isinstance(name, str) or not name or Path(name).name != name
                    or name in names):
                raise ContractError("artifact names must be unique path components")
            names.add(name)
            if path.resolve().parent != expected_parent:
                raise ContractError("artifact path is outside the run-owned store")
            content = path.read_bytes()
            if (not isinstance(digest, str)
                    or type(size) is not int
                    or size < 0
                    or hashlib.sha256(content).hexdigest() != digest
                    or len(content) != size):
                raise ConflictError("artifact changed before database import")
            prepared.append((name, str(path), digest, size))
        if names != set(expected_names):
            raise ContractError("terminal artifact set is incomplete or unexpected")
        return prepared

    def _reference_prepared_artifacts(
        self,
        run_id: str,
        version: int,
        prepared: list[tuple[str, str, str, int]],
    ) -> tuple[int, dict[str, str]]:
        artifact_ids = {}
        for name, path, digest, size in prepared:
            existing = self.connection.execute(
                "SELECT id,path,sha256,byte_size FROM artifacts WHERE run_id=? AND name=?",
                (run_id, name),
            ).fetchone()
            if existing:
                if (existing["path"] != path or existing["sha256"] != digest
                        or existing["byte_size"] != size):
                    raise ConflictError("durable artifact conflicts with an existing reference")
                artifact_ids[name] = existing["id"]
                continue
            artifact_id, now = str(uuid.uuid4()), _utc_now()
            self.connection.execute(
                "INSERT INTO artifacts(id,run_id,name,path,sha256,byte_size,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (artifact_id, run_id, name, path, digest, size, now),
            )
            artifact_ids[name] = artifact_id
            version += 1
            event = canonical_json({
                "artifact_id": artifact_id,
                "name": name,
                "sha256": digest,
                "byte_size": size,
            })
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'artifact.recorded',?,?)",
                (run_id, version, event, now),
            )
            self.connection.execute(
                "UPDATE runs SET version=?,updated_at=? WHERE id=?",
                (version, now, run_id),
            )
        return version, artifact_ids

    def _record_prepared_output(
        self,
        run_id: str,
        version: int,
        attempt: sqlite3.Row,
        artifact_ids: dict[str, str],
        stdout_name: str,
        stderr_name: str,
        encoded_metadata: str,
    ) -> int:
        stdout_id, stderr_id = artifact_ids[stdout_name], artifact_ids[stderr_name]
        if attempt["output_metadata"] is None:
            now = _utc_now()
            version += 1
            self.connection.execute(
                "UPDATE attempts SET stdout_artifact_id=?,stderr_artifact_id=?,"
                "output_metadata=? WHERE id=?",
                (stdout_id, stderr_id, encoded_metadata, attempt["id"]),
            )
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'attempt.output',?,?)",
                (run_id, version, encoded_metadata, now),
            )
            self.connection.execute(
                "UPDATE runs SET version=?,updated_at=? WHERE id=?",
                (version, now, run_id),
            )
        elif (attempt["stdout_artifact_id"] != stdout_id
                or attempt["stderr_artifact_id"] != stderr_id
                or attempt["output_metadata"] != encoded_metadata):
            raise ConflictError("durable output conflicts with its prior import")
        return version

    @_project_terminal
    def commit_durable_import(self, run_id: str, attempt_token: str, artifacts: list[dict[str, Any]], metadata: Any, terminal_state: str, payload: Any) -> str:
        """Atomically import one durable receipt, or observe its prior import.

        Content-addressed files are finalized before this call. All database
        references, output projection fields, and terminal state then cross a
        single write fence so competing recovery processes cannot partially
        import or downgrade a valid completion.
        """
        if terminal_state not in TERMINAL_STATES:
            raise ContractError("invalid terminal state")
        prepared, stdout_name, stderr_name = self._prepare_durable_artifacts(
            run_id, artifacts, require_result_receipt=True,
        )
        encoded_metadata, encoded_payload = canonical_json(metadata), canonical_json(payload)

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute("SELECT state,version FROM runs WHERE id=?", (run_id,)).fetchone()
            attempt = self.connection.execute(
                "SELECT id,status,stdout_artifact_id,stderr_artifact_id,output_metadata FROM attempts WHERE run_id=? AND attempt_token=?",
                (run_id, attempt_token),
            ).fetchone()
            if not run or not attempt:
                raise ConflictError("durable import is fenced")
            if attempt["status"] == "finished" and run["state"] in TERMINAL_STATES:
                self.connection.execute("COMMIT")
                return run["state"]
            if attempt["status"] not in {"running", "cancelling"} or run["state"] not in {"running", "cancelling"}:
                raise ConflictError("durable import is fenced")

            version, artifact_ids = self._reference_prepared_artifacts(
                run_id, run["version"], prepared,
            )
            version = self._record_prepared_output(
                run_id,
                version,
                attempt,
                artifact_ids,
                stdout_name,
                stderr_name,
                encoded_metadata,
            )

            now = _utc_now(); version += 1
            effective_state = "cancelled" if run["state"] == "cancelling" else terminal_state
            self.connection.execute("UPDATE attempts SET status='finished',finished_at=? WHERE id=?", (now, attempt["id"]))
            self.connection.execute("UPDATE supervisor_claims SET active=0 WHERE run_id=?", (run_id,))
            self.connection.execute(
                "UPDATE runs SET state=?,phase=NULL,version=?,updated_at=? WHERE id=?",
                (effective_state, version, now, run_id),
            )
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,?,?,?)",
                (run_id, version, f"run.{effective_state}", encoded_payload, now),
            )
            self.connection.execute("COMMIT")
            return effective_state
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def commit_durable_fallback(
        self,
        run_id: str,
        attempt_token: str,
        artifacts: list[dict[str, Any]],
        metadata: Any,
        error: dict[str, Any],
    ) -> str:
        """Record one failed profile attempt and queue its frozen fallback."""
        prepared, stdout_name, stderr_name = self._prepare_durable_artifacts(
            run_id, artifacts, require_result_receipt=False,
        )
        enriched_metadata = dict(metadata)
        enriched_metadata["failure"] = json.loads(canonical_json(error))
        encoded_metadata = canonical_json(enriched_metadata)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute(
                "SELECT state,phase,version FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            attempt = self.connection.execute(
                "SELECT id,status,stdout_artifact_id,stderr_artifact_id,output_metadata "
                "FROM attempts WHERE run_id=? AND attempt_token=?",
                (run_id, attempt_token),
            ).fetchone()
            if not run or not attempt:
                raise ConflictError("durable fallback import is fenced")
            if (attempt["status"] == "finished" and run["state"] == "queued"
                    and run["phase"] is None):
                self.connection.execute("COMMIT")
                return "queued"
            if (attempt["status"] != "running" or run["state"] != "running"
                    or run["phase"] is not None):
                raise ConflictError("durable fallback import is fenced")
            version, artifact_ids = self._reference_prepared_artifacts(
                run_id, run["version"], prepared,
            )
            version = self._record_prepared_output(
                run_id,
                version,
                attempt,
                artifact_ids,
                stdout_name,
                stderr_name,
                encoded_metadata,
            )
            now, version = _utc_now(), version + 1
            self.connection.execute(
                "UPDATE attempts SET status='finished',finished_at=? WHERE id=?",
                (now, attempt["id"]),
            )
            self.connection.execute(
                "UPDATE supervisor_claims SET active=0 WHERE run_id=?", (run_id,),
            )
            self.connection.execute(
                "UPDATE runs SET state='queued',phase=NULL,version=?,updated_at=? "
                "WHERE id=?",
                (version, now, run_id),
            )
            payload = dict(error)
            payload["attempt_id"] = attempt["id"]
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'run.fallback_queued',?,?)",
                (run_id, version, canonical_json(payload), now),
            )
            self.connection.execute("COMMIT")
            return "queued"
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def commit_delivery_candidate(
        self,
        run_id: str,
        attempt_token: str,
        artifacts: list[dict[str, Any]],
        metadata: Any,
        mutable_snapshot: dict[str, Any],
        review_worktree_path: str,
        candidate: dict[str, Any],
    ) -> str:
        """Atomically import one implementation and queue its frozen candidate."""
        prepared, stdout_name, stderr_name = self._prepare_durable_artifacts(
            run_id, artifacts, require_result_receipt=False,
        )
        encoded_metadata = canonical_json(metadata)
        encoded_snapshot = canonical_json(mutable_snapshot)
        if (not isinstance(candidate, dict)
                or mutable_snapshot.get("candidate") != candidate):
            raise ContractError("delivery candidate differs from its saved snapshot")
        expected_lengths = {
            "candidate_sha256": 64,
            "commit_oid": 40,
            "patch_sha256": 64,
        }
        for field, expected_length in expected_lengths.items():
            value = candidate.get(field)
            if (not isinstance(value, str) or len(value) != expected_length
                    or any(character not in "0123456789abcdef" for character in value)):
                raise ContractError(f"delivery candidate {field} is invalid")
        try:
            resolved_worktree = str(Path(review_worktree_path).resolve(strict=True))
            worktree_common = str(git_common_dir(Path(resolved_worktree)))
        except OSError as exc:
            raise ContractError("delivery review worktree is unavailable") from exc

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute(
                "SELECT r.state,r.phase,r.version,r.mutable_snapshot,p.git_common_dir "
                "FROM runs r JOIN projects p ON p.id=r.project_id WHERE r.id=?",
                (run_id,),
            ).fetchone()
            attempt = self.connection.execute(
                "SELECT id,status,role,stdout_artifact_id,stderr_artifact_id,"
                "output_metadata FROM attempts WHERE run_id=? AND attempt_token=?",
                (run_id, attempt_token),
            ).fetchone()
            if not run or not attempt:
                raise ConflictError("delivery candidate import is fenced")
            if (attempt["status"] == "finished" and run["state"] == "queued"
                    and run["phase"] is None
                    and run["mutable_snapshot"] == encoded_snapshot):
                self.connection.execute("COMMIT")
                return "candidate_ready"
            if (attempt["status"] != "running" or attempt["role"] != "implementer"
                    or run["state"] != "running" or run["phase"] is not None):
                raise ConflictError("delivery candidate import is fenced")
            if worktree_common != run["git_common_dir"]:
                raise ContractError("delivery review worktree belongs to another project")
            version, artifact_ids = self._reference_prepared_artifacts(
                run_id, run["version"], prepared,
            )
            version = self._record_prepared_output(
                run_id,
                version,
                attempt,
                artifact_ids,
                stdout_name,
                stderr_name,
                encoded_metadata,
            )
            now, version = _utc_now(), version + 1
            self.connection.execute(
                "UPDATE attempts SET status='finished',finished_at=? WHERE id=?",
                (now, attempt["id"]),
            )
            self.connection.execute(
                "UPDATE supervisor_claims SET active=0 WHERE run_id=?", (run_id,),
            )
            self.connection.execute(
                "UPDATE runs SET mutable_snapshot=?,worktree_path=?,state='queued',"
                "phase=NULL,version=?,updated_at=? WHERE id=?",
                (encoded_snapshot, resolved_worktree, version, now, run_id),
            )
            payload = canonical_json({
                "attempt_id": attempt["id"],
                "candidate_sha256": candidate["candidate_sha256"],
                "commit_oid": candidate["commit_oid"],
                "patch_sha256": candidate["patch_sha256"],
            })
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'delivery.candidate_ready',?,?)",
                (run_id, version, payload, now),
            )
            self.connection.execute("COMMIT")
            return "candidate_ready"
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def commit_durable_handoff(
        self,
        run_id: str,
        attempt_token: str,
        artifacts: list[dict[str, Any]],
        metadata: Any,
        packet: dict[str, Any],
        *, mutable_snapshot: dict[str, Any] | None = None,
    ) -> str:
        """Atomically import one completed attempt and publish its host packet."""
        prepared, stdout_name, stderr_name = self._prepare_durable_artifacts(
            run_id, artifacts, require_result_receipt=False,
        )
        encoded_metadata = canonical_json(metadata)
        frozen_packet = json.loads(canonical_json(packet))
        packet_artifacts = frozen_packet.get("artifacts")
        if not isinstance(packet_artifacts, list) or not packet_artifacts:
            raise ContractError("handoff packet must reference evidence artifacts")
        prepared_hashes = {name: digest for name, _, digest, _ in prepared}
        referenced_names = set()
        for reference in packet_artifacts:
            if not isinstance(reference, dict) or set(reference) != {"name", "sha256"}:
                raise ContractError("handoff artifact references are invalid")
            name, digest = reference["name"], reference["sha256"]
            if (not isinstance(name, str) or name in referenced_names
                    or prepared_hashes.get(name) != digest):
                raise ContractError("handoff artifact does not match durable evidence")
            referenced_names.add(name)

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute(
                "SELECT state,phase,version FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            attempt = self.connection.execute(
                "SELECT id,status,stdout_artifact_id,stderr_artifact_id,output_metadata "
                "FROM attempts WHERE run_id=? AND attempt_token=?",
                (run_id, attempt_token),
            ).fetchone()
            if not run or not attempt:
                raise ConflictError("durable handoff import is fenced")
            if attempt["status"] == "finished" and run["state"] == "awaiting_host":
                self.connection.execute("COMMIT")
                return "awaiting_host"
            if (attempt["status"] != "running" or run["state"] != "running"
                    or run["phase"] is not None):
                raise ConflictError("durable handoff import is fenced")
            if mutable_snapshot is not None:
                from .council_runtime import verify_origin
                current_snapshot = json.loads(self.run(run_id)["mutable_snapshot"])
                if (current_snapshot.get("task", {}).get("workflow") != "council-decision"
                        or attempt["id"] is None
                        or self.connection.execute("SELECT role FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0] != "critic"):
                    raise ConflictError("Council handoff projection is fenced")
                verify_origin(self, run_id, current_snapshot)
                verify_origin(self, run_id, mutable_snapshot)
                old = current_snapshot["council_state"]
                new = mutable_snapshot["council_state"]
                if (old["next_role"] != "critic" or set(old["documents"]) != {"proposer_a", "proposer_b"}
                        or set(new["documents"]) != {"proposer_a", "proposer_b", "critic"}
                        or new["next_role"] != "lead"
                        or {key: value for key, value in new["documents"].items() if key != "critic"} != old["documents"]
                        or {key: value for key, value in new["artifacts"].items() if key != "critic"} != old["artifacts"]):
                    raise ConflictError("Council critic import changed finalized proposals or skipped the barrier")
                self.connection.execute("UPDATE runs SET mutable_snapshot=? WHERE id=?", (canonical_json(mutable_snapshot), run_id))

            version, artifact_ids = self._reference_prepared_artifacts(
                run_id, run["version"], prepared,
            )
            version = self._record_prepared_output(
                run_id,
                version,
                attempt,
                artifact_ids,
                stdout_name,
                stderr_name,
                encoded_metadata,
            )
            for reference in packet_artifacts:
                reference["artifact_id"] = artifact_ids[reference["name"]]
            packet_json = canonical_json(frozen_packet)
            packet_sha256 = hashlib.sha256(packet_json.encode()).hexdigest()
            if self.connection.execute(
                "SELECT 1 FROM handoffs WHERE run_id=? AND status IN ('open','submitted')",
                (run_id,),
            ).fetchone():
                raise ConflictError("run already has a pending handoff")
            sequence = self.connection.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM handoffs WHERE run_id=?",
                (run_id,),
            ).fetchone()[0]
            handoff_id, now = str(uuid.uuid4()), _utc_now()
            from .reports import build_handoff_reports, handoff_report_names
            handoff_reports = build_handoff_reports(
                run_id=run_id,
                handoff_id=handoff_id,
                sequence=sequence,
                packet=frozen_packet,
                packet_sha256=packet_sha256,
                created_at=now,
            )
            report_artifacts = []
            for name, content in handoff_reports.items():
                path, digest, size = self.finalize_artifact(run_id, name, content)
                report_artifacts.append({
                    "name": name,
                    "path": path,
                    "sha256": digest,
                    "byte_size": size,
                })
            prepared_reports = self._prepare_exact_artifacts(
                run_id,
                report_artifacts,
                frozenset(handoff_report_names(sequence)),
            )
            version, _ = self._reference_prepared_artifacts(
                run_id, version, prepared_reports,
            )
            version += 1
            self.connection.execute(
                "INSERT INTO handoffs(id,run_id,sequence,packet_json,packet_sha256,status,"
                "created_run_version,created_at) VALUES(?,?,?,?,?,'open',?,?)",
                (handoff_id, run_id, sequence, packet_json, packet_sha256, version, now),
            )
            self.connection.execute(
                "UPDATE attempts SET status='finished',finished_at=? WHERE id=?",
                (now, attempt["id"]),
            )
            self.connection.execute(
                "UPDATE supervisor_claims SET active=0 WHERE run_id=?", (run_id,),
            )
            self.connection.execute(
                "UPDATE runs SET state='awaiting_host',phase=NULL,version=?,updated_at=? "
                "WHERE id=?",
                (version, now, run_id),
            )
            payload = canonical_json({
                "handoff_id": handoff_id,
                "packet_sha256": packet_sha256,
                "sequence": sequence,
            })
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'run.awaiting_host',?,?)",
                (run_id, version, payload, now),
            )
            self.connection.execute("COMMIT")
            return "awaiting_host"
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def commit_council_stage(self, run_id: str, attempt_token: str, artifacts: list,
                             metadata: dict, snapshot: dict, role: str) -> str:
        """Import one sealed proposal under the existing attempt fence."""
        from .council_runtime import verify_origin
        prepared, stdout_name, stderr_name = self._prepare_durable_artifacts(run_id, artifacts, require_result_receipt=False)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.run(run_id)
            attempt = self.connection.execute("SELECT * FROM attempts WHERE run_id=? AND attempt_token=?", (run_id, attempt_token)).fetchone()
            if (not attempt or run["state"] != "running" or run["phase"] is not None
                    or attempt["status"] != "running" or attempt["role"] != role
                    or role not in {"proposer_a", "proposer_b"}):
                raise ConflictError("Council proposal import is fenced")
            prior = json.loads(run["mutable_snapshot"])
            verify_origin(self, run_id, prior)
            verify_origin(self, run_id, snapshot)
            if prior["council_state"]["next_role"] != role or role in prior["council_state"]["documents"]:
                raise ConflictError("Council proposal stage is stale")
            old_docs = prior["council_state"]["documents"]
            new_docs = snapshot["council_state"]["documents"]
            if (set(new_docs) != set(old_docs) | {role}
                    or {k: v for k, v in new_docs.items() if k != role} != old_docs
                    or snapshot["council_state"]["next_role"] != ("proposer_b" if role == "proposer_a" else "critic")
                    or {key: value for key, value in snapshot["council_state"]["artifacts"].items() if key != role} != prior["council_state"].get("artifacts", {})):
                raise ConflictError("Council proposal import changed another finalized proposal")
            version, ids = self._reference_prepared_artifacts(run_id, run["version"], prepared)
            version = self._record_prepared_output(run_id, version, attempt, ids, stdout_name, stderr_name, canonical_json(metadata))
            now, version = _utc_now(), version + 1
            self.connection.execute("UPDATE attempts SET status='finished',finished_at=? WHERE id=?", (now, attempt["id"]))
            self.connection.execute("UPDATE supervisor_claims SET active=0 WHERE run_id=?", (run_id,))
            self.connection.execute("UPDATE runs SET mutable_snapshot=?,state='queued',phase=NULL,version=?,updated_at=? WHERE id=?",
                                    (canonical_json(snapshot), version, now, run_id))
            self.connection.execute("INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,'council.proposal_finalized',?,?)",
                                    (run_id, version, canonical_json({"role": role, "attempt_id": attempt["id"]}), now))
            self.connection.execute("COMMIT")
            return "queued"
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def queue_headless_lead(
        self,
        run_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        """Pause an open handoff only long enough to launch its configured lead."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT state,phase,version,mutable_snapshot FROM runs WHERE id=?",
                (run_id,),
            ).fetchone()
            handoff = self.connection.execute(
                "SELECT id,status,sequence FROM handoffs WHERE run_id=? "
                "ORDER BY sequence DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if not row or not handoff:
                raise ConflictError("headless lead handoff is missing")
            if row["version"] != expected_version:
                raise ConflictError("headless lead run version changed")
            try:
                snapshot = json.loads(row["mutable_snapshot"])
                budget = snapshot["task"]["budget"]["max_worker_invocations"]
                lead_mode = snapshot["task"]["lead"]["mode"]
            except (TypeError, KeyError, json.JSONDecodeError) as exc:
                raise ConflictError("frozen headless lead configuration is invalid") from exc
            if lead_mode != "headless" or type(budget) is not int or budget < 1:
                raise ConflictError("run is not configured for a headless lead")
            if (row["state"] != "awaiting_host" or row["phase"] is not None
                    or handoff["status"] != "open"):
                raise ConflictError("headless lead handoff is not queueable")
            active = self.connection.execute(
                "SELECT 1 FROM supervisor_claims WHERE run_id=? AND active=1",
                (run_id,),
            ).fetchone()
            if active:
                raise ConflictError("headless lead already has an active supervisor")
            invocations = self.worker_invocations(run_id)
            if invocations >= budget:
                self.connection.execute("COMMIT")
                return {
                    "action": "budget_exhausted",
                    "version": row["version"],
                    "worker_invocations": invocations,
                }
            now, version = _utc_now(), row["version"] + 1
            self.connection.execute(
                "UPDATE runs SET state='queued',phase=NULL,version=?,updated_at=? "
                "WHERE id=?",
                (version, now, run_id),
            )
            payload = canonical_json({
                "handoff_id": handoff["id"],
                "sequence": handoff["sequence"],
                "worker_invocations": invocations,
            })
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'run.headless_lead_queued',?,?)",
                (run_id, version, payload, now),
            )
            self.connection.execute("COMMIT")
            return {
                "action": "queued",
                "version": version,
                "worker_invocations": invocations,
            }
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def commit_headless_lead(
        self,
        run_id: str,
        attempt_token: str,
        artifacts: list[dict[str, Any]],
        metadata: Any,
    ) -> str:
        """Import one valid headless-lead result and reopen its frozen handoff."""
        prepared, stdout_name, stderr_name = self._prepare_durable_artifacts(
            run_id, artifacts, require_result_receipt=False,
        )
        encoded_metadata = canonical_json(metadata)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute(
                "SELECT state,phase,version FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            attempt = self.connection.execute(
                "SELECT id,status,role,stdout_artifact_id,stderr_artifact_id,"
                "output_metadata FROM attempts WHERE run_id=? AND attempt_token=?",
                (run_id, attempt_token),
            ).fetchone()
            handoff = self.connection.execute(
                "SELECT id,status,sequence FROM handoffs WHERE run_id=? "
                "ORDER BY sequence DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if not run or not attempt or not handoff:
                raise ConflictError("headless lead import is fenced")
            if (attempt["status"] == "finished"
                    and run["state"] == "awaiting_host"
                    and handoff["status"] == "open"):
                self.connection.execute("COMMIT")
                return "awaiting_host"
            if (attempt["status"] != "running" or attempt["role"] != "lead"
                    or run["state"] != "running" or run["phase"] is not None
                    or handoff["status"] != "open"):
                raise ConflictError("headless lead import is fenced")
            evidence_name = f"lead-attempt-{attempt['id']}.json"
            if evidence_name not in {item[0] for item in prepared}:
                raise ContractError("headless lead evidence artifact is missing")
            version, artifact_ids = self._reference_prepared_artifacts(
                run_id, run["version"], prepared,
            )
            version = self._record_prepared_output(
                run_id,
                version,
                attempt,
                artifact_ids,
                stdout_name,
                stderr_name,
                encoded_metadata,
            )
            now, version = _utc_now(), version + 1
            self.connection.execute(
                "UPDATE attempts SET status='finished',finished_at=? WHERE id=?",
                (now, attempt["id"]),
            )
            self.connection.execute(
                "UPDATE supervisor_claims SET active=0 WHERE run_id=?", (run_id,),
            )
            self.connection.execute(
                "UPDATE runs SET state='awaiting_host',phase=NULL,version=?,updated_at=? "
                "WHERE id=?",
                (version, now, run_id),
            )
            payload = canonical_json({
                "attempt_id": attempt["id"],
                "handoff_id": handoff["id"],
                "evidence": evidence_name,
            })
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'run.headless_lead_ready',?,?)",
                (run_id, version, payload, now),
            )
            self.connection.execute("COMMIT")
            return "awaiting_host"
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def block_recovery(self, run_id: str, attempt_token: str, reason: str, *, release_writer: bool = False) -> int:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute("SELECT state,version FROM runs WHERE id=?", (run_id,)).fetchone()
            attempt = self.connection.execute("SELECT id,status FROM attempts WHERE run_id=? AND attempt_token=?", (run_id, attempt_token)).fetchone()
            if not run or not attempt or attempt["status"] not in {"running", "cancelling"}:
                raise ConflictError("recovery disposition is fenced")
            version, now = run["version"] + 1, _utc_now()
            status = "recovery_required" if release_writer else "ownership_ambiguous"
            self.connection.execute("UPDATE attempts SET status=?,finished_at=? WHERE id=?", (status, now, attempt["id"]))
            self.connection.execute("UPDATE supervisor_claims SET active=0 WHERE run_id=?", (run_id,))
            self.connection.execute("UPDATE runs SET state='blocked',phase='recovery_required',version=?,updated_at=? WHERE id=?", (version, now, run_id))
            self.connection.execute("INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,'run.blocked',?,?)", (run_id, version, canonical_json({"reason": reason, "next_action": "RECOVERY_REQUIRED"}), now))
            self.connection.execute("COMMIT")
            return version
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    @_project_terminal
    def recover_unstarted_attempt(
        self, run_id: str, attempt_token: str, reason: str, *, terminal_artifacts: list | None = None,
    ) -> tuple[int, str]:
        """Recover a dead gated runner that never published a child identity.

        The supervisor must first establish that the runner is dead and the
        atomic child record is absent. The inner gate cannot open before that
        record is durable, so no worker command can have executed in this case.
        """
        prepared = (self._prepare_exact_artifacts(run_id, terminal_artifacts, BRANCH_REVIEW_TERMINAL_ARTIFACTS)
                    if terminal_artifacts is not None else None)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute(
                "SELECT state,phase,version FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            attempt = self.connection.execute(
                "SELECT id,status,child_record FROM attempts "
                "WHERE run_id=? AND attempt_token=?",
                (run_id, attempt_token),
            ).fetchone()
            if (not run or not attempt or run["state"] not in {"running", "cancelling"}
                    or run["phase"] is not None
                    or attempt["status"] not in {"running", "cancelling"}):
                raise ConflictError("unstarted attempt recovery is fenced")
            if not attempt["child_record"]:
                raise ConflictError("unstarted attempt has no durable child-record path")
            if Path(attempt["child_record"]).exists() or Path(attempt["child_record"]).is_symlink():
                raise ConflictError("unstarted attempt now has a child identity record")
            now = _utc_now()
            if run["state"] == "cancelling":
                if prepared is not None:
                    version, _ = self._reference_prepared_artifacts(run_id, run["version"], prepared)
                    version += 1
                else:
                    path, digest, size, receipt_time = self._terminal_receipt(
                        run_id, "cancelled", "cancelling", None, attempt_id=attempt["id"], now=now,
                    )
                    version = self._reference_terminal_receipt(
                        run_id, run["version"], path, digest, size, receipt_time,
                    ) + 1
                self.connection.execute(
                    "UPDATE attempts SET status='finished',finished_at=? WHERE id=?",
                    (now, attempt["id"]),
                )
                self.connection.execute(
                    "UPDATE supervisor_claims SET active=0 WHERE run_id=?", (run_id,),
                )
                self.connection.execute(
                    "UPDATE runs SET state='cancelled',phase=NULL,version=?,updated_at=? "
                    "WHERE id=?",
                    (version, now, run_id),
                )
                payload = canonical_json({
                    "attempt_id": attempt["id"],
                    "reason": reason,
                    "receipt": "result-receipt.json",
                })
                self.connection.execute(
                    "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                    "VALUES(?,?,'run.cancelled',?,?)",
                    (run_id, version, payload, now),
                )
                disposition = "cancelled"
            else:
                version = run["version"] + 1
                self.connection.execute(
                    "UPDATE attempts SET status='recovery_required',finished_at=? WHERE id=?",
                    (now, attempt["id"]),
                )
                self.connection.execute(
                    "UPDATE supervisor_claims SET active=0 WHERE run_id=?", (run_id,),
                )
                self.connection.execute(
                    "UPDATE runs SET state='queued',phase=NULL,version=?,updated_at=? WHERE id=?",
                    (version, now, run_id),
                )
                self.connection.execute(
                    "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                    "VALUES(?,?,'run.unstarted_attempt_recovered',?,?)",
                    (run_id, version, canonical_json({
                        "attempt_id": attempt["id"], "reason": reason,
                    }), now),
                )
                disposition = "requeued"
            self.connection.execute("COMMIT")
            return version, disposition
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def request_recovery_cancel(self, run_id: str, attempt_token: str) -> int:
        """Persist cancellation intent for an ownerless blocked attempt."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute(
                "SELECT state,phase,version FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            attempt = self.connection.execute(
                "SELECT id,status FROM attempts WHERE run_id=? AND attempt_token=?",
                (run_id, attempt_token),
            ).fetchone()
            if not run or not attempt:
                raise ConflictError("recovery cancellation is fenced")
            if run["state"] in TERMINAL_STATES:
                self.connection.execute("COMMIT")
                return run["version"]
            if run["state"] == "cancelling" and attempt["status"] == "cancelling":
                self.connection.execute("COMMIT")
                return run["version"]
            if (run["state"] != "blocked" or run["phase"] != "recovery_required"
                    or attempt["status"] not in {"ownership_ambiguous", "recovery_required"}):
                raise ConflictError("run is not awaiting recovery cancellation")
            version, now = run["version"] + 1, _utc_now()
            self.connection.execute(
                "UPDATE attempts SET status='cancelling',heartbeat_at=?,finished_at=NULL WHERE id=?",
                (now, attempt["id"]),
            )
            self.connection.execute(
                "UPDATE runs SET state='cancelling',phase='recovery_cleanup',"
                "version=?,updated_at=? WHERE id=?",
                (version, now, run_id),
            )
            payload = canonical_json({"attempt_id": attempt["id"]})
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'run.cancelling',?,?)",
                (run_id, version, payload, now),
            )
            self.connection.execute("COMMIT")
            return version
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    @_project_terminal
    def finish_recovery_cancel(
        self, run_id: str, attempt_token: str, reason: str,
    ) -> int:
        """Finish an ownerless cancellation only after absence is confirmed."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute(
                "SELECT state,phase,version FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            attempt = self.connection.execute(
                "SELECT id,status FROM attempts WHERE run_id=? AND attempt_token=?",
                (run_id, attempt_token),
            ).fetchone()
            if not run or not attempt:
                raise ConflictError("recovery cancellation completion is fenced")
            if run["state"] in TERMINAL_STATES:
                self.connection.execute("COMMIT")
                return run["version"]
            if (run["state"] != "cancelling" or run["phase"] != "recovery_cleanup"
                    or attempt["status"] != "cancelling"):
                raise ConflictError("recovery cancellation is not active")
            now = _utc_now()
            path, digest, size, receipt_time = self._terminal_receipt(
                run_id,
                "cancelled",
                "recovery_cleanup",
                None,
                attempt_id=attempt["id"],
                now=now,
            )
            version = self._reference_terminal_receipt(
                run_id, run["version"], path, digest, size, receipt_time,
            ) + 1
            self.connection.execute(
                "UPDATE attempts SET status='finished',finished_at=? WHERE id=?",
                (now, attempt["id"]),
            )
            self.connection.execute(
                "UPDATE supervisor_claims SET active=0 WHERE run_id=?", (run_id,),
            )
            self.connection.execute(
                "UPDATE runs SET state='cancelled',phase=NULL,version=?,updated_at=? WHERE id=?",
                (version, now, run_id),
            )
            payload = canonical_json({
                "attempt_id": attempt["id"],
                "reason": reason,
                "receipt": "result-receipt.json",
            })
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'run.cancelled',?,?)",
                (run_id, version, payload, now),
            )
            self.connection.execute("COMMIT")
            return version
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def fail_launch(self, reservation: AttemptReservation, reason: str) -> int:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute("SELECT phase,version FROM runs WHERE id=?", (reservation.run_id,)).fetchone()
            attempt = self.connection.execute("SELECT status,attempt_token FROM attempts WHERE id=?", (reservation.attempt_id,)).fetchone()
            if not run or run["phase"] != "launching" or not attempt or attempt["status"] != "reserved" or attempt["attempt_token"] != reservation.attempt_token:
                raise ConflictError("launch failure disposition is fenced")
            version, now = run["version"] + 1, _utc_now()
            self.connection.execute("UPDATE attempts SET status='recovery_required',finished_at=? WHERE id=?", (now, reservation.attempt_id))
            self.connection.execute("UPDATE supervisor_claims SET active=0 WHERE run_id=? AND fencing_token=?", (reservation.run_id, reservation.supervisor_token))
            self.connection.execute("UPDATE runs SET state='blocked',phase='recovery_required',version=?,updated_at=? WHERE id=?", (version, now, reservation.run_id))
            payload = canonical_json({"reason": reason, "next_action": "RECOVERY_REQUIRED"})
            self.connection.execute("INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,'run.blocked',?,?)", (reservation.run_id, version, payload, now))
            self.connection.execute("COMMIT")
            return version
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def recover_launching(self, run_id: str, expected_version: int) -> int:
        """Fence an abandoned pre-gate reservation and make the run launchable."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute(
                "SELECT state,phase,version FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            attempt = self.connection.execute(
                "SELECT id,status FROM attempts WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            claim = self.connection.execute(
                "SELECT active FROM supervisor_claims WHERE run_id=?", (run_id,),
            ).fetchone()
            if (not run or run["state"] != "queued" or run["phase"] != "launching"
                    or run["version"] != expected_version or not attempt
                    or attempt["status"] != "reserved" or not claim or not claim["active"]):
                raise ConflictError("launch reservation is no longer recoverable")
            version, now = expected_version + 1, _utc_now()
            self.connection.execute(
                "UPDATE attempts SET status='recovery_required',finished_at=? WHERE id=?",
                (now, attempt["id"]),
            )
            self.connection.execute(
                "UPDATE supervisor_claims SET active=0 WHERE run_id=?", (run_id,),
            )
            self.connection.execute(
                "UPDATE runs SET phase=NULL,version=?,updated_at=? WHERE id=?",
                (version, now, run_id),
            )
            payload = canonical_json({
                "attempt_id": attempt["id"],
                "reason": "abandoned gated launch reservation",
            })
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'run.launch_recovered',?,?)",
                (run_id, version, payload, now),
            )
            self.connection.execute("COMMIT")
            return version
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def active_attempt(self, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM attempts WHERE run_id=? AND status IN ('reserved','running','cancelling','ownership_ambiguous') ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _handoff_claim_from_row(row: sqlite3.Row, action: str) -> HandoffClaim:
        return HandoffClaim(
            run_id=row["run_id"],
            handoff_id=row["handoff_id"],
            owner_id=row["owner_id"],
            fencing_token=row["fencing_token"],
            expires_at=row["lease_expires_at"],
            run_version=row["run_version"],
            action=action,
        )

    @classmethod
    def _handoff_snapshot_from_rows(
        cls,
        run_id: str,
        run: sqlite3.Row,
        handoff: sqlite3.Row | None,
        claim_row: sqlite3.Row | None,
    ) -> HandoffSnapshot | None:
        if handoff is None:
            return None
        packet_json = handoff["packet_json"]
        if hashlib.sha256(packet_json.encode()).hexdigest() != handoff["packet_sha256"]:
            raise ConflictError("persisted handoff packet hash does not match")
        try:
            packet = json.loads(packet_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ConflictError("persisted handoff packet is invalid") from exc
        if not isinstance(packet, dict) or canonical_json(packet) != packet_json:
            raise ConflictError("persisted handoff packet is not canonical finite JSON")
        claim = cls._handoff_claim_from_row(claim_row, "current") if claim_row else None
        return HandoffSnapshot(
            run_id=run_id,
            run_state=run["state"],
            run_phase=run["phase"],
            run_version=run["version"],
            handoff_id=handoff["id"],
            sequence=handoff["sequence"],
            status=handoff["status"],
            packet=packet,
            packet_sha256=handoff["packet_sha256"],
            created_run_version=handoff["created_run_version"],
            submitted_run_version=handoff["submitted_run_version"],
            created_at=handoff["created_at"],
            closed_at=handoff["closed_at"],
            claim=claim,
        )

    def publish_handoff(
        self,
        run_id: str,
        expected_version: int,
        attempt_token: str,
        supervisor_token: int,
        packet: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> HandoffSnapshot:
        """Release a reconciled writer and publish one immutable host packet.

        Process absence is established by the owning supervisor before it calls
        this storage transition, just as it is before an ordinary attempt
        completion. The persisted attempt and supervisor fences ensure a stale
        coordinator cannot publish after ownership has moved.
        """
        if (type(expected_version) is not int or expected_version < 1
                or not isinstance(attempt_token, str) or not attempt_token
                or type(supervisor_token) is not int
                or supervisor_token < 1 or not isinstance(packet, dict)):
            raise ContractError("handoff publication requires valid ownership and packet fields")
        packet_json = canonical_json(packet)
        packet_sha256 = hashlib.sha256(packet_json.encode()).hexdigest()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            timestamp = _authoritative_now(now).isoformat()
            row = self.connection.execute(
                "SELECT r.state,r.phase,r.version,a.id AS attempt_id,a.status AS attempt_status,"
                "s.fencing_token AS supervisor_token,s.active AS supervisor_active "
                "FROM runs r JOIN attempts a ON a.run_id=r.id "
                "JOIN supervisor_claims s ON s.run_id=r.id "
                "WHERE r.id=? AND a.attempt_token=?",
                (run_id, attempt_token),
            ).fetchone()
            if (not row or row["state"] != "running" or row["phase"] is not None
                    or row["version"] != expected_version
                    or row["attempt_status"] != "running"
                    or not row["supervisor_active"]
                    or row["supervisor_token"] != supervisor_token):
                raise ConflictError("handoff publication is fenced")
            if self.connection.execute(
                "SELECT 1 FROM handoffs WHERE run_id=? AND status IN ('open','submitted')",
                (run_id,),
            ).fetchone():
                raise ConflictError("run already has a pending handoff")
            sequence = self.connection.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM handoffs WHERE run_id=?", (run_id,),
            ).fetchone()[0]
            handoff_id, version = str(uuid.uuid4()), expected_version + 1
            self.connection.execute(
                "INSERT INTO handoffs(id,run_id,sequence,packet_json,packet_sha256,status,"
                "created_run_version,created_at) VALUES(?,?,?,?,?,'open',?,?)",
                (handoff_id, run_id, sequence, packet_json, packet_sha256, version, timestamp),
            )
            self.connection.execute(
                "UPDATE attempts SET status='finished',finished_at=? WHERE id=?",
                (timestamp, row["attempt_id"]),
            )
            self.connection.execute(
                "UPDATE supervisor_claims SET active=0 WHERE run_id=? AND fencing_token=?",
                (run_id, supervisor_token),
            )
            self.connection.execute(
                "UPDATE runs SET state='awaiting_host',phase=NULL,version=?,updated_at=? WHERE id=?",
                (version, timestamp, run_id),
            )
            payload = canonical_json({
                "handoff_id": handoff_id,
                "packet_sha256": packet_sha256,
                "sequence": sequence,
            })
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'run.awaiting_host',?,?)",
                (run_id, version, payload, timestamp),
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        snapshot = self.handoff_snapshot(run_id)
        if snapshot is None:  # Defensive: the just-committed projection must exist.
            raise ConflictError("published handoff is missing")
        return snapshot

    def handoff_snapshot(self, run_id: str) -> HandoffSnapshot | None:
        self.connection.execute("BEGIN")
        try:
            run = self.connection.execute(
                "SELECT state,phase,version FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            if not run:
                raise ContractError("run does not exist")
            handoff = self.connection.execute(
                "SELECT * FROM handoffs WHERE run_id=? ORDER BY sequence DESC LIMIT 1", (run_id,),
            ).fetchone()
            claim_row = None
            if handoff:
                claim_row = self.connection.execute(
                    "SELECT run_id,handoff_id,owner_id,fencing_token,lease_expires_at,"
                    "? AS run_version FROM claims WHERE run_id=? AND kind='host' "
                    "AND handoff_id=? AND active=1",
                    (run["version"], run_id, handoff["id"]),
                ).fetchone()
            snapshot = self._handoff_snapshot_from_rows(
                run_id, run, handoff, claim_row,
            )
            self.connection.execute("COMMIT")
            return snapshot
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def handoff_snapshot_by_id(
        self, run_id: str, handoff_id: str,
    ) -> HandoffSnapshot:
        if not isinstance(handoff_id, str) or not handoff_id:
            raise ContractError("handoff id is required")
        self.connection.execute("BEGIN")
        try:
            run = self.connection.execute(
                "SELECT state,phase,version FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            if not run:
                raise ContractError("run does not exist")
            handoff = self.connection.execute(
                "SELECT * FROM handoffs WHERE id=? AND run_id=?", (handoff_id, run_id),
            ).fetchone()
            if not handoff:
                raise ConflictError("handoff belongs to a different run")
            claim_row = self.connection.execute(
                "SELECT run_id,handoff_id,owner_id,fencing_token,lease_expires_at,"
                "? AS run_version FROM claims WHERE run_id=? AND kind='host' "
                "AND handoff_id=? AND active=1",
                (run["version"], run_id, handoff_id),
            ).fetchone()
            snapshot = self._handoff_snapshot_from_rows(
                run_id, run, handoff, claim_row,
            )
            if snapshot is None:  # Defensive: handoff was selected above.
                raise ConflictError("handoff is missing")
            self.connection.execute("COMMIT")
            return snapshot
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def branch_review_history(self, run_id: str) -> list[dict[str, Any]]:
        """Return canonical recorded handoffs and decisions in workflow order."""
        if not self.connection.execute(
            "SELECT 1 FROM runs WHERE id=?", (run_id,),
        ).fetchone():
            raise ContractError("run does not exist")
        rows = self.connection.execute(
            "SELECT h.id AS handoff_id,h.sequence,h.packet_json,h.packet_sha256,"
            "s.submission_id,s.submission_hash,s.disposition,s.decision_json,"
            "s.recorded_run_version "
            "FROM handoffs h JOIN handoff_submissions s ON s.handoff_id=h.id "
            "WHERE h.run_id=? AND s.outcome='recorded' ORDER BY h.sequence",
            (run_id,),
        ).fetchall()
        history = []
        for row in rows:
            try:
                packet = json.loads(row["packet_json"])
                decision = json.loads(row["decision_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ConflictError("persisted branch review history is invalid") from exc
            if (not isinstance(packet, dict)
                    or canonical_json(packet) != row["packet_json"]
                    or hashlib.sha256(row["packet_json"].encode()).hexdigest()
                    != row["packet_sha256"]
                    or not isinstance(decision, dict)
                    or canonical_json(decision) != row["decision_json"]):
                raise ConflictError("persisted branch review history hash is invalid")
            submission_id, submission_hash, disposition, _, _ = (
                self._validated_handoff_decision(run_id, decision)
            )
            if (submission_id != row["submission_id"]
                    or submission_hash != row["submission_hash"]
                    or disposition != row["disposition"]):
                raise ConflictError("persisted branch review decision is inconsistent")
            history.append({
                "handoff_id": row["handoff_id"],
                "sequence": row["sequence"],
                "packet": packet,
                "packet_sha256": row["packet_sha256"],
                "decision": decision,
                "recorded_run_version": row["recorded_run_version"],
            })
        return history

    def recorded_handoff_submission(
        self, run_id: str, handoff_id: str,
    ) -> dict[str, Any] | None:
        for entry in self.branch_review_history(run_id):
            if entry["handoff_id"] == handoff_id:
                return entry
        return None

    def _terminal_finish_for_claim(
        self, run_id: str, row: sqlite3.Row,
    ) -> dict[str, Any] | None:
        """Only the exact latest acquisition event proves terminal authority."""
        if (row["kind"] != "host" or not row["active"]
                or row["owner_id"] != "terminal-operator"
                or row["claim_handoff_id"] != row["handoff_id"]):
            return None
        event = self.connection.execute(
            "SELECT type,payload FROM events WHERE run_id=? "
            "AND type IN ('handoff.acquired','handoff.taken_over','handoff.renewed') "
            "ORDER BY id DESC LIMIT 1", (run_id,),
        ).fetchone()
        if event is None:
            return None
        try:
            payload = json.loads(event["payload"])
            if not isinstance(payload, dict) or "terminal_finish" not in payload:
                return None
            if (set(payload) != {"action", "expires_at", "fencing_token", "handoff_id", "owner_id", "terminal_finish"}
                    or canonical_json(payload) != event["payload"]):
                raise ConflictError("persisted terminal finish marker is invalid")
            marker = payload["terminal_finish"]
            if (not isinstance(marker, dict)
                    or set(marker) != {"schema_version", "packet_sha256", "decision"}
                    or type(marker["schema_version"]) is not int
                    or marker["schema_version"] != 1):
                raise ConflictError("persisted terminal finish intent is invalid")
            # A later app claim or renewal invalidates older terminal authority,
            # including when it happens to use the same human-readable owner.
            if (type(payload["fencing_token"]) is not int
                    or payload["fencing_token"] != row["fencing_token"]
                    or payload["expires_at"] != row["lease_expires_at"]
                    or payload["handoff_id"] != row["handoff_id"]
                    or payload["owner_id"] != row["owner_id"]
                    or event["type"] != f"handoff.{payload['action']}"
                    or marker["packet_sha256"] != row["packet_sha256"]):
                return None
            self._validated_handoff_decision(run_id, marker["decision"])
            if marker["decision"]["submission_id"] != f"terminal-{row['handoff_id']}":
                raise ConflictError("persisted terminal finish targets a different handoff")
            return marker["decision"]
        except (TypeError, ValueError, ContractError) as exc:
            raise ConflictError("persisted terminal finish intent is invalid") from exc

    def terminal_finish_decision(
        self, run_id: str, handoff_id: str,
    ) -> dict[str, Any] | None:
        """Read a pending guided choice without exposing or inventing a claim."""
        row = self.connection.execute(
            "SELECT h.id AS handoff_id,h.packet_sha256,c.kind,c.owner_id,"
            "c.fencing_token,c.active,c.handoff_id AS claim_handoff_id,c.lease_expires_at "
            "FROM runs r JOIN handoffs h ON h.run_id=r.id JOIN claims c ON c.run_id=r.id "
            "WHERE r.id=? AND h.id=? AND r.state='awaiting_host' AND r.phase IS NULL "
            "AND h.status='open'", (run_id, handoff_id),
        ).fetchone()
        return self._terminal_finish_for_claim(run_id, row) if row is not None else None

    def claim_handoff(
        self,
        run_id: str,
        expected_version: int,
        owner_id: str,
        prior_claim: HandoffClaim | None = None,
        *,
        now: datetime | None = None,
        initial_only: bool = False,
        terminal_decision: dict[str, Any] | None = None,
    ) -> HandoffClaim:
        if (type(expected_version) is not int or expected_version < 1
                or not isinstance(owner_id, str) or not owner_id):
            raise ContractError("handoff claim requires a run version and owner")
        if prior_claim is not None and not isinstance(prior_claim, HandoffClaim):
            raise ContractError("prior handoff claim is invalid")
        if terminal_decision is not None and (
                not initial_only or prior_claim is not None
                or owner_id != "terminal-operator"):
            raise ContractError("terminal finish requires its own initial guided claim")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = _authoritative_now(now)
            timestamp = current.isoformat()
            expires_at = (current + timedelta(seconds=HOST_LEASE_SECONDS)).isoformat()
            row = self.connection.execute(
                "SELECT r.state,r.phase,r.version,h.id AS handoff_id,h.status,h.packet_sha256,"
                "c.kind,c.owner_id,c.fencing_token,c.active,c.handoff_id AS claim_handoff_id,"
                "c.lease_expires_at "
                "FROM runs r JOIN handoffs h ON h.run_id=r.id "
                "JOIN claims c ON c.run_id=r.id "
                "WHERE r.id=? ORDER BY h.sequence DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if not row:
                raise ContractError("run has no handoff")
            if (row["state"] != "awaiting_host" or row["phase"] is not None
                    or row["status"] != "open" or row["version"] != expected_version):
                raise ConflictError("handoff is not claimable at that run version")
            terminal_marker = None
            if terminal_decision is not None:
                self._validated_handoff_decision(run_id, terminal_decision)
                if terminal_decision["submission_id"] != f"terminal-{row['handoff_id']}":
                    raise ConflictError("terminal finish decision targets a different handoff")
                if (not terminal_decision["reason"].strip()
                        or len(terminal_decision["reason"]) > 2000):
                    raise ContractError("terminal finish requires a bounded non-empty reason")
                terminal_marker = {
                    "schema_version": 1, "packet_sha256": row["packet_sha256"],
                    "decision": json.loads(canonical_json(terminal_decision)),
                }
            prior_host_claim = row["kind"] == "host" and row["claim_handoff_id"] == row["handoff_id"]
            if initial_only and prior_host_claim:
                saved = self._terminal_finish_for_claim(run_id, row) if terminal_marker is not None else None
                if saved is None:
                    raise ConflictError("handoff already has a host claim; use its saved claim to complete or renew it")
                if canonical_json(saved) != canonical_json(terminal_decision):
                    raise ConflictError("a different terminal finish intent is pending; retry its exact saved decision")
            live = bool(
                row["kind"] == "host" and row["active"]
                and row["claim_handoff_id"] == row["handoff_id"]
                and row["lease_expires_at"]
                and current < _parse_utc(row["lease_expires_at"])
            )
            if initial_only and prior_host_claim and live:
                # Exact live recovery does not renew, mutate or increment a
                # fence. The durable event retains the capability after a crash.
                self.connection.execute("COMMIT")
                return HandoffClaim(
                    run_id, row["handoff_id"], owner_id, row["fencing_token"],
                    row["lease_expires_at"], expected_version, "current",
                )
            if prior_claim is not None:
                if (not live or prior_claim.run_id != run_id
                        or prior_claim.handoff_id != row["handoff_id"]
                        or prior_claim.owner_id != owner_id
                        or row["owner_id"] != owner_id
                        or prior_claim.fencing_token != row["fencing_token"]
                        or prior_claim.expires_at != row["lease_expires_at"]):
                    raise ConflictError("handoff renewal claim is stale or expired")
                token, action = row["fencing_token"], "renewed"
                self.connection.execute(
                    "UPDATE claims SET lease_expires_at=?,renewed_at=? WHERE run_id=?",
                    (expires_at, timestamp, run_id),
                )
            else:
                if live:
                    raise ConflictError("handoff already has a live claim")
                token = row["fencing_token"] + 1
                action = "taken_over" if prior_host_claim else "acquired"
                self.connection.execute(
                    "UPDATE claims SET kind='host',fencing_token=?,owner_id=?,active=1,"
                    "claimed_at=?,handoff_id=?,lease_expires_at=?,renewed_at=NULL WHERE run_id=?",
                    (token, owner_id, timestamp, row["handoff_id"], expires_at, run_id),
                )
            version = expected_version + 1
            self.connection.execute(
                "UPDATE runs SET version=?,updated_at=? WHERE id=?", (version, timestamp, run_id),
            )
            payload = canonical_json({
                "action": action,
                "expires_at": expires_at,
                "fencing_token": token,
                "handoff_id": row["handoff_id"],
                "owner_id": owner_id,
                **({"terminal_finish": terminal_marker} if terminal_marker is not None else {}),
            })
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,?,?,?)",
                (run_id, version, f"handoff.{action}", payload, timestamp),
            )
            self.connection.execute("COMMIT")
            return HandoffClaim(
                run_id=run_id,
                handoff_id=row["handoff_id"],
                owner_id=owner_id,
                fencing_token=token,
                expires_at=expires_at,
                run_version=version,
                action=action,
            )
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def _validated_handoff_decision(
        self, run_id: str, decision: dict[str, Any],
    ) -> tuple[str, str, str, str, list[dict[str, str]]]:
        expected_fields = {
            "schema_version", "submission_id", "submission_hash",
            "disposition", "reason", "evidence_refs",
        }
        snapshot = json.loads(self.run(run_id)["mutable_snapshot"] or "{}")
        if snapshot.get("task", {}).get("workflow") == "council-decision":
            expected_fields.add("council_choice")
        if not isinstance(decision, dict) or set(decision) != expected_fields:
            raise ContractError("handoff decision fields are invalid")
        if decision["schema_version"] != 1 or type(decision["schema_version"]) is not int:
            raise ContractError("handoff decision schema_version is invalid")
        submission_id = decision["submission_id"]
        disposition = decision["disposition"]
        reason = decision["reason"]
        evidence_refs = decision["evidence_refs"]
        if not isinstance(submission_id, str) or not submission_id:
            raise ContractError("handoff submission_id is required")
        if disposition not in {"accept", "revise", "reject"}:
            raise ContractError("handoff disposition is invalid")
        if not isinstance(reason, str) or (disposition == "revise" and not reason):
            raise ContractError("handoff decision reason is invalid")
        if not isinstance(evidence_refs, list):
            raise ContractError("handoff evidence_refs must be an array")
        normalized_refs = []
        for evidence in evidence_refs:
            if (not isinstance(evidence, dict) or set(evidence) != {"artifact_id", "sha256"}
                    or not isinstance(evidence["artifact_id"], str)
                    or not evidence["artifact_id"]
                    or not isinstance(evidence["sha256"], str)):
                raise ContractError("handoff evidence reference is invalid")
            artifact = self.connection.execute(
                "SELECT sha256 FROM artifacts WHERE id=? AND run_id=?",
                (evidence["artifact_id"], run_id),
            ).fetchone()
            if not artifact or artifact["sha256"] != evidence["sha256"]:
                raise ContractError("handoff evidence does not match a run artifact")
            normalized_refs.append({
                "artifact_id": evidence["artifact_id"], "sha256": evidence["sha256"],
            })
        body = {key: decision[key] for key in expected_fields if key != "submission_hash"}
        expected_hash = request_hash(body)
        if decision["submission_hash"] != expected_hash:
            raise ContractError("handoff submission hash does not match the decision")
        return submission_id, expected_hash, disposition, reason, normalized_refs

    def record_handoff_submission(
        self,
        run_id: str,
        claim: HandoffClaim,
        decision: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> HandoffSubmission:
        if not isinstance(claim, HandoffClaim) or claim.run_id != run_id:
            raise ContractError("handoff completion claim is invalid")
        submission_id, submission_hash, disposition, _, evidence_refs = (
            self._validated_handoff_decision(run_id, decision)
        )
        decision_json = canonical_json(decision)
        evidence_json = canonical_json(evidence_refs)
        rejection = None
        result = None
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = _authoritative_now(now)
            timestamp = current.isoformat()
            run = self.connection.execute(
                "SELECT state,phase,version FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            if not run:
                raise ContractError("run does not exist")
            handoff = self.connection.execute(
                "SELECT * FROM handoffs WHERE id=? AND run_id=?", (claim.handoff_id, run_id),
            ).fetchone()
            if not handoff:
                raise ConflictError("handoff completion targets a different run")
            prior = self.connection.execute(
                "SELECT * FROM handoff_submissions WHERE handoff_id=? AND submission_id=? "
                "AND submission_hash=? ORDER BY created_at LIMIT 1",
                (claim.handoff_id, submission_id, submission_hash),
            ).fetchone()
            if prior:
                if prior["outcome"] == "recorded":
                    self.connection.execute("COMMIT")
                    return HandoffSubmission(
                        run_id=run_id,
                        handoff_id=claim.handoff_id,
                        submission_id=submission_id,
                        submission_hash=submission_hash,
                        disposition=prior["disposition"],
                        recorded_run_version=prior["recorded_run_version"],
                        replayed=True,
                    )
                rejection = prior["rejection_code"] or "handoff_submission_rejected"
            reused_id = self.connection.execute(
                "SELECT 1 FROM handoff_submissions WHERE handoff_id=? AND submission_id=? "
                "AND submission_hash<>?",
                (claim.handoff_id, submission_id, submission_hash),
            ).fetchone()
            if reused_id and rejection is None:
                rejection = "submission_id_reused"
            persisted_claim = self.connection.execute(
                "SELECT c.kind,c.owner_id,c.fencing_token,c.active,c.lease_expires_at,"
                "c.handoff_id AS claim_handoff_id,h.id AS handoff_id,h.packet_sha256 "
                "FROM claims c JOIN handoffs h ON h.run_id=c.run_id "
                "WHERE c.run_id=? AND h.id=?", (run_id, claim.handoff_id),
            ).fetchone()
            recovered_rejection = None
            if (prior is not None and prior["outcome"] == "rejected"
                    and isinstance(prior["id"], str) and prior["id"]
                    and prior["rejection_code"] == "expired_claim"
                    and claim.owner_id == "terminal-operator"
                    and prior["owner_id"] == claim.owner_id
                    and type(prior["fencing_token"]) is int
                    and prior["fencing_token"] < claim.fencing_token
                    and prior["decision_json"] == decision_json
                    and prior["evidence_refs_json"] == evidence_json
                    and prior["disposition"] == disposition
                    and type(prior["recorded_run_version"]) is int
                    and prior["recorded_run_version"] < run["version"]
                    and reused_id is None
                    and persisted_claim is not None
                    and claim.expires_at == persisted_claim["lease_expires_at"]):
                saved = self._terminal_finish_for_claim(run_id, persisted_claim)
                if saved is not None and canonical_json(saved) == decision_json:
                    rejected_event = self.connection.execute(
                        "SELECT run_id,run_version,type,payload,created_at FROM events "
                        "WHERE run_id=? AND run_version=?",
                        (run_id, prior["recorded_run_version"]),
                    ).fetchone()
                    expected_payload = canonical_json({
                        "handoff_id": prior["handoff_id"],
                        "reason": "expired_claim",
                        "submission_hash": prior["submission_hash"],
                        "submission_id": prior["submission_id"],
                    })
                    if (rejected_event is None
                            or rejected_event["run_id"] != run_id
                            or rejected_event["run_version"] != prior["recorded_run_version"]
                            or rejected_event["type"] != "handoff.completion_rejected"
                            or rejected_event["created_at"] != prior["created_at"]
                            or rejected_event["payload"] != expected_payload):
                        raise ConflictError("expired terminal finish rejection audit is invalid")
                    # The row is an operational projection with a unique
                    # decision identity. Preserve its complete rejected state
                    # in the append-only log before changing that projection.
                    if _parse_utc(prior["created_at"]) > current:
                        raise ConflictError("expired terminal finish rejection timestamp is invalid")
                    recovered_rejection = dict(prior)
                    rejection = None
            if rejection is None:
                if run["state"] in TERMINAL_STATES:
                    rejection = "terminal_run"
                elif (run["state"] != "awaiting_host" or run["phase"] is not None
                        or handoff["status"] != "open"):
                    rejection = "handoff_not_open"
                elif (not persisted_claim or persisted_claim["kind"] != "host"
                        or not persisted_claim["active"]
                        or persisted_claim["claim_handoff_id"] != claim.handoff_id
                        or persisted_claim["owner_id"] != claim.owner_id
                        or persisted_claim["fencing_token"] != claim.fencing_token):
                    rejection = "stale_claim"
                elif (not persisted_claim["lease_expires_at"]
                        or current >= _parse_utc(persisted_claim["lease_expires_at"])):
                    rejection = "expired_claim"
            if rejection is None:
                version = run["version"] + 1
                if recovered_rejection is not None:
                    payload = canonical_json({
                        "disposition": disposition,
                        "handoff_id": claim.handoff_id,
                        "fencing_token": claim.fencing_token,
                        "submission_hash": submission_hash,
                        "submission_id": submission_id,
                        "rejected_submission": recovered_rejection,
                        "rejected_submission_sha256": request_hash(recovered_rejection),
                    })
                    self.connection.execute(
                        "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                        "VALUES(?,?,'handoff.completion_recovered',?,?)",
                        (run_id, version, payload, timestamp),
                    )
                    version += 1
                    updated = self.connection.execute(
                        "UPDATE handoff_submissions SET owner_id=?,fencing_token=?,outcome='recorded',"
                        "rejection_code=NULL,recorded_run_version=?,created_at=? "
                        "WHERE id=? AND outcome='rejected' AND rejection_code='expired_claim'",
                        (claim.owner_id, claim.fencing_token, version, timestamp, prior["id"]),
                    )
                    if updated.rowcount != 1:
                        raise ConflictError("expired terminal finish projection changed")
                else:
                    self.connection.execute(
                        "INSERT INTO handoff_submissions(id,handoff_id,submission_id,submission_hash,"
                        "owner_id,fencing_token,disposition,decision_json,evidence_refs_json,outcome,"
                        "rejection_code,recorded_run_version,created_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,'recorded',NULL,?,?)",
                        (str(uuid.uuid4()), claim.handoff_id, submission_id, submission_hash,
                         claim.owner_id, claim.fencing_token, disposition, decision_json,
                         evidence_json, version, timestamp),
                    )
                self.connection.execute(
                    "UPDATE handoffs SET status='submitted',submitted_run_version=?,closed_at=? "
                    "WHERE id=?", (version, timestamp, claim.handoff_id),
                )
                self.connection.execute(
                    "UPDATE claims SET active=0 WHERE run_id=? AND handoff_id=?",
                    (run_id, claim.handoff_id),
                )
                self.connection.execute(
                    "UPDATE runs SET phase='handoff_submitted',version=?,updated_at=? WHERE id=?",
                    (version, timestamp, run_id),
                )
                payload = canonical_json({
                    "disposition": disposition,
                    "handoff_id": claim.handoff_id,
                    "submission_hash": submission_hash,
                    "submission_id": submission_id,
                })
                self.connection.execute(
                    "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                    "VALUES(?,?,'handoff.submitted',?,?)",
                    (run_id, version, payload, timestamp),
                )
                result = HandoffSubmission(
                    run_id=run_id,
                    handoff_id=claim.handoff_id,
                    submission_id=submission_id,
                    submission_hash=submission_hash,
                    disposition=disposition,
                    recorded_run_version=version,
                    replayed=False,
                )
            else:
                existing_rejection = self.connection.execute(
                    "SELECT recorded_run_version FROM handoff_submissions WHERE handoff_id=? "
                    "AND submission_id=? AND submission_hash=? AND outcome='rejected'",
                    (claim.handoff_id, submission_id, submission_hash),
                ).fetchone()
                if not existing_rejection:
                    terminal_audit = run["state"] in TERMINAL_STATES
                    version = run["version"] if terminal_audit else run["version"] + 1
                    self.connection.execute(
                        "INSERT INTO handoff_submissions(id,handoff_id,submission_id,submission_hash,"
                        "owner_id,fencing_token,disposition,decision_json,evidence_refs_json,outcome,"
                        "rejection_code,recorded_run_version,created_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,'rejected',?,?,?)",
                        (str(uuid.uuid4()), claim.handoff_id, submission_id, submission_hash,
                         claim.owner_id, claim.fencing_token, disposition, decision_json,
                         evidence_json, rejection, version, timestamp),
                    )
                    if not terminal_audit:
                        self.connection.execute(
                            "UPDATE runs SET version=?,updated_at=? WHERE id=?",
                            (version, timestamp, run_id),
                        )
                        payload = canonical_json({
                            "handoff_id": claim.handoff_id,
                            "reason": rejection,
                            "submission_hash": submission_hash,
                            "submission_id": submission_id,
                        })
                        self.connection.execute(
                            "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                            "VALUES(?,?,'handoff.completion_rejected',?,?)",
                            (run_id, version, payload, timestamp),
                        )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        if rejection is not None:
            raise ConflictError(f"handoff completion rejected: {rejection}")
        if result is None:
            raise ConflictError("handoff completion was not recorded")
        return result

    def requeue_review_revision(
        self,
        run_id: str,
        handoff_id: str,
        submission_id: str,
        submission_hash: str,
    ) -> dict[str, Any]:
        """Consume one saved revise decision and requeue within both task budgets."""
        if not all(
            isinstance(value, str) and value
            for value in (handoff_id, submission_id, submission_hash)
        ):
            raise ContractError("revision requeue identifiers are invalid")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT r.state,r.phase,r.version,r.mutable_snapshot,h.status,h.sequence,"
                "s.disposition FROM runs r JOIN handoffs h ON h.run_id=r.id "
                "JOIN handoff_submissions s ON s.handoff_id=h.id "
                "WHERE r.id=? AND h.id=? AND s.submission_id=? "
                "AND s.submission_hash=? AND s.outcome='recorded'",
                (run_id, handoff_id, submission_id, submission_hash),
            ).fetchone()
            if not row:
                raise ConflictError("recorded revision submission is missing")
            if row["disposition"] != "revise":
                raise ConflictError("handoff submission is not a revision request")
            latest_sequence = self.connection.execute(
                "SELECT MAX(sequence) FROM handoffs WHERE run_id=?", (run_id,),
            ).fetchone()[0]
            if row["status"] == "consumed":
                action = (
                    "requeued"
                    if row["sequence"] == latest_sequence
                    and row["state"] == "queued"
                    and row["phase"] is None
                    else "already_advanced"
                )
                self.connection.execute("COMMIT")
                return {
                    "action": action,
                    "version": row["version"],
                    "replayed": True,
                }
            if (row["state"] != "awaiting_host"
                    or row["phase"] != "handoff_submitted"
                    or row["status"] != "submitted"
                    or row["sequence"] != latest_sequence):
                raise ConflictError("revision handoff is no longer current")
            try:
                snapshot = json.loads(row["mutable_snapshot"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ConflictError("frozen review snapshot is invalid") from exc
            if (not isinstance(snapshot, dict)
                    or canonical_json(snapshot) != row["mutable_snapshot"]):
                raise ConflictError("frozen review snapshot is not canonical")
            try:
                budget = snapshot["task"]["budget"]
                max_revisions = budget["max_revisions"]
                max_invocations = budget["max_worker_invocations"]
                lead_mode = snapshot["task"]["lead"]["mode"]
            except (KeyError, TypeError) as exc:
                raise ConflictError("frozen review budget is missing") from exc
            if (type(max_revisions) is not int or max_revisions < 0
                    or type(max_invocations) is not int or max_invocations < 1):
                raise ConflictError("frozen review budget is invalid")
            revisions = self.connection.execute(
                "SELECT COUNT(*) FROM handoff_submissions s "
                "JOIN handoffs h ON h.id=s.handoff_id "
                "WHERE h.run_id=? AND s.outcome='recorded' "
                "AND s.disposition='revise' AND h.sequence<=?",
                (run_id, row["sequence"]),
            ).fetchone()[0]
            invocations = self.connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE run_id=?", (run_id,),
            ).fetchone()[0]
            required_invocations = 2 if lead_mode == "headless" else 1
            if (revisions > max_revisions
                    or invocations + required_invocations > max_invocations):
                self.connection.execute("COMMIT")
                return {
                    "action": "budget_exhausted",
                    "version": row["version"],
                    "replayed": False,
                    "revisions_requested": revisions,
                    "worker_invocations": invocations,
                }
            now, version = _utc_now(), row["version"] + 1
            self.connection.execute(
                "UPDATE handoffs SET status='consumed',closed_at=? WHERE id=?",
                (now, handoff_id),
            )
            self.connection.execute(
                "UPDATE runs SET state='queued',phase=NULL,version=?,updated_at=? "
                "WHERE id=?",
                (version, now, run_id),
            )
            payload = canonical_json({
                "handoff_id": handoff_id,
                "submission_id": submission_id,
                "revisions_requested": revisions,
                "worker_invocations": invocations,
            })
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'run.revision_queued',?,?)",
                (run_id, version, payload, now),
            )
            self.connection.execute("COMMIT")
            return {
                "action": "requeued",
                "version": version,
                "replayed": False,
                "revisions_requested": revisions,
                "worker_invocations": invocations,
            }
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def requeue_delivery_revision(
        self,
        run_id: str,
        handoff_id: str,
        submission_id: str,
        submission_hash: str,
    ) -> dict[str, Any]:
        """Consume a delivery revise decision and atomically return to its writer."""
        if not all(
            isinstance(value, str) and value
            for value in (handoff_id, submission_id, submission_hash)
        ):
            raise ContractError("delivery revision identifiers are invalid")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT r.state,r.phase,r.version,r.mutable_snapshot,h.status,h.sequence,"
                "h.packet_json,s.disposition,s.decision_json "
                "FROM runs r JOIN handoffs h ON h.run_id=r.id "
                "JOIN handoff_submissions s ON s.handoff_id=h.id "
                "WHERE r.id=? AND h.id=? AND s.submission_id=? "
                "AND s.submission_hash=? AND s.outcome='recorded'",
                (run_id, handoff_id, submission_id, submission_hash),
            ).fetchone()
            if not row:
                raise ConflictError("recorded delivery revision is missing")
            if row["disposition"] != "revise":
                raise ConflictError("delivery submission is not a revision request")
            latest_sequence = self.connection.execute(
                "SELECT MAX(sequence) FROM handoffs WHERE run_id=?", (run_id,),
            ).fetchone()[0]
            if row["status"] == "consumed":
                action = (
                    "requeued"
                    if row["sequence"] == latest_sequence
                    and row["state"] == "queued"
                    and row["phase"] is None
                    else "already_advanced"
                )
                self.connection.execute("COMMIT")
                return {
                    "action": action,
                    "version": row["version"],
                    "replayed": True,
                }
            if (row["state"] != "awaiting_host"
                    or row["phase"] != "handoff_submitted"
                    or row["status"] != "submitted"
                    or row["sequence"] != latest_sequence):
                raise ConflictError("delivery revision handoff is no longer current")
            try:
                snapshot = json.loads(row["mutable_snapshot"])
                packet = json.loads(row["packet_json"])
                decision = json.loads(row["decision_json"])
                task = snapshot["task"]
                budget = task["budget"]
                candidate = snapshot["candidate"]
                delivery = snapshot["delivery_workspace"]
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ConflictError("frozen delivery revision is invalid") from exc
            if (not isinstance(snapshot, dict)
                    or canonical_json(snapshot) != row["mutable_snapshot"]
                    or task.get("workflow") != "issue-delivery"
                    or packet.get("workflow") != "issue-delivery"
                    or packet.get("candidate_sha256")
                    != candidate.get("candidate_sha256")
                    or decision.get("submission_id") != submission_id
                    or decision.get("submission_hash") != submission_hash):
                raise ConflictError("frozen delivery revision changed its evidence")
            max_revisions = budget.get("max_revisions")
            max_invocations = budget.get("max_worker_invocations")
            lead_mode = task.get("lead", {}).get("mode")
            if (type(max_revisions) is not int or max_revisions < 0
                    or type(max_invocations) is not int or max_invocations < 1
                    or lead_mode not in {"host", "headless"}):
                raise ConflictError("frozen delivery budget is invalid")
            revisions = self.connection.execute(
                "SELECT COUNT(*) FROM handoff_submissions s "
                "JOIN handoffs h ON h.id=s.handoff_id "
                "WHERE h.run_id=? AND s.outcome='recorded' "
                "AND s.disposition='revise' AND h.sequence<=?",
                (run_id, row["sequence"]),
            ).fetchone()[0]
            invocations = self.connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE run_id=?", (run_id,),
            ).fetchone()[0]
            required_invocations = 3 if lead_mode == "headless" else 2
            wall_exhausted = self.remaining_wall_seconds(run_id) == 0
            if (revisions > max_revisions
                    or invocations + required_invocations > max_invocations
                    or wall_exhausted):
                self.connection.execute("COMMIT")
                return {
                    "action": "budget_exhausted",
                    "version": row["version"],
                    "replayed": False,
                    "revisions_requested": revisions,
                    "worker_invocations": invocations,
                    "wall_exhausted": wall_exhausted,
                }
            new_snapshot = json.loads(canonical_json(snapshot))
            review_fixture = new_snapshot.pop("internal_review_fixture", None)
            if review_fixture is not None:
                new_snapshot["pending_review_fixture"] = {
                    field: review_fixture[field]
                    for field in ("verdict", "summary", "findings")
                }
            revision_request = {
                "handoff_id": handoff_id,
                "sequence": row["sequence"],
                "submission_id": submission_id,
                "submission_hash": submission_hash,
                "previous_candidate_sha256": candidate["candidate_sha256"],
                "reason": decision["reason"],
                "review": packet["review"],
                "checks": packet["checks"],
                "evidence_refs": decision["evidence_refs"],
            }
            new_snapshot["revision_request"] = revision_request
            for field in ("candidate", "workspace", "check_workspace"):
                new_snapshot.pop(field, None)
            encoded_snapshot = canonical_json(new_snapshot)
            delivery_path = str(Path(delivery["path"]).resolve(strict=True))
            now, version = _utc_now(), row["version"] + 1
            self.connection.execute(
                "UPDATE handoffs SET status='consumed',closed_at=? WHERE id=?",
                (now, handoff_id),
            )
            self.connection.execute(
                "UPDATE runs SET mutable_snapshot=?,worktree_path=?,state='queued',"
                "phase=NULL,version=?,updated_at=? WHERE id=?",
                (encoded_snapshot, delivery_path, version, now, run_id),
            )
            payload = canonical_json({
                "handoff_id": handoff_id,
                "submission_id": submission_id,
                "previous_candidate_sha256": candidate["candidate_sha256"],
                "revisions_requested": revisions,
                "worker_invocations": invocations,
            })
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'delivery.revision_queued',?,?)",
                (run_id, version, payload, now),
            )
            self.connection.execute("COMMIT")
            return {
                "action": "requeued",
                "version": version,
                "replayed": False,
                "revisions_requested": revisions,
                "worker_invocations": invocations,
            }
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    @_project_terminal
    def complete_handoff_terminal(
        self,
        run_id: str,
        handoff_id: str,
        submission_id: str,
        submission_hash: str,
        artifacts: list[dict[str, Any]],
        terminal_state: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically publish M3 terminal reports and consume the host handoff."""
        if terminal_state not in {"succeeded", "failed"}:
            raise ContractError("branch review terminal state is invalid")
        if not all(
            isinstance(value, str) and value
            for value in (handoff_id, submission_id, submission_hash)
        ):
            raise ContractError("terminal handoff identifiers are invalid")
        prepared = self._prepare_exact_artifacts(
            run_id, artifacts, BRANCH_REVIEW_TERMINAL_ARTIFACTS,
        )
        encoded_payload = canonical_json(payload)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT r.state,r.phase,r.version,h.status,h.sequence,s.disposition "
                "FROM runs r JOIN handoffs h ON h.run_id=r.id "
                "JOIN handoff_submissions s ON s.handoff_id=h.id "
                "WHERE r.id=? AND h.id=? AND s.submission_id=? "
                "AND s.submission_hash=? AND s.outcome='recorded'",
                (run_id, handoff_id, submission_id, submission_hash),
            ).fetchone()
            if not row:
                raise ConflictError("recorded terminal submission is missing")
            expected_state = "succeeded" if row["disposition"] == "accept" else "failed"
            if terminal_state != expected_state:
                raise ContractError("terminal state contradicts the lead disposition")
            if row["state"] in TERMINAL_STATES:
                if row["state"] != terminal_state or row["status"] != "consumed":
                    raise ConflictError("terminal handoff was completed differently")
                self.connection.execute("COMMIT")
                return {
                    "state": row["state"],
                    "version": row["version"],
                    "replayed": True,
                }
            latest_sequence = self.connection.execute(
                "SELECT MAX(sequence) FROM handoffs WHERE run_id=?", (run_id,),
            ).fetchone()[0]
            if (row["state"] != "awaiting_host"
                    or row["phase"] != "handoff_submitted"
                    or row["status"] != "submitted"
                    or row["sequence"] != latest_sequence):
                raise ConflictError("terminal handoff is no longer current")
            version, _ = self._reference_prepared_artifacts(
                run_id, row["version"], prepared,
            )
            now, version = _utc_now(), version + 1
            self.connection.execute(
                "UPDATE handoffs SET status='consumed',closed_at=? WHERE id=?",
                (now, handoff_id),
            )
            self.connection.execute(
                "UPDATE claims SET active=0 WHERE run_id=? AND handoff_id=?",
                (run_id, handoff_id),
            )
            self.connection.execute(
                "UPDATE runs SET state=?,phase=NULL,version=?,updated_at=? WHERE id=?",
                (terminal_state, version, now, run_id),
            )
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,?,?,?)",
                (run_id, version, f"run.{terminal_state}", encoded_payload, now),
            )
            self.connection.execute("COMMIT")
            return {"state": terminal_state, "version": version, "replayed": False}
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    @_project_terminal
    def cancel_host_wait(
        self,
        run_id: str,
        *,
        now: datetime | None = None,
        terminal_artifacts: list[dict[str, Any]] | None = None,
    ) -> int:
        prepared = (
            self._prepare_exact_artifacts(
                run_id, terminal_artifacts, BRANCH_REVIEW_TERMINAL_ARTIFACTS,
            )
            if terminal_artifacts is not None else None
        )
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            timestamp = _authoritative_now(now).isoformat()
            run = self.connection.execute(
                "SELECT state,phase,version FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            if not run:
                raise ContractError("run does not exist")
            if run["state"] in TERMINAL_STATES:
                self.connection.execute("COMMIT")
                return run["version"]
            handoff = self.connection.execute(
                "SELECT id,status FROM handoffs WHERE run_id=? ORDER BY sequence DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if (run["state"] != "awaiting_host" or not handoff
                    or handoff["status"] not in {"open", "submitted"}
                    or run["phase"] not in {None, "handoff_submitted"}):
                raise ConflictError("run is not awaiting a cancellable host handoff")
            if prepared is None:
                path, digest, size, receipt_time = self._terminal_receipt(
                    run_id, "cancelled", "awaiting_host", None, now=timestamp,
                )
                version = self._reference_terminal_receipt(
                    run_id, run["version"], path, digest, size, receipt_time,
                )
            else:
                version, _ = self._reference_prepared_artifacts(
                    run_id, run["version"], prepared,
                )
            version += 1
            self.connection.execute(
                "UPDATE handoffs SET status='cancelled',closed_at=? WHERE id=?",
                (timestamp, handoff["id"]),
            )
            self.connection.execute(
                "UPDATE claims SET active=0,fencing_token=fencing_token+1 "
                "WHERE run_id=? AND kind='host' AND handoff_id=?",
                (run_id, handoff["id"]),
            )
            self.connection.execute(
                "UPDATE runs SET state='cancelled',phase=NULL,version=?,updated_at=? WHERE id=?",
                (version, timestamp, run_id),
            )
            payload = canonical_json({
                "handoff_id": handoff["id"],
                "receipt": "result-receipt.json",
                "terminal_reports": prepared is not None,
            })
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'run.cancelled',?,?)",
                (run_id, version, payload, timestamp),
            )
            self.connection.execute("COMMIT")
            return version
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    @_project_terminal
    def fail_queued_budget(
        self,
        run_id: str,
        expected_version: int,
        error: dict[str, Any],
        terminal_artifacts: list[dict[str, Any]],
    ) -> int:
        """Fail a paused workflow before another worker launch can consume budget."""
        prepared = self._prepare_exact_artifacts(
            run_id, terminal_artifacts, BRANCH_REVIEW_TERMINAL_ARTIFACTS,
        )
        encoded_error = canonical_json(error)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute(
                "SELECT state,phase,version FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            if (not run or run["state"] not in {"queued", "awaiting_host"}
                    or run["phase"] is not None
                    or run["version"] != expected_version):
                raise ConflictError("budget exhaustion is no longer current")
            if self.connection.execute(
                "SELECT 1 FROM supervisor_claims WHERE run_id=? AND active=1",
                (run_id,),
            ).fetchone():
                raise ConflictError("budget exhaustion raced with a supervisor")
            version, _ = self._reference_prepared_artifacts(
                run_id, run["version"], prepared,
            )
            now, version = _utc_now(), version + 1
            self.connection.execute(
                "UPDATE handoffs SET status='consumed',closed_at=? WHERE run_id=? "
                "AND status IN ('open','submitted')",
                (now, run_id),
            )
            self.connection.execute(
                "UPDATE claims SET active=0 WHERE run_id=?", (run_id,),
            )
            self.connection.execute(
                "UPDATE runs SET state='failed',phase=NULL,version=?,updated_at=? "
                "WHERE id=?",
                (version, now, run_id),
            )
            payload = dict(error)
            payload["receipt"] = "result-receipt.json"
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'run.failed',?,?)",
                (run_id, version, canonical_json(payload), now),
            )
            self.connection.execute("COMMIT")
            return version
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    @_project_terminal
    def cancel_council_queued(self, run_id: str, expected_version: int, artifacts: list) -> int:
        prepared = self._prepare_exact_artifacts(run_id, artifacts, BRANCH_REVIEW_TERMINAL_ARTIFACTS)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.run(run_id)
            snapshot = json.loads(run["mutable_snapshot"])
            if (run["version"] != expected_version or run["state"] != "queued" or run["phase"] is not None
                    or snapshot["task"]["workflow"] != "council-decision"):
                raise ConflictError("Council queued cancellation is fenced")
            if self.connection.execute("SELECT 1 FROM supervisor_claims WHERE run_id=? AND active=1", (run_id,)).fetchone():
                raise ConflictError("Council queued cancellation raced with an owned launcher")
            version, _ = self._reference_prepared_artifacts(run_id, run["version"], prepared)
            now, version = _utc_now(), version + 1
            self.connection.execute("UPDATE attempts SET status='recovery_required',finished_at=? WHERE run_id=? AND status='reserved'", (now, run_id))
            self.connection.execute("UPDATE supervisor_claims SET active=0 WHERE run_id=?", (run_id,))
            self.connection.execute("UPDATE claims SET active=0,fencing_token=fencing_token+1 WHERE run_id=?", (run_id,))
            self.connection.execute("UPDATE runs SET state='cancelled',phase=NULL,version=?,updated_at=? WHERE id=?", (version, now, run_id))
            self.connection.execute("INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,'run.cancelled',?,?)",
                (run_id, version, canonical_json({"receipt": "result-receipt.json", "workflow": "council-decision"}), now))
            self.connection.execute("COMMIT")
            return version
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    @_project_terminal
    def cancel_queued(self, run_id: str) -> int:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT state,phase,version FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise ContractError("run does not exist")
            if row["state"] in TERMINAL_STATES:
                self.connection.execute("COMMIT"); return row["version"]
            if row["state"] != "queued" or row["phase"] is not None:
                raise ConflictError("queued run is owned by another operation")
            path,digest,size,now=self._terminal_receipt(run_id,"cancelled","queued",None)
            version=self._reference_terminal_receipt(run_id,row["version"],path,digest,size,now)+1
            self.connection.execute("UPDATE runs SET state='cancelled',version=?,updated_at=? WHERE id=?", (version, now, run_id))
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'run.cancelled',?,?)",
                (run_id,version,canonical_json({"receipt":"result-receipt.json"}),now),
            )
            self.connection.execute("COMMIT"); return version
        except Exception:
            self.connection.execute("ROLLBACK"); raise

    @_project_terminal
    def cancel_launching(self, run_id: str) -> int:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row=self.connection.execute("SELECT state,phase,version FROM runs WHERE id=?",(run_id,)).fetchone()
            if not row or row["state"]!="queued" or row["phase"]!="launching": raise ConflictError("run is not launching")
            path,digest,size,now=self._terminal_receipt(run_id,"cancelled","launching",None)
            version=self._reference_terminal_receipt(run_id,row["version"],path,digest,size,now)+1
            self.connection.execute("UPDATE attempts SET status='recovery_required',finished_at=? WHERE run_id=? AND status='reserved'",(now,run_id))
            self.connection.execute("UPDATE supervisor_claims SET active=0 WHERE run_id=?",(run_id,))
            self.connection.execute("UPDATE runs SET state='cancelled',phase=NULL,version=?,updated_at=? WHERE id=?",(version,now,run_id))
            self.connection.execute(
                "INSERT INTO events(run_id,run_version,type,payload,created_at) "
                "VALUES(?,?,'run.cancelled',?,?)",
                (run_id,version,canonical_json({"receipt":"result-receipt.json"}),now),
            )
            self.connection.execute("COMMIT"); return version
        except Exception: self.connection.execute("ROLLBACK"); raise

    def events_page(self, run_id: str, after: int = 0, limit: int = 100) -> dict[str, Any]:
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ContractError("event cursor/limit is invalid")
        if not self.connection.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
            raise ContractError("run does not exist")
        rows = self.connection.execute("SELECT id,run_version,type,payload,created_at FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT ?", (run_id, after, limit + 1)).fetchall()
        page, more = rows[:limit], len(rows) > limit
        consumed = page[-1]["id"] if page else after
        return {"events": [{**dict(row), "payload": json.loads(row["payload"])} for row in page], "next_cursor": consumed, "has_more": more}

    def events_for_run(self, run_id: str) -> list[dict[str, Any]]:
        if not self.connection.execute(
            "SELECT 1 FROM runs WHERE id=?", (run_id,),
        ).fetchone():
            raise ContractError("run does not exist")
        rows = self.connection.execute(
            "SELECT id,run_version,type,payload,created_at FROM events "
            "WHERE run_id=? ORDER BY id",
            (run_id,),
        ).fetchall()
        events = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ConflictError("persisted event payload is invalid") from exc
            if canonical_json(payload) != row["payload"]:
                raise ConflictError("persisted event payload is not canonical")
            events.append({**dict(row), "payload": payload})
        return events

    def status_snapshot(
        self, run_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, HandoffSnapshot | None]:
        self.connection.execute("BEGIN")
        try:
            run = self.connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                raise ContractError("run does not exist")
            attempt = self.connection.execute("SELECT * FROM attempts WHERE run_id=? ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
            handoff = self.connection.execute(
                "SELECT * FROM handoffs WHERE run_id=? ORDER BY sequence DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            claim_row = None
            if handoff:
                claim_row = self.connection.execute(
                    "SELECT run_id,handoff_id,owner_id,fencing_token,lease_expires_at,"
                    "? AS run_version FROM claims WHERE run_id=? AND kind='host' "
                    "AND handoff_id=? AND active=1",
                    (run["version"], run_id, handoff["id"]),
                ).fetchone()
            handoff_snapshot = self._handoff_snapshot_from_rows(
                run_id, run, handoff, claim_row,
            )
            self.connection.execute("COMMIT")
            return dict(run), dict(attempt) if attempt else None, handoff_snapshot
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def result_snapshot(self, run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        self.connection.execute("BEGIN")
        try:
            run=self.connection.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone()
            if not run: raise ContractError("run does not exist")
            artifacts=self.connection.execute("SELECT id,name,path,sha256,byte_size,created_at FROM artifacts WHERE run_id=? ORDER BY created_at,id",(run_id,)).fetchall()
            self.connection.execute("COMMIT"); return dict(run),[dict(row) for row in artifacts]
        except Exception: self.connection.execute("ROLLBACK"); raise

    def artifacts_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute("SELECT id,name,path,sha256,byte_size,created_at FROM artifacts WHERE run_id=? ORDER BY created_at,id", (run_id,))]

    def artifact_named(self, run_id: str, name: str) -> dict[str, Any] | None:
        row=self.connection.execute("SELECT id,name,path,sha256,byte_size,created_at FROM artifacts WHERE run_id=? AND name=?",(run_id,name)).fetchone()
        return dict(row) if row else None

    def attempt(self, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM attempts WHERE run_id=? ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
        return dict(row) if row else None

    def attempts_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return [
            dict(row) for row in self.connection.execute(
                "SELECT * FROM attempts WHERE run_id=? ORDER BY created_at,id",
                (run_id,),
            )
        ]

    def run(self, run_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row: raise ContractError("run does not exist")
        return dict(row)
