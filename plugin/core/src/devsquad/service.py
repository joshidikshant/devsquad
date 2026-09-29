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

from .codex_lead_worker import freeze_codex_lead
from .codex_review_worker import freeze_codex_reviewer
from .claude_delivery_worker import freeze_claude_implementer
from .contracts import (
    BudgetExhausted,
    CapabilityUnavailable,
    ContractError,
    ProfileUnsupported,
)
from .reports import (
    build_early_terminal_reports,
    build_terminal_reports,
    project_branch_review_history,
    validate_saved_review_handoff,
)
from .router import capacity_with_saved_observations, load_routing
from .store import (
    ConflictError,
    HandoffClaim,
    HandoffSnapshot,
    Store,
    TERMINAL_STATES,
    canonical_json,
    request_hash,
)
from .validation import validate_task
from .workflows import (
    apply_lead_disposition,
    decode_headless_lead_evidence,
    review_mode,
    validate_branch_review_handoff,
    validate_handoff_decision_evidence,
    validate_review_document,
)
from .workspaces import (
    assert_clean_inputs,
    committed_regular_file,
    prepare_check_workspace,
    prepare_delivery_workspace,
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

    def _preparation_failure_artifacts(
        self,
        store: Store,
        run_id: str,
        task: dict[str, Any],
        snapshot: dict[str, Any] | None,
        error: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if task.get("workflow") not in {"branch-review", "issue-delivery"}:
            return None
        reports = build_early_terminal_reports(
            run_id=run_id,
            state="failed",
            task=task,
            snapshot=snapshot,
            run_artifacts=[],
            events=store.events_for_run(run_id),
            completed_at=datetime.now(timezone.utc).isoformat(),
            phase="preparing",
            error=error,
        )
        prepared = []
        for name in sorted(reports):
            path, digest, size = store.finalize_artifact(run_id, name, reports[name])
            prepared.append({
                "name": name,
                "path": path,
                "sha256": digest,
                "byte_size": size,
            })
        return prepared

    @staticmethod
    def _saved_review_progress(
        store: Store,
        run_id: str,
        snapshot: dict[str, Any],
        handoff: HandoffSnapshot | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Project every persisted attempt and completed disposition in DB order."""
        history = store.branch_review_history(run_id)
        _, dispositions = project_branch_review_history(history, snapshot)
        frozen_handoffs = {
            item["handoff_id"]: {
                "handoff_id": item["handoff_id"],
                "packet": item["packet"],
                "packet_sha256": item["packet_sha256"],
            }
            for item in history
        }
        if handoff is not None:
            frozen_handoffs[handoff.handoff_id] = {
                "handoff_id": handoff.handoff_id,
                "packet": handoff.packet,
                "packet_sha256": handoff.packet_sha256,
            }
        packets = [item["packet"] for item in history]
        if (handoff is not None and not any(
                packet.get("attempt_id") == handoff.packet.get("attempt_id")
                for packet in packets)):
            packets.append(handoff.packet)
        packets_by_attempt = {
            packet["attempt_id"]: validate_saved_review_handoff(packet, snapshot)
            for packet in packets
        }
        artifacts = store.artifacts_for_run(run_id)
        artifacts_by_name = {artifact["name"]: artifact for artifact in artifacts}
        failed_by_id = {
            attempt["id"]: attempt
            for attempt in Service._failed_fallback_attempts(
                store, run_id, snapshot,
            )
        }
        attempts = []
        for attempt in store.attempts_for_run(run_id):
            if attempt["id"] in failed_by_id:
                attempts.append(failed_by_id[attempt["id"]])
                continue
            if attempt.get("role") == "implementer":
                iteration = next((
                    item for item in snapshot.get("delivery_iterations", [])
                    if item.get("candidate", {}).get("implementation_artifact")
                    == f"implementation-attempt-{attempt['id']}.json"
                ), None)
                if iteration is not None:
                    attempts.append({
                        "id": attempt["id"],
                        "status": "succeeded",
                        **iteration["implementation"]["attempt"],
                        "summary": iteration["implementation"].get("summary"),
                        "candidate": iteration["candidate"],
                    })
            elif attempt.get("role") == "reviewer":
                packet = packets_by_attempt.get(attempt["id"])
                if packet is not None:
                    attempts.append({
                        "id": attempt["id"],
                        "status": "succeeded",
                        **packet["attempt"],
                        "review": packet["review"],
                        "checks": packet["checks"],
                        "evaluation": packet["evaluation"],
                    })
            elif attempt.get("role") == "lead" and attempt.get("status") == "finished":
                artifact = artifacts_by_name.get(
                    f"lead-attempt-{attempt['id']}.json"
                )
                if artifact is None:
                    raise ConflictError("saved headless lead evidence is missing")
                content = Path(artifact["path"]).read_bytes()
                try:
                    raw = json.loads(content)
                    frozen_handoff = frozen_handoffs[raw["handoff_id"]]
                except (KeyError, TypeError, json.JSONDecodeError) as exc:
                    raise ConflictError(
                        "saved headless lead evidence is invalid"
                    ) from exc
                evidence = decode_headless_lead_evidence(
                    content, snapshot, frozen_handoff,
                )
                attempts.append({
                    "id": attempt["id"],
                    "status": "succeeded",
                    **evidence["attempt"],
                })
        return attempts, dispositions

    def _paused_review_terminal_artifacts(
        self,
        store: Store,
        run_id: str,
        snapshot: dict[str, Any],
        handoff: HandoffSnapshot | None,
        *,
        state: str,
        phase: str,
        error: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Materialize complete M3 reports before a paused run terminalizes."""
        attempts, dispositions = self._saved_review_progress(
            store, run_id, snapshot, handoff,
        )
        artifacts = store.artifacts_for_run(run_id)
        reports = build_early_terminal_reports(
            run_id=run_id,
            state=state,
            task=snapshot["task"],
            snapshot=snapshot,
            run_artifacts=artifacts,
            events=store.events_for_run(run_id),
            completed_at=datetime.now(timezone.utc).isoformat(),
            phase=phase,
            error=error,
            prior_attempts=attempts,
            prior_dispositions=dispositions,
        )
        prepared = []
        for name in sorted(reports):
            path, digest, size = store.finalize_artifact(
                run_id, name, reports[name],
            )
            prepared.append({
                "name": name,
                "path": path,
                "sha256": digest,
                "byte_size": size,
            })
        return prepared

    def fail_budget_exhausted(
        self,
        run_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        """Terminalize a queued branch review that cannot launch another worker."""
        store = self._store()
        try:
            run = store.run(run_id)
            snapshot = self._review_snapshot(run)
            if snapshot.get("task", {}).get("workflow") not in {
                "branch-review", "issue-delivery",
            }:
                raise ConflictError("queued budget failure is not a review workflow")
            error = {
                "error": "BUDGET_EXHAUSTED",
                "message": "run wall-time budget is exhausted",
            }
            history = store.branch_review_history(run_id)
            if history:
                run_artifacts = store.artifacts_for_run(run_id)
                reports = build_terminal_reports(
                    run_id=run_id,
                    state="failed",
                    snapshot=snapshot,
                    history=history,
                    run_artifacts=run_artifacts,
                    events=store.events_for_run(run_id),
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    error=error,
                    headless_leads=self._headless_leads_for_history(
                        snapshot, history, run_artifacts,
                    ),
                    failed_attempts=self._failed_fallback_attempts(
                        store, run_id, snapshot,
                    ),
                )
                terminal_artifacts = []
                for name in sorted(reports):
                    path, digest, size = store.finalize_artifact(
                        run_id, name, reports[name],
                    )
                    terminal_artifacts.append({
                        "name": name,
                        "path": path,
                        "sha256": digest,
                        "byte_size": size,
                    })
            else:
                terminal_artifacts = self._paused_review_terminal_artifacts(
                    store,
                    run_id,
                    snapshot,
                    store.handoff_snapshot(run_id),
                    state="failed",
                    phase="budget",
                    error=error,
                )
            version = store.fail_queued_budget(
                run_id, expected_version, error, terminal_artifacts,
            )
            return {"run_id": run_id, "state": "failed", "version": version}
        finally:
            store.close()

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
        internal_lead_fixture: dict[str, Any] | None = None,
        internal_implementation_fixture: dict[str, Any] | None = None,
        capacity_store: Store | None = None,
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
            if capacity_store is None:
                raise ContractError("public preflight requires shared capacity state")
            effective_registry = capacity_store.effective_profile_registry(
                config_payloads["profiles_file"],
                config_payloads["policy_file"],
            )
            snapshot["routing"] = load_routing(
                task,
                effective_registry["profiles_payload"],
                config_payloads["policy_file"],
                availability=capacity_with_saved_observations(
                    effective_registry["profiles_payload"],
                    config_payloads["policy_file"],
                    capacity_store.capacity_snapshot,
                ),
            )
            snapshot["routing"]["profile_registry"].update({
                "source_sha256": effective_registry["source_sha256"],
                "lifecycle_bindings": effective_registry[
                    "lifecycle_bindings"
                ],
            })
            if project_id is None or run_id is None:
                raise ContractError("public preflight requires run-owned workspace identity")
            if task["workflow"] == "branch-review":
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
            else:
                snapshot["delivery_workspace"] = prepare_delivery_workspace(
                    repo,
                    self.runtime,
                    project_id,
                    run_id,
                    target_oid,
                    task["scope"]["read_paths"],
                    task["scope"]["write_paths"],
                    required_clean_paths=config_paths.values(),
                )
                fixture_fields = (
                    {"writes", "delay_seconds"},
                    {"writes", "delay_seconds", "fail_profile_ids"},
                )
                def valid_implementation_fixture(value: Any) -> bool:
                    return (
                        isinstance(value, dict)
                        and set(value) in fixture_fields
                        and (
                            "fail_profile_ids" not in value
                            or isinstance(value["fail_profile_ids"], list)
                            and all(
                                isinstance(item, str) and item
                                for item in value["fail_profile_ids"]
                            )
                        )
                    )

                valid_fixture = valid_implementation_fixture(
                    internal_implementation_fixture
                )
                if (isinstance(internal_implementation_fixture, dict)
                        and set(internal_implementation_fixture) == {"iterations"}):
                    fixtures = internal_implementation_fixture["iterations"]
                    valid_fixture = (
                        isinstance(fixtures, list)
                        and 1 <= len(fixtures)
                        <= task["budget"]["max_revisions"] + 1
                        and all(
                            valid_implementation_fixture(item)
                            for item in fixtures
                        )
                    )
                if (internal_implementation_fixture is not None
                        and not valid_fixture):
                    raise ContractError("internal implementation fixture is invalid")
                if internal_implementation_fixture is not None:
                    snapshot["internal_implementation_fixture"] = json.loads(
                        canonical_json(internal_implementation_fixture)
                    )
            if internal_review_fixture is not None:
                if (not isinstance(internal_review_fixture, dict)
                        or set(internal_review_fixture)
                        != {"verdict", "summary", "findings"}):
                    raise ContractError("internal review fixture fields are invalid")
                if task["workflow"] == "branch-review":
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
                else:
                    snapshot["pending_review_fixture"] = json.loads(
                        canonical_json(internal_review_fixture)
                    )
            if internal_lead_fixture is not None:
                if (task["lead"]["mode"] != "headless"
                        or not isinstance(internal_lead_fixture, dict)
                        or set(internal_lead_fixture) != {"disposition", "reason"}
                        or internal_lead_fixture["disposition"]
                        not in {"accept", "revise", "reject"}
                        or not isinstance(internal_lead_fixture["reason"], str)):
                    raise ContractError("internal lead fixture is invalid")
                snapshot["internal_lead_fixture"] = json.loads(
                    canonical_json(internal_lead_fixture)
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
            internal_lead_fixture = submitted.get("_internal_lead_fixture")
            internal_implementation_fixture = submitted.get(
                "_internal_implementation_fixture"
            )
            store.validate_predecessor(run_id, fencing_token, supersedes_run_id)
            validated_supersedes_run_id = supersedes_run_id
            validate_task(task, require_existing_repo=True)
            minimum_headless_invocations = (
                3 if task["workflow"] == "issue-delivery" else 2
            )
            if (task["lead"]["mode"] == "headless"
                    and task["budget"]["max_worker_invocations"]
                    < minimum_headless_invocations):
                raise ContractError(
                    "headless workflow has insufficient worker invocations"
                )
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
                internal_lead_fixture=internal_lead_fixture,
                internal_implementation_fixture=internal_implementation_fixture,
                capacity_store=store,
            )
            if (task["workflow"] == "issue-delivery"
                    and internal_delay is None
                    and internal_implementation_fixture is None):
                implementer_route = snapshot["routing"]["roles"]["implementer"]
                implementer_candidates = [
                    implementer_route["selected"],
                    *implementer_route["fallbacks"],
                ]
                snapshot["implementation_adapters"] = {
                    candidate["profile_id"]: freeze_claude_implementer(candidate)
                    for candidate in implementer_candidates
                }
                snapshot["implementation_adapter"] = (
                    snapshot["implementation_adapters"][
                        implementer_route["selected"]["profile_id"]
                    ]
                )
            if ((task["workflow"] == "branch-review"
                    or (task["workflow"] == "issue-delivery"
                        and internal_implementation_fixture is None))
                    and internal_delay is None
                    and internal_review_fixture is None):
                reviewer_route = snapshot["routing"]["roles"]["reviewer"]
                reviewer_candidates = [
                    reviewer_route["selected"], *reviewer_route["fallbacks"],
                ]
                snapshot["review_adapters"] = {
                    candidate["profile_id"]: freeze_codex_reviewer(candidate)
                    for candidate in reviewer_candidates
                }
                snapshot["review_adapter"] = snapshot["review_adapters"][
                    reviewer_route["selected"]["profile_id"]
                ]
            if (internal_delay is None and task["lead"]["mode"] == "headless"
                    and internal_lead_fixture is None):
                lead_route = snapshot["routing"]["roles"]["lead"]
                lead_candidates = [lead_route["selected"], *lead_route["fallbacks"]]
                snapshot["lead_adapters"] = {
                    candidate["profile_id"]: freeze_codex_lead(candidate)
                    for candidate in lead_candidates
                }
                snapshot["lead_adapter"] = snapshot["lead_adapters"][
                    lead_route["selected"]["profile_id"]
                ]
            if store.remaining_wall_seconds(run_id) == 0:
                raise BudgetExhausted("run wall-time budget is exhausted in preflight")
            package, digest = self._freeze_package()
            version = store.complete_preparation(
                run_id,
                fencing_token,
                snapshot,
                package_path=str(package),
                package_digest=digest,
                supersedes_run_id=supersedes_run_id,
                worktree_path=(
                    snapshot.get("workspace") or snapshot.get("delivery_workspace") or {}
                ).get("path"),
            )
            return (version, package, digest), None
        except (BudgetExhausted, CapabilityUnavailable, ProfileUnsupported) as exc:
            error = {"error": exc.code, "message": str(exc)}
            terminal_artifacts = self._preparation_failure_artifacts(
                store, run_id, task, snapshot, error,
            )
            store.fail_preparation(
                run_id,
                fencing_token,
                error,
                mutable_snapshot=snapshot,
                supersedes_run_id=validated_supersedes_run_id,
                terminal_artifacts=terminal_artifacts,
            )
            return None, error
        except Exception as exc:
            error = {"error": "PREPARATION_FAILED", "message": str(exc)}
            try:
                terminal_artifacts = self._preparation_failure_artifacts(
                    store, run_id, task, snapshot, error,
                )
                # Preserve validated lineage through unrelated failures; a
                # rejected predecessor remains only in submitted_request.
                store.fail_preparation(
                    run_id,
                    fencing_token,
                    error,
                    mutable_snapshot=snapshot,
                    supersedes_run_id=validated_supersedes_run_id,
                    terminal_artifacts=terminal_artifacts,
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
        _internal_lead_fixture: dict[str, Any] | None = None,
        _internal_implementation_fixture: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate_task(task, require_existing_repo=True)
        if _internal_fake_delay is not None and _internal_review_fixture is not None:
            raise ContractError("internal lifecycle fixtures are mutually exclusive")
        if _internal_lead_fixture is not None and _internal_fake_delay is not None:
            raise ContractError("internal lifecycle fixtures are mutually exclusive")
        if (_internal_implementation_fixture is not None
                and (_internal_fake_delay is not None
                     or task["workflow"] != "issue-delivery")):
            raise ContractError("internal implementation fixture requires issue-delivery")
        submitted = {"task": task, "supersedes_run_id": supersedes_run_id}
        if _internal_fake_delay is not None:
            submitted["_internal_fake_delay"] = _internal_fake_delay
        if _internal_review_fixture is not None:
            submitted["_internal_review_fixture"] = _internal_review_fixture
        if _internal_lead_fixture is not None:
            submitted["_internal_lead_fixture"] = _internal_lead_fixture
        if _internal_implementation_fixture is not None:
            submitted["_internal_implementation_fixture"] = (
                _internal_implementation_fixture
            )
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

    def capacity_observe(self, observation: dict[str, Any]) -> dict[str, Any]:
        """Record one capacity observation and return its current pool view."""
        store = self._store()
        try:
            recorded = store.record_pool_observation(observation)
            return {
                "record": recorded,
                "capacity": store.capacity_snapshot(observation.get("pool_id")),
            }
        finally:
            store.close()

    def outcome_add(self, run_id: str, outcome: dict[str, Any]) -> dict[str, Any]:
        store = self._store()
        try:
            return store.record_outcome(run_id, outcome)
        finally:
            store.close()

    def learning_report(self, project: str | Path) -> dict[str, Any]:
        if not isinstance(project, (str, Path)):
            raise ContractError("report project path is invalid")
        store = self._store()
        try:
            return store.learning_report(Path(project))
        finally:
            store.close()

    def policy_evaluate(self, experiment: dict[str, Any]) -> dict[str, Any]:
        """Evaluate and save one frozen learning experiment without promotion."""
        store = self._store()
        try:
            return store.evaluate_learning_experiment(experiment)
        finally:
            store.close()

    @staticmethod
    def _finalize_learning_file(
        directory: Path, name: str, content: bytes,
    ) -> dict[str, Any]:
        digest = hashlib.sha256(content).hexdigest()
        component = Path(name)
        if component.name != name or not component.stem or not component.suffix:
            raise ContractError("learning file name is invalid")
        destination = directory / f"{component.stem}.{digest}{component.suffix}"
        descriptor, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if destination.read_bytes() != content:
                    raise ConflictError("content-addressed learning file is corrupt")
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {
            "path": str(destination),
            "sha256": digest,
            "byte_size": len(content),
        }

    def learning_propose(self, project: str | Path) -> dict[str, Any]:
        """Write a local review draft from saved evidence without policy mutation."""
        from .learning import (
            build_learning_proposal,
            render_learning_proposal_markdown,
        )

        if not isinstance(project, (str, Path)):
            raise ContractError("proposal project path is invalid")
        store = self._store()
        try:
            inputs = store.learning_proposal_inputs(Path(project))
        finally:
            store.close()
        generated_at = inputs["report"]["generated_at"]
        proposal = build_learning_proposal(
            inputs["report"], inputs["experiment"], generated_at=generated_at,
        )
        json_content = (canonical_json(proposal) + "\n").encode()
        markdown_content = render_learning_proposal_markdown(proposal).encode()
        directory = self.runtime / "learning" / "proposals"
        directory.mkdir(parents=True, exist_ok=True)
        return {
            "proposal": proposal,
            "artifacts": {
                "json": self._finalize_learning_file(
                    directory, f"{proposal['proposal_id']}.json", json_content,
                ),
                "markdown": self._finalize_learning_file(
                    directory, f"{proposal['proposal_id']}.md", markdown_content,
                ),
            },
        }

    def profile_template_add(self, template: dict[str, Any]) -> dict[str, Any]:
        store = self._store()
        try:
            return store.register_profile_template(template)
        finally:
            store.close()

    def profile_binding_bootstrap(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or set(request) != {
            "template", "profile", "version",
        }:
            raise ContractError("profile binding bootstrap fields are invalid")
        store = self._store()
        try:
            return store.bootstrap_profile_binding(
                request["template"], request["profile"], version=request["version"],
            )
        finally:
            store.close()

    def profile_qualification_add(
        self, qualification: dict[str, Any],
    ) -> dict[str, Any]:
        store = self._store()
        try:
            return store.record_profile_qualification(qualification)
        finally:
            store.close()

    def profile_binding_change(self, change: dict[str, Any]) -> dict[str, Any]:
        store = self._store()
        try:
            result = store.change_profile_binding(change)
        finally:
            store.close()
        return self._profile_binding_decision_artifacts(result)

    def profile_binding_fallback(self, change: dict[str, Any]) -> dict[str, Any]:
        """Apply a catalog-proven fallback to a prior qualified binding."""
        store = self._store()
        try:
            result = store.fallback_unavailable_profile_binding(change)
        finally:
            store.close()
        return self._profile_binding_decision_artifacts(result)

    def _profile_binding_decision_artifacts(
        self, result: dict[str, Any],
    ) -> dict[str, Any]:
        from .lifecycle import render_binding_decision_markdown

        receipt = result["receipt"]
        json_content = (canonical_json(receipt) + "\n").encode()
        markdown_content = render_binding_decision_markdown(receipt).encode()
        directory = self.runtime / "learning" / "decisions"
        directory.mkdir(parents=True, exist_ok=True)
        return {
            **result,
            "artifacts": {
                "json": self._finalize_learning_file(
                    directory, f"{receipt['decision_id']}.json", json_content,
                ),
                "markdown": self._finalize_learning_file(
                    directory, f"{receipt['decision_id']}.md", markdown_content,
                ),
            },
        }

    def profile_binding_status(self, alias: str) -> dict[str, Any]:
        store = self._store()
        try:
            binding = store.profile_binding(alias)
            if binding is None:
                raise ContractError("profile binding does not exist")
            return {
                "binding": binding,
                "decisions": store.profile_binding_decisions(alias),
            }
        finally:
            store.close()

    @staticmethod
    def _status_capacity(store: Store, run: dict[str, Any]) -> dict[str, Any] | None:
        try:
            snapshot = json.loads(run["mutable_snapshot"])
            routing = snapshot["routing"]
            roles = routing["roles"]
            frozen = routing["capacity"]
        except (KeyError, TypeError, json.JSONDecodeError):
            return None
        current = {}
        for role in roles.values():
            for candidate in [role["selected"], *role.get("fallbacks", [])]:
                profile = candidate["profile"]
                profile_id = candidate["profile_id"]
                if profile_id in current:
                    continue
                target = {
                    field: profile[field]
                    for field in ("harness", "model_family", "model_id")
                }
                current[profile_id] = store.capacity_snapshot(
                    profile["account_pool_id"], target=target,
                )
        return {"frozen": frozen, "current": current}

    def status(self, run_id: str) -> dict[str, Any]:
        store = self._store()
        try:
            run, attempt, handoff = store.status_snapshot(run_id)
            active=attempt if attempt and attempt.get("status") in {"reserved","running","cancelling","ownership_ambiguous"} else None
            if run["state"] == "blocked":
                next_action = "recovery_file_required"
            elif run["state"] == "awaiting_host" and run["phase"] is None:
                try:
                    snapshot = self._review_snapshot(run)
                    headless = snapshot["task"]["lead"]["mode"] == "headless"
                except (ConflictError, KeyError, TypeError):
                    headless = False
                next_action = "continue_headless_lead" if headless else "claim_handoff"
            elif run["state"] == "awaiting_host":
                next_action = "handoff_submission_saved"
            elif run["state"] == "queued" and run["phase"] is None:
                try:
                    snapshot = self._review_snapshot(run)
                    next_action = (
                        "resume_candidate_review"
                        if snapshot.get("task", {}).get("workflow") == "issue-delivery"
                        and isinstance(snapshot.get("candidate"), dict)
                        else None
                    )
                except ConflictError:
                    next_action = None
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
                "capacity": self._status_capacity(store, run),
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
            elif run["state"] == "awaiting_host":
                terminal_artifacts = None
                try:
                    snapshot = self._review_snapshot(run)
                    handoff = store.handoff_snapshot(run_id)
                    if (snapshot.get("task", {}).get("workflow")
                            in {"branch-review", "issue-delivery"}
                            and handoff is not None):
                        terminal_artifacts = self._paused_review_terminal_artifacts(
                            store,
                            run_id,
                            snapshot,
                            handoff,
                            state="cancelled",
                            phase="awaiting_host",
                            error=None,
                        )
                except (KeyError, TypeError):
                    terminal_artifacts = None
                version = store.cancel_host_wait(
                    run_id, terminal_artifacts=terminal_artifacts,
                )
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
            run = store.run(run_id)
            handoff_before_claim = store.handoff_snapshot(run_id)
            if (handoff_before_claim is not None
                    and handoff_before_claim.packet.get("workflow")
                    in {"branch-review", "issue-delivery"}
                    and self._review_snapshot(run)["task"]["lead"]["mode"]
                    == "headless"):
                raise ConflictError(
                    "headless review does not accept a host claim"
                )
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
        packet = validate_saved_review_handoff(handoff.packet, snapshot)
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

    @staticmethod
    def _headless_leads_for_history(
        snapshot: dict[str, Any],
        history: list[dict[str, Any]],
        run_artifacts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if snapshot["task"]["lead"]["mode"] != "headless":
            return []
        artifacts_by_name = {
            artifact["name"]: artifact for artifact in run_artifacts
        }
        evidence = []
        for item in history:
            submission_id = item["decision"]["submission_id"]
            if not submission_id.startswith("headless-"):
                raise ConflictError("headless lead submission identity is invalid")
            attempt_id = submission_id[len("headless-"):]
            artifact = artifacts_by_name.get(f"lead-attempt-{attempt_id}.json")
            if artifact is None:
                raise ConflictError("headless lead evidence artifact is missing")
            frozen_handoff = {
                "handoff_id": item["handoff_id"],
                "packet": item["packet"],
                "packet_sha256": item["packet_sha256"],
            }
            evidence.append(decode_headless_lead_evidence(
                Path(artifact["path"]).read_bytes(), snapshot, frozen_handoff,
            ))
        return evidence

    @staticmethod
    def _failed_fallback_attempts(
        store: Store,
        run_id: str,
        snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        failures = []
        for attempt in store.attempts_for_run(run_id):
            encoded = attempt.get("output_metadata")
            if not encoded:
                continue
            try:
                metadata = json.loads(encoded)
                if not isinstance(metadata, dict) or "failure" not in metadata:
                    continue
                error = metadata["failure"]
                role = attempt["role"]
                index = attempt["profile_index"]
                routed = snapshot["routing"]["roles"][role]
                candidates = [routed["selected"], *routed["fallbacks"]]
                selected = candidates[index]
            except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ConflictError("saved fallback attempt is invalid") from exc
            if (not isinstance(error, dict)
                    or selected["profile_id"] != attempt["profile_id"]):
                raise ConflictError("saved fallback attempt changed its profile")
            failures.append({
                "id": attempt["id"],
                "role": role,
                "status": "failed",
                "profile_index": index,
                "selected_profile": selected,
                "observed_identity": None,
                "worker_invocations": 1,
                "native_model_requests": None,
                "usage": {
                    "input_tokens": None,
                    "output_tokens": None,
                    "total_tokens": None,
                    "source": "unavailable",
                },
                "error": error,
            })
        return failures

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
        headless_leads = self._headless_leads_for_history(
            snapshot, history, run_artifacts,
        )
        reports = build_terminal_reports(
            run_id=run_id,
            state=terminal_state,
            snapshot=snapshot,
            history=history,
            run_artifacts=run_artifacts,
            events=store.events_for_run(run_id),
            completed_at=datetime.now(timezone.utc).isoformat(),
            error=error,
            headless_leads=headless_leads,
            failed_attempts=self._failed_fallback_attempts(
                store, run_id, snapshot,
            ),
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
            workflow = snapshot["task"]["workflow"]
            requeue_method = (
                store.requeue_delivery_revision
                if workflow == "issue-delivery"
                else store.requeue_review_revision
            )
            requeue = requeue_method(
                run_id, handoff.handoff_id,
                decision["submission_id"], decision["submission_hash"],
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
                "message": f"{workflow} worker invocation or wall budget is exhausted",
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

    def _saved_headless_lead(
        self,
        store: Store,
        run_id: str,
        snapshot: dict[str, Any],
        handoff: HandoffSnapshot,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        frozen_handoff = {
            "handoff_id": handoff.handoff_id,
            "packet": handoff.packet,
            "packet_sha256": handoff.packet_sha256,
        }
        for attempt in reversed(store.attempts_for_run(run_id)):
            if attempt.get("role") != "lead" or attempt.get("status") != "finished":
                continue
            artifact = store.artifact_named(
                run_id, f"lead-attempt-{attempt['id']}.json",
            )
            if artifact is None:
                continue
            path = Path(artifact["path"])
            try:
                content = path.read_bytes()
            except OSError as exc:
                raise ConflictError("saved headless lead evidence is missing") from exc
            if (len(content) != artifact["byte_size"]
                    or hashlib.sha256(content).hexdigest() != artifact["sha256"]):
                raise ConflictError("saved headless lead evidence is corrupt")
            try:
                evidence = decode_headless_lead_evidence(
                    content, snapshot, frozen_handoff,
                )
            except ContractError:
                continue
            return attempt, evidence
        return None

    def _continue_headless_lead(
        self,
        store: Store,
        run_id: str,
        run: dict[str, Any],
        handoff: HandoffSnapshot,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        saved = self._saved_headless_lead(
            store, run_id, snapshot, handoff,
        )
        if saved is None:
            queued = store.queue_headless_lead(run_id, run["version"])
            if queued["action"] == "budget_exhausted":
                error = {
                    "error": "BUDGET_EXHAUSTED",
                    "message": "headless lead worker invocation budget is exhausted",
                }
                terminal_artifacts = self._paused_review_terminal_artifacts(
                    store,
                    run_id,
                    snapshot,
                    handoff,
                    state="failed",
                    phase="lead",
                    error=error,
                )
                version = store.fail_queued_budget(
                    run_id, run["version"], error, terminal_artifacts,
                )
                return {
                    "action": "budget_exhausted",
                    "state": "failed",
                    "version": version,
                    "replayed_continuation": False,
                    "launch": None,
                }
            if queued["action"] != "queued":
                raise ConflictError("headless lead handoff was not queueable")
            prepared = store.run(run_id)
            package, digest = self._verified_package(prepared)
            return {
                "action": "headless_lead_queued",
                "state": prepared["state"],
                "version": prepared["version"],
                "replayed_continuation": False,
                "launch": (prepared["version"], package, digest),
            }

        attempt, evidence = saved
        claim = store.claim_handoff(
            run_id,
            run["version"],
            f"headless-lead:{attempt['id']}",
        )
        choice = evidence["choice"]
        body = {
            "schema_version": 1,
            "submission_id": f"headless-{attempt['id']}",
            "disposition": choice["disposition"],
            "reason": choice["reason"],
            "evidence_refs": [
                {
                    "artifact_id": reference["artifact_id"],
                    "sha256": reference["sha256"],
                }
                for reference in handoff.packet["artifacts"]
            ],
        }
        decision = {**body, "submission_hash": request_hash(body)}
        self._review_gate(store, run_id, handoff, snapshot, decision)
        submission = store.record_handoff_submission(run_id, claim, decision)
        entry = store.recorded_handoff_submission(run_id, handoff.handoff_id)
        if entry is None:
            raise ConflictError("recorded headless lead submission is missing")
        continuation = self._continue_branch_review_submission(
            store, run_id, handoff, snapshot, entry,
        )
        launch = continuation.pop("launch")
        current = store.run(run_id)
        return {
            "action": continuation["action"],
            "state": current["state"],
            "version": current["version"],
            "submission_id": submission.submission_id,
            "disposition": submission.disposition,
            "replayed_continuation": continuation["replayed_continuation"],
            "launch": launch,
        }

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
            managed_review = handoff.packet.get("workflow") in {
                "branch-review", "issue-delivery",
            }
            snapshot = self._review_snapshot(run) if managed_review else None
            if (managed_review and snapshot["task"]["lead"]["mode"] == "headless"
                    and not decoded.owner_id.startswith("headless-lead:")):
                raise ConflictError(
                    "headless review does not accept a host completion"
                )
            if managed_review:
                self._review_gate(store, run_id, handoff, snapshot, decision)
            submission = store.record_handoff_submission(run_id, decoded, decision)
            continuation = None
            if managed_review:
                entry = store.recorded_handoff_submission(run_id, decoded.handoff_id)
                if entry is None:
                    raise ConflictError("recorded review submission is missing")
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
        elif managed_review:
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
            if run["state"] == "awaiting_host" and run["phase"] is None:
                handoff = store.handoff_snapshot(run_id)
                if (handoff is None or handoff.packet.get("workflow")
                        not in {"branch-review", "issue-delivery"}):
                    raise ConflictError("run has no resumable review handoff")
                snapshot = self._review_snapshot(run)
                if snapshot["task"]["lead"]["mode"] == "headless":
                    continuation = self._continue_headless_lead(
                        store, run_id, run, handoff, snapshot,
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
                else:
                    raise ConflictError("host-led handoff must be completed by its host")
            elif run["state"] == "awaiting_host" and run["phase"] == "handoff_submitted":
                handoff = store.handoff_snapshot(run_id)
                if (handoff is None or handoff.packet.get("workflow")
                        not in {"branch-review", "issue-delivery"}):
                    raise ConflictError("run has no resumable review submission")
                snapshot = self._review_snapshot(run)
                entry = store.recorded_handoff_submission(run_id, handoff.handoff_id)
                if entry is None:
                    raise ConflictError("recorded review submission is missing")
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
