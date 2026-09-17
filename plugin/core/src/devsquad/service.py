"""Durable M2 application operations shared by CLI and later MCP surfaces."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Any

from .contracts import ContractError
from .reports import build_terminal_reports
from .router import load_routing
from .store import (
    ConflictError,
    HandoffClaim,
    HandoffSnapshot,
    Store,
    TERMINAL_STATES,
    canonical_json,
)
from .validation import validate_task
from .workflows import (
    apply_lead_disposition,
    review_mode,
    validate_branch_review_handoff,
    validate_handoff_decision_evidence,
    validate_review_document,
)
from .workspaces import (
    assert_clean_inputs,
    committed_regular_file,
    prepare_check_workspace,
    prepare_review_workspace,
    repo_relative_config,
    resolve_commit,
)


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
        members = sorted(p for p in source.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
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
                for copied in (temporary/"devsquad").rglob("*"):
                    if copied.is_file():
                        with copied.open("rb") as stream: os.fsync(stream.fileno())
                try:
                    os.rename(temporary, destination.parent)
                except OSError:
                    if not destination.exists(): raise
            finally:
                shutil.rmtree(temporary, ignore_errors=True)
        if self._package_digest(destination.parent)!=value:
            raise ConflictError("frozen package cache is corrupt")
        for directory_path in (destination,destination.parent,destination.parent.parent):
            descriptor=os.open(directory_path,os.O_RDONLY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
        return destination.parent, value

    @staticmethod
    def _package_digest(package: Path) -> str:
        source=package/"devsquad"; digest=hashlib.sha256()
        for path in sorted(p for p in source.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"):
            relative=path.relative_to(source); digest.update(str(relative).encode()+b"\0"+path.read_bytes())
        return digest.hexdigest()

    def _verified_package(self, run: dict[str, Any]) -> tuple[Path,str]:
        if not run.get("package_path") or not run.get("package_digest"):
            raise ConflictError("run has no pinned package")
        package=Path(run["package_path"]).resolve()
        expected_root=(self.runtime/"packages").resolve()
        if expected_root not in package.parents or self._package_digest(package)!=run["package_digest"]:
            raise ConflictError("pinned package is missing or corrupt")
        return package,run["package_digest"]

    @staticmethod
    def _claim_payload(claim: HandoffClaim) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "run_id": claim.run_id,
            "handoff_id": claim.handoff_id,
            "owner": claim.owner_id,
            "fencing_token": claim.fencing_token,
            "expires_at": claim.expires_at,
            "run_version": claim.run_version,
        }

    @staticmethod
    def _decode_claim(value: dict[str, Any]) -> HandoffClaim:
        fields = {
            "schema_version", "run_id", "handoff_id", "owner",
            "fencing_token", "expires_at", "run_version",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ContractError("handoff claim fields are invalid")
        if value["schema_version"] != 1 or type(value["schema_version"]) is not int:
            raise ContractError("handoff claim schema_version is invalid")
        for field in ("run_id", "handoff_id", "owner", "expires_at"):
            if not isinstance(value[field], str) or not value[field]:
                raise ContractError(f"handoff claim {field} is invalid")
        for field in ("fencing_token", "run_version"):
            if type(value[field]) is not int or value[field] < 1:
                raise ContractError(f"handoff claim {field} is invalid")
        try:
            expires_at = datetime.fromisoformat(value["expires_at"])
        except ValueError as exc:
            raise ContractError("handoff claim expires_at is invalid") from exc
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ContractError("handoff claim expires_at is invalid")
        return HandoffClaim(
            run_id=value["run_id"],
            handoff_id=value["handoff_id"],
            owner_id=value["owner"],
            fencing_token=value["fencing_token"],
            expires_at=value["expires_at"],
            run_version=value["run_version"],
            action="presented",
        )

    @staticmethod
    def _handoff_payload(snapshot: HandoffSnapshot, *, include_packet: bool) -> dict[str, Any]:
        payload = {
            "handoff_id": snapshot.handoff_id,
            "sequence": snapshot.sequence,
            "status": snapshot.status,
            "packet_sha256": snapshot.packet_sha256,
            "created_run_version": snapshot.created_run_version,
            "submitted_run_version": snapshot.submitted_run_version,
        }
        if include_packet:
            payload["packet"] = snapshot.packet
        if snapshot.claim is not None:
            payload["claimed_by"] = snapshot.claim.owner_id
            payload["claim_expires_at"] = snapshot.claim.expires_at
        else:
            payload["claimed_by"] = None
            payload["claim_expires_at"] = None
        return payload

    def _resolve_snapshot(
        self,
        task: dict[str, Any],
        internal_delay: float | None,
        resolved_repo: Path | None = None,
        *,
        project_id: str | None = None,
        run_id: str | None = None,
        internal_review_fixture: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        repo = resolved_repo or Path(task["project"]["repo_path"]).resolve(strict=True)
        base_oid = resolve_commit(repo, task["project"]["base_ref"])
        target_oid = resolve_commit(repo, task["project"]["target_ref"])
        scope_paths = tuple(dict.fromkeys(
            task["scope"]["read_paths"] + task["scope"]["write_paths"]
        ))
        config_paths = {
            label: repo_relative_config(repo, task["routing"][label], label)
            for label in ("profiles_file", "policy_file")
        }
        if internal_delay is None:
            assert_clean_inputs(repo, scope_paths, config_paths.values())
        configs = {}
        config_payloads = {}
        for label, relative_path in config_paths.items():
            path = repo / relative_path
            data = (
                committed_regular_file(repo, target_oid, relative_path)
                if internal_delay is None else path.read_bytes()
            )
            config_payloads[label] = data
            configs[label] = {
                "path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
            }
        snapshot = {
            "task": task,
            "base_oid": base_oid,
            "target_oid": target_oid,
            "configs": configs,
        }
        if internal_delay is not None:
            if internal_delay < 0 or internal_delay > 60: raise ContractError("internal fake delay is invalid")
            snapshot["internal_fake_delay"] = internal_delay
        else:
            snapshot["routing"] = load_routing(
                task,
                config_payloads["profiles_file"],
                config_payloads["policy_file"],
            )
            if project_id is None or run_id is None:
                raise ContractError("public preflight requires run-owned workspace identity")
            snapshot["workspace"] = prepare_review_workspace(
                repo,
                self.runtime,
                project_id,
                run_id,
                base_oid,
                target_oid,
                scope_paths,
                required_clean_paths=config_paths.values(),
            )
            snapshot["check_workspace"] = prepare_check_workspace(
                repo,
                self.runtime,
                project_id,
                run_id,
                target_oid,
                scope_paths,
                required_clean_paths=config_paths.values(),
            )
            if internal_review_fixture is not None:
                if (not isinstance(internal_review_fixture, dict)
                        or set(internal_review_fixture)
                        != {"verdict", "summary", "findings"}):
                    raise ContractError("internal review fixture fields are invalid")
                fixture_document = {
                    "schema_version": 1,
                    "candidate_sha256": snapshot["workspace"]["candidate_sha256"],
                    "base_oid": base_oid,
                    "target_oid": target_oid,
                    "review_mode": review_mode(task),
                    **internal_review_fixture,
                }
                snapshot["internal_review_fixture"] = validate_review_document(
                    fixture_document, task, snapshot["workspace"],
                )
        return snapshot

    def _continue_preparation(
        self,
        store: Store,
        run_id: str,
        fencing_token: int,
        submitted: dict[str, Any],
    ) -> tuple[tuple[int, Path, str] | None, dict[str, Any] | None]:
        snapshot = None
        supersedes_run_id = submitted.get("supersedes_run_id")
        validated_supersedes_run_id = None
        try:
            task = submitted["task"]
            internal_delay = submitted.get("_internal_fake_delay")
            internal_review_fixture = submitted.get("_internal_review_fixture")
            store.validate_predecessor(run_id, fencing_token, supersedes_run_id)
            validated_supersedes_run_id = supersedes_run_id
            validate_task(task, require_existing_repo=True)
            worktree = store.preparation_worktree(
                run_id, fencing_token, Path(task["project"]["repo_path"]),
            )
            project_id = store.run(run_id)["project_id"]
            snapshot = self._resolve_snapshot(
                task,
                internal_delay,
                worktree,
                project_id=project_id,
                run_id=run_id,
                internal_review_fixture=internal_review_fixture,
            )
            if internal_delay is None and internal_review_fixture is None:
                error = {
                    "error": "CAPABILITY_UNAVAILABLE",
                    "message": "branch-review workflow is introduced in M3",
                }
                store.fail_preparation(
                    run_id,
                    fencing_token,
                    error,
                    mutable_snapshot=snapshot,
                    supersedes_run_id=validated_supersedes_run_id,
                )
                return None, error
            package, digest = self._freeze_package()
            version = store.complete_preparation(
                run_id,
                fencing_token,
                snapshot,
                package_path=str(package),
                package_digest=digest,
                supersedes_run_id=supersedes_run_id,
                worktree_path=(
                    snapshot["workspace"]["path"]
                    if internal_review_fixture is not None else None
                ),
            )
            return (version, package, digest), None
        except Exception as exc:
            error = {"error": "PREPARATION_FAILED", "message": str(exc)}
            try:
                # Preserve validated lineage through unrelated failures; a
                # rejected predecessor remains only in submitted_request.
                store.fail_preparation(
                    run_id,
                    fencing_token,
                    error,
                    mutable_snapshot=snapshot,
                    supersedes_run_id=validated_supersedes_run_id,
                )
            except ConflictError:
                # Cancellation or another recovery owner may have fenced us.
                raise exc
            return None, error

    def start(
        self,
        task: dict[str, Any],
        idempotency_key: str,
        supersedes_run_id: str | None = None,
        *,
        _internal_fake_delay: float | None = None,
        _internal_review_fixture: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate_task(task, require_existing_repo=True)
        if _internal_fake_delay is not None and _internal_review_fixture is not None:
            raise ContractError("internal lifecycle fixtures are mutually exclusive")
        submitted = {"task": task, "supersedes_run_id": supersedes_run_id}
        if _internal_fake_delay is not None:
            submitted["_internal_fake_delay"] = _internal_fake_delay
        if _internal_review_fixture is not None:
            submitted["_internal_review_fixture"] = _internal_review_fixture
        store = self._store()
        try:
            claim = store.claim_start(Path(task["project"]["repo_path"]), idempotency_key, submitted, f"preflight:{os.getpid()}")
            if not claim.created:
                return {"run_id": claim.run_id, "state": store.run(claim.run_id)["state"], "created": False}
            launch, error = self._continue_preparation(
                store, claim.run_id, claim.fencing_token or 0, submitted,
            )
        finally:
            store.close()
        if launch is None:
            return {"run_id": claim.run_id, "state": "failed", "created": True, "error": error}
        version, package, digest = launch
        self._spawn_daemon(claim.run_id, version, package, digest)
        return {"run_id": claim.run_id, "state": "queued", "created": True}

    def _spawn_daemon(self, run_id: str, expected_version: int, package: Path, digest: str) -> int:
        command = [sys.executable, "-P", "-m", "devsquad.detached", "--database", str(self.database), "--artifacts", str(self.artifacts), "--run-id", run_id, "--expected-version", str(expected_version), "--package-digest", digest]
        environment = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(package)}
        log_dir=self.runtime/"private-logs"; log_dir.mkdir(parents=True,exist_ok=True)
        with (log_dir/f"{run_id}.supervisor.log").open("ab",buffering=0) as diagnostic:
            process = subprocess.Popen(command, cwd=self.runtime, env=environment, stdin=subprocess.DEVNULL, stdout=diagnostic, stderr=diagnostic, start_new_session=True, close_fds=True)
        threading.Thread(
            target=process.wait,
            name=f"devsquad-reap-{run_id}",
            daemon=True,
        ).start()
        return process.pid

    def status(self, run_id: str) -> dict[str, Any]:
        store = self._store()
        try:
            run, attempt, handoff = store.status_snapshot(run_id)
            active=attempt if attempt and attempt.get("status") in {"reserved","running","cancelling","ownership_ambiguous"} else None
            if run["state"] == "blocked":
                next_action = "recovery_file_required"
            elif run["state"] == "awaiting_host" and run["phase"] is None:
                next_action = "claim_handoff"
            elif run["state"] == "awaiting_host":
                next_action = "handoff_submission_saved"
            else:
                next_action = None
            return {
                "run_id": run_id,
                "state": run["state"],
                "phase": run["phase"],
                "version": run["version"],
                "active_attempt": {
                    key: active.get(key)
                    for key in ("id", "status", "pid", "pgid", "heartbeat_at")
                } if active else None,
                "handoff": self._handoff_payload(handoff, include_packet=False) if handoff else None,
                "next_action": next_action,
            }
        finally: store.close()

    def events(self, run_id: str, after: int = 0, limit: int = 100) -> dict[str, Any]:
        store = self._store()
        try: return store.events_page(run_id, after, limit)
        finally: store.close()

    def result(self, run_id: str) -> dict[str, Any]:
        store = self._store()
        try:
            run, artifacts = store.result_snapshot(run_id)
            if run["state"] not in TERMINAL_STATES:
                return {"run_id":run_id,"ready":False,"state":run["state"],"artifacts":[]}
            if not any(item["name"]=="result-receipt.json" for item in artifacts):
                raise ConflictError("terminal result has no durable receipt")
            for item in artifacts:
                path=Path(item["path"])
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=item["sha256"]:
                    raise ConflictError("referenced result artifact is missing or corrupt")
            return {"run_id":run_id,"ready":True,"state":run["state"],"artifacts":artifacts}
        finally: store.close()

    def cancel(self, run_id: str) -> dict[str, Any]:
        store = self._store()
        try:
            run = store.run(run_id)
            if run["state"] == "queued" and run["phase"] == "preparing": version = store.cancel_preparing(run_id)
            elif run["state"] == "queued" and run["phase"] == "launching": version = store.cancel_launching(run_id)
            elif run["state"] == "queued" and run["phase"] is None: version = store.cancel_queued(run_id)
            elif run["state"] == "cancelling" and run["phase"] == "recovery_cleanup":
                from .supervisor import Supervisor
                version = Supervisor(store).cancel_orphan(run_id)
            elif run["state"] in {"running", "cancelling"}: version, _ = store.request_cancel(run_id)
            elif run["state"] == "awaiting_host": version = store.cancel_host_wait(run_id)
            elif run["state"] == "blocked":
                from .supervisor import Supervisor
                version = Supervisor(store).cancel_orphan(run_id)
            elif run["state"] in TERMINAL_STATES: version = run["version"]
            else: raise ConflictError("run requires recovery before cancellation")
            return {"run_id": run_id, "state": store.run(run_id)["state"], "version": version}
        finally: store.close()

    def handoff_claim(
        self,
        run_id: str,
        expected_version: int,
        owner: str,
        prior_claim: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if type(expected_version) is not int or expected_version < 1:
            raise ContractError("handoff expected version is invalid")
        if not isinstance(owner, str) or not owner:
            raise ContractError("handoff owner is required")
        decoded = self._decode_claim(prior_claim) if prior_claim is not None else None
        store = self._store()
        try:
            claim = store.claim_handoff(run_id, expected_version, owner, decoded)
            snapshot = store.handoff_snapshot(run_id)
            if snapshot is None:  # Defensive: claim_handoff just verified it.
                raise ConflictError("claimed handoff is missing")
            return {
                "run_id": run_id,
                "state": "awaiting_host",
                "phase": None,
                "version": claim.run_version,
                "action": claim.action,
                "claim": self._claim_payload(claim),
                "handoff": self._handoff_payload(snapshot, include_packet=True),
            }
        finally:
            store.close()

    @staticmethod
    def _review_snapshot(run: dict[str, Any]) -> dict[str, Any]:
        try:
            snapshot = json.loads(run["mutable_snapshot"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ConflictError("frozen branch review snapshot is invalid") from exc
        if (not isinstance(snapshot, dict)
                or canonical_json(snapshot) != run["mutable_snapshot"]):
            raise ConflictError("frozen branch review snapshot is not canonical")
        return snapshot

    @staticmethod
    def _review_gate(
        store: Store,
        run_id: str,
        handoff: HandoffSnapshot,
        snapshot: dict[str, Any],
        decision: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        packet = validate_branch_review_handoff(handoff.packet, snapshot)
        validate_handoff_decision_evidence(decision, packet)
        revisions_used = sum(
            entry["decision"]["disposition"] == "revise"
            for entry in store.branch_review_history(run_id)
            if entry["sequence"] < handoff.sequence
        )
        gate = apply_lead_disposition(
            packet["evaluation"],
            decision.get("disposition"),
            revisions_used=revisions_used,
            max_revisions=snapshot["task"]["budget"]["max_revisions"],
        )
        return packet, gate

    def _terminalize_branch_review(
        self,
        store: Store,
        run_id: str,
        handoff: HandoffSnapshot,
        snapshot: dict[str, Any],
        entry: dict[str, Any],
        terminal_state: str,
        error: dict[str, Any] | None,
    ) -> dict[str, Any]:
        history = store.branch_review_history(run_id)
        evidence_ids = {
            reference["artifact_id"]
            for item in history
            for reference in item["packet"]["artifacts"]
        }
        run_artifacts = store.artifacts_for_run(run_id)
        if evidence_ids - {artifact["id"] for artifact in run_artifacts}:
            raise ConflictError("branch review evidence artifact is missing")
        expected_parent = (self.artifacts / run_id).resolve()
        for artifact in run_artifacts:
            path = Path(artifact["path"])
            if (path.resolve().parent != expected_parent
                    or not path.is_file()
                    or path.stat().st_size != artifact["byte_size"]
                    or hashlib.sha256(path.read_bytes()).hexdigest()
                    != artifact["sha256"]):
                raise ConflictError("branch review artifact is missing or corrupt")
        reports = build_terminal_reports(
            run_id=run_id,
            state=terminal_state,
            snapshot=snapshot,
            history=history,
            run_artifacts=run_artifacts,
            events=store.events_for_run(run_id),
            completed_at=datetime.now(timezone.utc).isoformat(),
            error=error,
        )
        prepared = []
        for name in sorted(reports):
            content = reports[name]
            path, digest, size = store.finalize_artifact(run_id, name, content)
            prepared.append({
                "name": name,
                "path": path,
                "sha256": digest,
                "byte_size": size,
            })
        decision = entry["decision"]
        outcome = store.complete_handoff_terminal(
            run_id,
            handoff.handoff_id,
            decision["submission_id"],
            decision["submission_hash"],
            prepared,
            terminal_state,
            {
                "handoff_id": handoff.handoff_id,
                "submission_id": decision["submission_id"],
                "submission_hash": decision["submission_hash"],
                "disposition": decision["disposition"],
                "receipt": "result-receipt.json",
                "error": error,
            },
        )
        return {
            "action": "terminal",
            "state": outcome["state"],
            "version": outcome["version"],
            "replayed_continuation": outcome["replayed"],
            "launch": None,
        }

    def _continue_branch_review_submission(
        self,
        store: Store,
        run_id: str,
        handoff: HandoffSnapshot,
        snapshot: dict[str, Any],
        entry: dict[str, Any],
    ) -> dict[str, Any]:
        run = store.run(run_id)
        if run["state"] in TERMINAL_STATES:
            return {
                "action": "already_terminal",
                "state": run["state"],
                "version": run["version"],
                "replayed_continuation": True,
                "launch": None,
            }
        _, gate = self._review_gate(
            store, run_id, handoff, snapshot, entry["decision"],
        )
        if gate["action"] == "repeat_review":
            decision = entry["decision"]
            requeue = store.requeue_review_revision(
                run_id,
                handoff.handoff_id,
                decision["submission_id"],
                decision["submission_hash"],
            )
            if requeue["action"] == "requeued":
                queued = store.run(run_id)
                package, digest = self._verified_package(queued)
                return {
                    "action": "requeued",
                    "state": queued["state"],
                    "version": queued["version"],
                    "replayed_continuation": requeue["replayed"],
                    "launch": (queued["version"], package, digest),
                }
            if requeue["action"] == "already_advanced":
                advanced = store.run(run_id)
                return {
                    "action": "already_advanced",
                    "state": advanced["state"],
                    "version": advanced["version"],
                    "replayed_continuation": True,
                    "launch": None,
                }
            gate = {
                **gate,
                "action": "budget_exhausted",
                "terminal_state": "failed",
            }
            error = {
                "error": "BUDGET_EXHAUSTED",
                "message": "branch review worker invocation budget is exhausted",
            }
        elif gate["action"] == "budget_exhausted":
            error = {
                "error": "BUDGET_EXHAUSTED",
                "message": "branch review revision budget is exhausted",
            }
        elif gate["terminal_state"] == "failed":
            error = {
                "error": "REVIEW_REJECTED",
                "message": entry["decision"]["reason"] or "host rejected the review",
            }
        else:
            error = None
        return self._terminalize_branch_review(
            store,
            run_id,
            handoff,
            snapshot,
            entry,
            gate["terminal_state"],
            error,
        )

    def handoff_complete(
        self,
        run_id: str,
        claim: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        decoded = self._decode_claim(claim)
        if decoded.run_id != run_id:
            raise ConflictError("handoff completion claim targets a different run")
        store = self._store()
        launch = None
        try:
            run = store.run(run_id)
            handoff = store.handoff_snapshot_by_id(run_id, decoded.handoff_id)
            branch_review = handoff.packet.get("workflow") == "branch-review"
            snapshot = self._review_snapshot(run) if branch_review else None
            if branch_review:
                self._review_gate(store, run_id, handoff, snapshot, decision)
            submission = store.record_handoff_submission(run_id, decoded, decision)
            continuation = None
            if branch_review:
                entry = store.recorded_handoff_submission(run_id, decoded.handoff_id)
                if entry is None:
                    raise ConflictError("recorded branch review submission is missing")
                continuation = self._continue_branch_review_submission(
                    store, run_id, handoff, snapshot, entry,
                )
                launch = continuation.pop("launch")
            run = store.run(run_id)
            response = {
                "run_id": run_id,
                "state": run["state"],
                "phase": run["phase"],
                "version": run["version"],
                "handoff_id": submission.handoff_id,
                "submission_id": submission.submission_id,
                "submission_hash": submission.submission_hash,
                "disposition": submission.disposition,
                "recorded_run_version": submission.recorded_run_version,
                "replayed": submission.replayed,
            }
            if continuation is not None:
                response["continuation"] = continuation
        finally:
            store.close()
        if launch is not None:
            version, package, digest = launch
            self._spawn_daemon(run_id, version, package, digest)
            response["launched"] = True
        elif branch_review:
            response["launched"] = False
        return response

    def resume(self, run_id: str, recovery: dict[str, Any] | None = None) -> dict[str, Any]:
        store = self._store()
        launch: tuple[int, Path, str] | None = None
        preparation_error: dict[str, Any] | None = None
        branch_response: dict[str, Any] | None = None
        try:
            run = store.run(run_id)
            if run["state"] in TERMINAL_STATES: raise ConflictError("terminal run cannot resume; start a superseding run")
            if run["state"] == "awaiting_host" and run["phase"] == "handoff_submitted":
                handoff = store.handoff_snapshot(run_id)
                if handoff is None or handoff.packet.get("workflow") != "branch-review":
                    raise ConflictError("run has no resumable branch review submission")
                snapshot = self._review_snapshot(run)
                entry = store.recorded_handoff_submission(run_id, handoff.handoff_id)
                if entry is None:
                    raise ConflictError("recorded branch review submission is missing")
                continuation = self._continue_branch_review_submission(
                    store, run_id, handoff, snapshot, entry,
                )
                launch = continuation.pop("launch")
                current = store.run(run_id)
                branch_response = {
                    "run_id": run_id,
                    "disposition": continuation["action"],
                    "state": current["state"],
                    "version": current["version"],
                    "launched": launch is not None,
                }
            if branch_response is None and run["state"] in {"running","cancelling"}:
                from .supervisor import Supervisor
                attempt=store.attempt(run_id)
                disposition = Supervisor(store).import_durable(run_id) if attempt and attempt.get("exit_record") else Supervisor(store).recover(run_id)
                if disposition != "requeued":
                    return {"run_id": run_id, "disposition": disposition, "launched": False}
                run = store.run(run_id)
                version = run["version"]
            if branch_response is not None:
                pass
            elif run["state"] == "queued" and run["phase"] == "preparing":
                submitted = json.loads(run["submitted_request"])
                claim = store.reclaim_preparation(
                    run_id, run["version"], f"preflight-recovery:{os.getpid()}",
                )
                launch, preparation_error = self._continue_preparation(
                    store, run_id, claim.fencing_token, submitted,
                )
            elif run["state"] == "queued" and run["phase"] == "launching":
                version = store.recover_launching(run_id, run["version"])
            elif run["state"] == "queued" and run["phase"] is None:
                version = run["version"]
            elif run["state"] == "blocked":
                if not isinstance(recovery,dict) or set(recovery)!={"attempt_id","disposition"} or recovery["disposition"] not in {"confirm_dead","retain_ownership"}:
                    raise ContractError("blocked resume requires a typed recovery disposition")
                attempt=store.attempt(run_id)
                if not attempt or recovery["attempt_id"]!=attempt["id"]:
                    raise ConflictError("recovery disposition targets a different attempt")
                required="retain_ownership" if attempt["status"]=="ownership_ambiguous" else "confirm_dead"
                if recovery["disposition"]!=required:
                    raise ConflictError("recovery disposition contradicts persisted ownership")
                return {"run_id":run_id,"disposition":required,"launched":False}
            else:
                raise ConflictError("run is not resumable")
        finally: store.close()
        if branch_response is not None:
            if launch is not None:
                version, package, digest = launch
                self._spawn_daemon(run_id, version, package, digest)
            return branch_response
        if run["phase"] == "preparing":
            if launch is None:
                return {
                    "run_id": run_id,
                    "disposition": "preparation_failed",
                    "launched": False,
                    "error": preparation_error,
                }
            version,package,digest=launch
        else:
            package,digest=self._verified_package(run)
        self._spawn_daemon(run_id, version, package, digest)
        return {"run_id": run_id, "disposition": "continued", "launched": True}
