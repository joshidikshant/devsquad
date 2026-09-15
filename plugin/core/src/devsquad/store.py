"""Transactional SQLite ledger and atomic artifact storage for M2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import uuid
from typing import Any

from .contracts import ContractError

SUPPORTED_SCHEMA_VERSION = 4
TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


class Store:
    def __init__(self, database: Path, artifacts: Path):
        self.database = database
        self.artifacts = artifacts
        database.parent.mkdir(parents=True, exist_ok=True)
        artifacts.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database, timeout=10, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
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
            if current > SUPPORTED_SCHEMA_VERSION:
                raise SchemaVersionError(f"database schema {current} is newer than supported {SUPPORTED_SCHEMA_VERSION}")
            while current < SUPPORTED_SCHEMA_VERSION:
                next_version = current + 1
                candidates = [entry for entry in files("devsquad.migrations").iterdir() if entry.name.startswith(f"{next_version:03d}_") and entry.name.endswith(".sql")]
                if len(candidates) != 1:
                    raise SchemaVersionError(f"migration {next_version} is missing or ambiguous")
                sql = candidates[0].read_text()
                for statement in sql.split(";"):
                    if statement.strip():
                        self.connection.execute(statement)
                self.connection.execute("INSERT INTO schema_migrations(version, applied_at) VALUES(?, ?)", (next_version, _utc_now()))
                current = next_version
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

    def claim_start(self, worktree: Path, idempotency_key: str, submitted_request: Any, owner_id: str) -> StartClaim:
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

    def _terminal_receipt(self, run_id: str, terminal_state: str, phase: str, payload: Any) -> tuple[Path, str, int, str]:
        finished_at = _utc_now()
        receipt = {
            "schema_version": 1,
            "run_id": run_id,
            "state": terminal_state,
            "phase": phase,
            "attempt_id": None,
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

    def complete_preparation(self, run_id: str, fencing_token: int, mutable_snapshot: Any, *, package_path: str | None = None, package_digest: str | None = None, supersedes_run_id: str | None = None) -> int:
        snapshot = canonical_json(mutable_snapshot)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT r.state,r.phase,r.version,c.fencing_token,c.active FROM runs r JOIN claims c ON c.run_id=r.id WHERE r.id=?",
                (run_id,),
            ).fetchone()
            if not row or row["state"] != "queued" or row["phase"] != "preparing" or not row["active"] or row["fencing_token"] != fencing_token:
                raise ConflictError("preparation claim is stale or cancelled")
            if supersedes_run_id is not None:
                predecessor = self.connection.execute("SELECT project_id,state FROM runs WHERE id=?", (supersedes_run_id,)).fetchone()
                project = self.connection.execute("SELECT project_id FROM runs WHERE id=?", (run_id,)).fetchone()
                if not predecessor or predecessor["project_id"] != project["project_id"] or predecessor["state"] not in TERMINAL_STATES:
                    raise ConflictError("superseded run must be terminal and belong to the same project")
            version, now = row["version"] + 1, _utc_now()
            self.connection.execute("UPDATE runs SET mutable_snapshot=?,package_path=?,package_digest=?,supersedes_run_id=?,phase=NULL,version=?,updated_at=? WHERE id=?", (snapshot, package_path, package_digest, supersedes_run_id, version, now, run_id))
            self.connection.execute("UPDATE claims SET active=0 WHERE run_id=?", (run_id,))
            self.connection.execute("INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,'run.queued','{}',?)", (run_id, version, now))
            self.connection.execute("COMMIT")
            return version
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def fail_preparation(
        self,
        run_id: str,
        fencing_token: int,
        error: Any,
        *,
        mutable_snapshot: Any | None = None,
        supersedes_run_id: str | None = None,
    ) -> int:
        encoded = canonical_json(error)
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
            path, digest, size, now = self._terminal_receipt(run_id, "failed", "preparing", error)
            version = self._reference_terminal_receipt(
                run_id, row["version"], path, digest, size, now,
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

    def reserve_attempt(self, run_id: str, expected_version: int, owner_id: str, package_digest: str) -> AttemptReservation:
        if not owner_id or not package_digest:
            raise ContractError("supervisor owner and package digest are required")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute("SELECT project_id,worktree_path,state,phase,version FROM runs WHERE id=?", (run_id,)).fetchone()
            if not run or run["version"] != expected_version or run["state"] != "queued" or run["phase"] is not None:
                raise ConflictError("run is not available for supervisor claim")
            active = self.connection.execute("SELECT 1 FROM supervisor_claims WHERE run_id=? AND active=1", (run_id,)).fetchone()
            if active:
                raise ConflictError("run already has a supervisor claim")
            if not run["worktree_path"]:
                raise ContractError("run has no canonical worktree identity")
            old = self.connection.execute("SELECT COALESCE(MAX(fencing_token),0) FROM supervisor_claims WHERE run_id=?", (run_id,)).fetchone()[0]
            supervisor_token, attempt_id, attempt_token = old + 1, str(uuid.uuid4()), uuid.uuid4().hex
            now, version = _utc_now(), expected_version + 1
            self.connection.execute("INSERT OR REPLACE INTO supervisor_claims(run_id,owner_id,fencing_token,package_digest,heartbeat_at,active) VALUES(?,?,?,?,?,1)", (run_id, owner_id, supervisor_token, package_digest, now))
            self.connection.execute("INSERT INTO attempts(id,run_id,project_id,worktree_path,attempt_token,status,heartbeat_at,package_digest,created_at) VALUES(?,?,?,?,?,'reserved',?,?,?)", (attempt_id, run_id, run["project_id"], run["worktree_path"], attempt_token, now, package_digest, now))
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

    def commit_durable_import(self, run_id: str, attempt_token: str, artifacts: list[dict[str, Any]], metadata: Any, terminal_state: str, payload: Any) -> str:
        """Atomically import one durable receipt, or observe its prior import.

        Content-addressed files are finalized before this call. All database
        references, output projection fields, and terminal state then cross a
        single write fence so competing recovery processes cannot partially
        import or downgrade a valid completion.
        """
        if terminal_state not in TERMINAL_STATES:
            raise ContractError("invalid terminal state")
        expected_parent = (self.artifacts / run_id).resolve()
        prepared = []
        names = set()
        for artifact in artifacts:
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
        stdout_name = next((name for name in names if name.endswith(".stdout")), None)
        stderr_name = next((name for name in names if name.endswith(".stderr")), None)
        if stdout_name is None or stderr_name is None or "result-receipt.json" not in names:
            raise ContractError("durable import requires stdout, stderr, and result receipt artifacts")
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

            version = run["version"]
            artifact_ids = {}
            for name, path, digest, size in prepared:
                existing = self.connection.execute(
                    "SELECT id,path,sha256,byte_size FROM artifacts WHERE run_id=? AND name=?", (run_id, name),
                ).fetchone()
                if existing:
                    if existing["path"] != path or existing["sha256"] != digest or existing["byte_size"] != size:
                        raise ConflictError("durable artifact conflicts with an existing reference")
                    artifact_ids[name] = existing["id"]
                    continue
                artifact_id, now = str(uuid.uuid4()), _utc_now()
                self.connection.execute(
                    "INSERT INTO artifacts(id,run_id,name,path,sha256,byte_size,created_at) VALUES(?,?,?,?,?,?,?)",
                    (artifact_id, run_id, name, path, digest, size, now),
                )
                artifact_ids[name] = artifact_id
                version += 1
                event = canonical_json({"artifact_id": artifact_id, "name": name, "sha256": digest, "byte_size": size})
                self.connection.execute(
                    "INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,'artifact.recorded',?,?)",
                    (run_id, version, event, now),
                )
                self.connection.execute(
                    "UPDATE runs SET version=?,updated_at=? WHERE id=?", (version, now, run_id),
                )

            stdout_id, stderr_id = artifact_ids[stdout_name], artifact_ids[stderr_name]
            if attempt["output_metadata"] is None:
                now = _utc_now(); version += 1
                self.connection.execute(
                    "UPDATE attempts SET stdout_artifact_id=?,stderr_artifact_id=?,output_metadata=? WHERE id=?",
                    (stdout_id, stderr_id, encoded_metadata, attempt["id"]),
                )
                self.connection.execute(
                    "INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,'attempt.output',?,?)",
                    (run_id, version, encoded_metadata, now),
                )
                self.connection.execute(
                    "UPDATE runs SET version=?,updated_at=? WHERE id=?", (version, now, run_id),
                )
            elif (attempt["stdout_artifact_id"] != stdout_id
                    or attempt["stderr_artifact_id"] != stderr_id
                    or attempt["output_metadata"] != encoded_metadata):
                raise ConflictError("durable output conflicts with its prior import")

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

    def active_attempt(self, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM attempts WHERE run_id=? AND status IN ('reserved','running','cancelling','ownership_ambiguous') ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
        return dict(row) if row else None

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

    def status_snapshot(self, run_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        self.connection.execute("BEGIN")
        try:
            run = self.connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                raise ContractError("run does not exist")
            attempt = self.connection.execute("SELECT * FROM attempts WHERE run_id=? ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
            self.connection.execute("COMMIT")
            return dict(run), dict(attempt) if attempt else None
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

    def run(self, run_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row: raise ContractError("run does not exist")
        return dict(row)
