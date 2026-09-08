"""Durable M2 application operations shared by CLI and later MCP surfaces."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from .contracts import ContractError
from .store import ConflictError, Store, TERMINAL_STATES, canonical_json
from .validation import validate_task


class Service:
    def __init__(self, runtime: Path):
        self.runtime = runtime.resolve()
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.database = self.runtime / "state.sqlite3"
        self.artifacts = self.runtime / "artifacts"

    def _store(self) -> Store:
        return Store(self.database, self.artifacts)

    def _freeze_package(self) -> tuple[Path, str]:
        source = Path(__file__).resolve().parent
        digest = hashlib.sha256()
        members = sorted(p for p in source.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix in {".py", ".sql"})
        for path in members:
            relative = path.relative_to(source)
            digest.update(str(relative).encode() + b"\0" + path.read_bytes())
        value = digest.hexdigest()
        destination = self.runtime / "packages" / value / "devsquad"
        if not destination.exists():
            destination.parent.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix=f".{value}.", dir=destination.parent.parent))
            try:
                shutil.copytree(source, temporary / "devsquad", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(temporary / "devsquad", destination)
            finally:
                shutil.rmtree(temporary, ignore_errors=True)
        return destination.parent, value

    @staticmethod
    def _resolve_snapshot(task: dict[str, Any], internal_delay: float | None) -> dict[str, Any]:
        repo = Path(task["project"]["repo_path"]).resolve(strict=True)
        def oid(ref: str) -> str:
            result = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", f"{ref}^{{commit}}"], text=True, capture_output=True, check=False)
            if result.returncode != 0: raise ContractError(f"Git ref does not resolve to a commit: {ref}")
            return result.stdout.strip()
        configs = {}
        for label in ("profiles_file", "policy_file"):
            candidate = Path(task["routing"][label])
            path = (repo / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
            if path != repo and repo not in path.parents: raise ContractError(f"{label} escapes project")
            data = path.read_bytes(); configs[label] = {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}
        snapshot = {"task": task, "base_oid": oid(task["project"]["base_ref"]), "target_oid": oid(task["project"]["target_ref"]), "configs": configs}
        if internal_delay is not None:
            if internal_delay < 0 or internal_delay > 60: raise ContractError("internal fake delay is invalid")
            snapshot["internal_fake_delay"] = internal_delay
        return snapshot

    def start(self, task: dict[str, Any], idempotency_key: str, supersedes_run_id: str | None = None, *, _internal_fake_delay: float | None = None) -> dict[str, Any]:
        validate_task(task, require_existing_repo=True)
        submitted = {"task": task, "supersedes_run_id": supersedes_run_id}
        store = self._store()
        try:
            claim = store.claim_start(Path(task["project"]["repo_path"]), idempotency_key, submitted, f"preflight:{os.getpid()}")
            if not claim.created:
                return {"run_id": claim.run_id, "state": store.run(claim.run_id)["state"], "created": False}
            snapshot = self._resolve_snapshot(task, _internal_fake_delay)
            version = store.complete_preparation(claim.run_id, claim.fencing_token or 0, snapshot)
        finally:
            store.close()
        package, digest = self._freeze_package()
        self._spawn_daemon(claim.run_id, version, package, digest)
        return {"run_id": claim.run_id, "state": "queued", "created": True}

    def _spawn_daemon(self, run_id: str, expected_version: int, package: Path, digest: str) -> int:
        command = [sys.executable, "-m", "devsquad.detached", "--database", str(self.database), "--artifacts", str(self.artifacts), "--run-id", run_id, "--expected-version", str(expected_version), "--package-digest", digest]
        environment = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(package)}
        process = subprocess.Popen(command, cwd=self.runtime, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        return process.pid

    def status(self, run_id: str) -> dict[str, Any]:
        store = self._store()
        try:
            run = store.run(run_id); attempt = store.active_attempt(run_id)
            return {"run_id": run_id, "state": run["state"], "phase": run["phase"], "version": run["version"], "active_attempt": {k: attempt.get(k) for k in ("id","status","pid","pgid","heartbeat_at")} if attempt else None, "next_action": "recovery_file_required" if run["state"] == "blocked" else None}
        finally: store.close()

    def events(self, run_id: str, after: int = 0, limit: int = 100) -> dict[str, Any]:
        store = self._store()
        try: return store.events_page(run_id, after, limit)
        finally: store.close()

    def result(self, run_id: str) -> dict[str, Any]:
        store = self._store()
        try:
            run = store.run(run_id)
            return {"run_id": run_id, "ready": run["state"] in TERMINAL_STATES, "state": run["state"], "artifacts": store.artifacts_for_run(run_id) if run["state"] in TERMINAL_STATES else []}
        finally: store.close()

    def cancel(self, run_id: str) -> dict[str, Any]:
        store = self._store()
        try:
            run = store.run(run_id)
            if run["state"] == "queued" and run["phase"] is None: version = store.cancel_queued(run_id)
            elif run["state"] in {"running", "cancelling"}: version, _ = store.request_cancel(run_id)
            elif run["state"] in TERMINAL_STATES: version = run["version"]
            else: raise ConflictError("run requires recovery before cancellation")
            return {"run_id": run_id, "state": store.run(run_id)["state"], "version": version}
        finally: store.close()

    def resume(self, run_id: str, recovery: dict[str, Any] | None = None) -> dict[str, Any]:
        store = self._store()
        try:
            run = store.run(run_id)
            if run["state"] in TERMINAL_STATES: raise ConflictError("terminal run cannot resume; start a superseding run")
            if run["state"] == "running":
                from .supervisor import Supervisor
                disposition = Supervisor(store).recover(run_id)
                return {"run_id": run_id, "disposition": disposition, "launched": False}
            if run["state"] == "queued" and run["phase"] is None:
                version = run["version"]
            else:
                raise ConflictError("run requires an explicit recovery disposition")
        finally: store.close()
        package, digest = self._freeze_package(); self._spawn_daemon(run_id, version, package, digest)
        return {"run_id": run_id, "disposition": "continued", "launched": True}
