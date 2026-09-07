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

SUPPORTED_SCHEMA_VERSION = 1
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
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.migrate()
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")

    def close(self) -> None:
        self.connection.close()

    def migrate(self) -> None:
        self.connection.execute("BEGIN EXCLUSIVE")
        try:
            table = self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
            current = self.connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0] if table else 0
            if current > SUPPORTED_SCHEMA_VERSION:
                raise SchemaVersionError(f"database schema {current} is newer than supported {SUPPORTED_SCHEMA_VERSION}")
            if current == 0:
                sql = files("devsquad.migrations").joinpath("001_initial.sql").read_text()
                for statement in sql.split(";"):
                    if statement.strip():
                        self.connection.execute(statement)
                self.connection.execute("INSERT INTO schema_migrations(version, applied_at) VALUES(1, ?)", (_utc_now(),))
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
                "INSERT INTO runs(id,project_id,idempotency_key,request_hash,submitted_request,state,phase,version,created_at,updated_at) VALUES(?,?,?,?,?,'queued','preparing',1,?,?)",
                (run_id, project_id, idempotency_key, digest, encoded, now, now),
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

    def complete_preparation(self, run_id: str, fencing_token: int, mutable_snapshot: Any) -> int:
        snapshot = canonical_json(mutable_snapshot)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT r.state,r.phase,r.version,c.fencing_token,c.active FROM runs r JOIN claims c ON c.run_id=r.id WHERE r.id=?",
                (run_id,),
            ).fetchone()
            if not row or row["state"] != "queued" or row["phase"] != "preparing" or not row["active"] or row["fencing_token"] != fencing_token:
                raise ConflictError("preparation claim is stale or cancelled")
            version, now = row["version"] + 1, _utc_now()
            self.connection.execute("UPDATE runs SET mutable_snapshot=?,phase=NULL,version=?,updated_at=? WHERE id=?", (snapshot, version, now, run_id))
            self.connection.execute("UPDATE claims SET active=0 WHERE run_id=?", (run_id,))
            self.connection.execute("INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,'run.queued','{}',?)", (run_id, version, now))
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
            version, now = row["version"] + 1, _utc_now()
            self.connection.execute("UPDATE runs SET state='cancelled',phase=NULL,version=?,updated_at=? WHERE id=?", (version, now, run_id))
            self.connection.execute("UPDATE claims SET active=0,fencing_token=fencing_token+1 WHERE run_id=?", (run_id,))
            self.connection.execute("INSERT INTO events(run_id,run_version,type,payload,created_at) VALUES(?,?,'run.cancelled','{}',?)", (run_id, version, now))
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
        destination = directory / f"{digest}.blob"
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

    def run(self, run_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row: raise ContractError("run does not exist")
        return dict(row)
