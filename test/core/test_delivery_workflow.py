from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

CORE = Path(__file__).resolve().parents[2] / "plugin" / "core"
sys.path.insert(0, str(CORE / "src"))

from devsquad.contracts import ContractError
from devsquad.claude_delivery_worker import (
    freeze_claude_implementer,
    run as run_claude_implementer,
)
from devsquad.service import Service
from devsquad.store import ConflictError, Store, request_hash
from devsquad.workspaces import (
    freeze_delivery_candidate,
    prepare_delivery_workspace,
    resolve_commit,
)
from devsquad.workflows import validate_branch_review_evidence


class DeliveryWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.runtime = self.root / "runtime"
        self.remote = self.root / "remote.git"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q")
        self.git(self.repo, "config", "user.name", "Fixture")
        self.git(self.repo, "config", "user.email", "fixture@example.test")
        (self.repo / "src").mkdir()
        (self.repo / "tests").mkdir()
        (self.repo / "src/app.py").write_text("VALUE = 'base'\n")
        (self.repo / "tests/test_app.py").write_text("# base test\n")
        (self.repo / "README.md").write_text("fixture\n")
        (self.repo / "devsquad").mkdir()
        profiles, policy = self.delivery_routing_documents()
        (self.repo / "devsquad/profiles.json").write_text(
            json.dumps(profiles, sort_keys=True) + "\n"
        )
        (self.repo / "devsquad/policy.json").write_text(
            json.dumps(policy, sort_keys=True) + "\n"
        )
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "base")
        self.baseline = resolve_commit(self.repo, "HEAD")
        subprocess.run(
            ["git", "init", "--bare", "-q", str(self.remote)], check=True,
        )
        self.git(self.repo, "remote", "add", "origin", str(self.remote))
        self.git(self.repo, "push", "-q", "origin", "HEAD:refs/heads/main")
        self.source_status = self.git(self.repo, "status", "--porcelain")
        self.source_refs = self.git(self.repo, "show-ref")
        self.remote_refs = self.git(self.remote, "show-ref")

    @staticmethod
    def delivery_routing_documents():
        def profile(
            profile_id: str,
            *,
            family: str,
            model: str,
            permission: str,
        ) -> dict[str, object]:
            return {
                "id": profile_id,
                "harness": "fixture",
                "model_family": family,
                "model_id": model,
                "effort": {"value": "low", "transport": "native"},
                "required_tools": ["read", "write"] if permission == "workspace_write" else ["read"],
                "permission_policy": permission,
                "account_pool_id": f"{profile_id}-subscription",
                "billing_mode": "subscription",
                "quality_status": "proven",
                "evidence_refs": ["tracked-fixture"],
            }

        profiles = {
            "schema_version": 1,
            "profiles": [
                profile(
                    "fixture-implementer",
                    family="fixture-family-a",
                    model="fixture-write-model",
                    permission="workspace_write",
                ),
                profile(
                    "fixture-implementer-fallback",
                    family="fixture-family-a2",
                    model="fixture-write-model-fallback",
                    permission="workspace_write",
                ),
                profile(
                    "fixture-reviewer",
                    family="fixture-family-b",
                    model="fixture-review-model",
                    permission="read_only",
                ),
                profile(
                    "fixture-lead",
                    family="fixture-family-c",
                    model="fixture-lead-model",
                    permission="read_only",
                ),
            ],
            "bindings": {},
        }
        policy = {
            "schema_version": 1,
            "id": "delivery-fixture-policy",
            "version": 1,
            "roles": {
                "implementer": [
                    {"kind": "profile", "id": "fixture-implementer"},
                    {"kind": "profile", "id": "fixture-implementer-fallback"},
                ],
                "reviewer": [
                    {"kind": "profile", "id": "fixture-reviewer"}
                ],
                "lead": [{"kind": "profile", "id": "fixture-lead"}],
            },
            "task_classes": {"fixture-delivery-small": "proven"},
            "require_different_model_for_review": True,
            "prefer_different_harness_for_review": True,
            "account_pools": {
                "fixture-implementer-subscription": {
                    "allowed_billing_modes": ["subscription"],
                    "max_concurrency": 1,
                    "unknown_capacity_policy": "allow_bounded",
                },
                "fixture-implementer-fallback-subscription": {
                    "allowed_billing_modes": ["subscription"],
                    "max_concurrency": 1,
                    "unknown_capacity_policy": "allow_bounded",
                },
                "fixture-reviewer-subscription": {
                    "allowed_billing_modes": ["subscription"],
                    "max_concurrency": 1,
                    "unknown_capacity_policy": "allow_bounded",
                },
                "fixture-lead-subscription": {
                    "allowed_billing_modes": ["subscription"],
                    "max_concurrency": 1,
                    "unknown_capacity_policy": "allow_bounded",
                },
            },
            "experiment_budget": {},
        }
        return profiles, policy

    @staticmethod
    def git(repo: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout

    def prepare(self) -> dict[str, object]:
        return prepare_delivery_workspace(
            self.repo,
            self.runtime,
            "project-1",
            "run-1",
            self.baseline,
            ("src", "tests"),
            ("src/app.py", "tests"),
        )

    def delivery_task(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "project": {
                "repo_path": str(self.repo),
                "base_ref": self.baseline,
                "target_ref": self.baseline,
            },
            "workflow": "issue-delivery",
            "goal": "Fix the fixture value and add one regression test.",
            "task_class": "fixture-delivery-small",
            "acceptance": [
                {
                    "id": "value-fixed",
                    "description": "The fixture value is fixed.",
                    "evidence_kind": "check",
                },
                {
                    "id": "independent-review",
                    "description": "A different model reviews the candidate.",
                    "evidence_kind": "review",
                },
            ],
            "checks": [
                {
                    "id": "fixture-check",
                    "argv": ["python3", "-c", "print('ok')"],
                    "cwd": ".",
                    "timeout_seconds": 10,
                    "required_to_pass": True,
                }
            ],
            "scope": {
                "read_paths": ["src", "tests"],
                "write_paths": ["src/app.py", "tests"],
            },
            "lead": {"mode": "host"},
            "routing": {
                "profiles_file": "devsquad/profiles.json",
                "policy_file": "devsquad/policy.json",
            },
            "budget": {
                "wall_seconds": 30,
                "max_worker_invocations": 5,
                "max_revisions": 1,
                "max_fallbacks_per_step": 0,
            },
            "origin": {"surface": "test"},
        }

    @staticmethod
    def implementation_fixture(delay: float = 0) -> dict[str, object]:
        return {
            "writes": [
                {"path": "src/app.py", "content": "VALUE = 'fixed'\n"},
                {
                    "path": "tests/test regression.py",
                    "content": "def test_regression():\n    assert True\n",
                },
            ],
            "delay_seconds": delay,
        }

    @staticmethod
    def repair_fixtures() -> dict[str, object]:
        return {
            "iterations": [
                {
                    "writes": [
                        {"path": "src/app.py", "content": "VALUE = 'wrong'\n"},
                    ],
                    "delay_seconds": 0,
                },
                {
                    "writes": [
                        {"path": "src/app.py", "content": "VALUE = 'fixed'\n"},
                    ],
                    "delay_seconds": 0,
                },
            ],
        }

    @staticmethod
    def clean_review_fixture() -> dict[str, object]:
        return {
            "verdict": "clean",
            "summary": "The exact frozen candidate satisfies the task.",
            "findings": [],
        }

    @staticmethod
    def decision(
        packet: dict[str, object],
        submission_id: str,
        disposition: str,
        reason: str,
    ) -> dict[str, object]:
        body = {
            "schema_version": 1,
            "submission_id": submission_id,
            "disposition": disposition,
            "reason": reason,
            "evidence_refs": [
                {
                    "artifact_id": reference["artifact_id"],
                    "sha256": reference["sha256"],
                }
                for reference in packet["artifacts"]
            ],
        }
        return {**body, "submission_hash": request_hash(body)}

    def wait_for_candidate(self, service: Service, run_id: str) -> dict[str, object]:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status = service.status(run_id)
            if status["state"] == "queued" and status["next_action"] == "resume_candidate_review":
                return status
            if status["state"] in {"failed", "cancelled"}:
                self.fail(f"delivery run terminalized early: {status}")
            time.sleep(0.05)
        self.fail(f"delivery candidate did not become ready: {service.status(run_id)}")

    def wait_for_handoff(self, service: Service, run_id: str) -> dict[str, object]:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status = service.status(run_id)
            if status["state"] == "awaiting_host":
                return status
            if status["state"] in {"failed", "cancelled"}:
                self.fail(f"delivery review terminalized early: {status}")
            time.sleep(0.05)
        self.fail(f"delivery handoff did not become ready: {service.status(run_id)}")

    def wait_for_terminal(self, service: Service, run_id: str) -> dict[str, object]:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status = service.status(run_id)
            if status["state"] in {"succeeded", "failed", "cancelled"}:
                return status
            time.sleep(0.05)
        self.fail(f"delivery run did not terminalize: {service.status(run_id)}")

    def assert_source_unchanged(self) -> None:
        self.assertEqual(resolve_commit(self.repo, "HEAD"), self.baseline)
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), self.source_status)
        self.assertEqual(self.git(self.repo, "show-ref"), self.source_refs)
        self.assertEqual(self.git(self.remote, "show-ref"), self.remote_refs)
        self.assertEqual((self.repo / "src/app.py").read_text(), "VALUE = 'base'\n")

    def test_scoped_candidate_commit_patch_and_replay_preserve_source_and_remote(self):
        prepared = self.prepare()
        workspace = Path(prepared["path"])
        self.assertEqual(self.git(workspace, "rev-parse", "--abbrev-ref", "HEAD").strip(), "HEAD")
        (workspace / "src/app.py").write_text("VALUE = 'fixed'\n")
        (workspace / "tests/test regression.py").write_text(
            "def test_regression():\n    assert True\n"
        )

        candidate, patch = freeze_delivery_candidate(
            self.repo,
            workspace,
            self.baseline,
            ("src/app.py", "tests"),
            "run-1",
        )
        self.assertEqual(candidate["baseline_oid"], self.baseline)
        self.assertEqual(candidate["patch_sha256"], hashlib.sha256(patch).hexdigest())
        self.assertEqual(
            candidate["captured_untracked_paths"], ["tests/test regression.py"],
        )
        self.assertEqual(
            candidate["changed_paths"], ["src/app.py", "tests/test regression.py"],
        )
        self.assertIn(b"VALUE = 'fixed'", patch)
        self.assertEqual(
            self.git(workspace, "rev-parse", "HEAD^").strip(), self.baseline,
        )
        self.assertEqual(self.git(workspace, "status", "--porcelain"), "")
        replayed, replay_patch = freeze_delivery_candidate(
            self.repo,
            workspace,
            self.baseline,
            ("src/app.py", "tests"),
            "run-1",
        )
        self.assertEqual(replayed, candidate)
        self.assertEqual(replay_patch, patch)
        self.assert_source_unchanged()

    def test_frozen_claude_worker_edits_only_the_delivery_workspace(self):
        prepared = self.prepare()
        binary = self.root / "claude"
        binary.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--version\" ]; then\n"
            "  printf '%s\\n' '2.1.220 (Claude Code)'\n"
            "  exit 0\n"
            "fi\n"
            "separator=0\n"
            "for argument do\n"
            "  if [ \"$separator\" = 1 ]; then break; fi\n"
            "  if [ \"$argument\" = -- ]; then separator=1; fi\n"
            "done\n"
            "[ \"$separator\" = 1 ] || exit 9\n"
            "printf '%s\\n' \"VALUE = 'fixed'\" > src/app.py\n"
            "printf '%s\\n' '{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false,"
            "\"result\":\"Applied the bounded fix.\","
            "\"session_id\":\"session-fixture\","
            "\"modelUsage\":{\"claude-sonnet-fixture\":{\"inputTokens\":12,\"outputTokens\":7}},"
            "\"usage\":{\"input_tokens\":12,\"output_tokens\":7}}'\n"
        )
        binary.chmod(0o700)
        profile = {
            "id": "claude-implementer",
            "harness": "claude",
            "model_family": "claude-sonnet",
            "model_id": "claude-sonnet-fixture",
            "effort": {"value": "high", "transport": "native"},
            "required_tools": ["read", "write"],
            "permission_policy": "workspace_write",
            "account_pool_id": "claude-subscription",
            "billing_mode": "subscription",
            "quality_status": "proven",
            "evidence_refs": ["fixture"],
        }
        selected = {
            "reference": {"kind": "profile", "id": profile["id"]},
            "binding": None,
            "profile_id": profile["id"],
            "profile_sha256": hashlib.sha256(
                json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "profile": profile,
        }
        with patch.dict(os.environ, {"PATH": str(self.root)}):
            adapter = freeze_claude_implementer(selected)
            snapshot = {
                "task": self.delivery_task(),
                "delivery_workspace": prepared,
                "routing": {
                    "roles": {
                        "implementer": {"selected": selected, "fallbacks": []},
                    },
                },
                "implementation_adapter": adapter,
                "implementation_adapters": {profile["id"]: adapter},
            }
            evidence = run_claude_implementer(snapshot)
        self.assertEqual(
            (Path(prepared["path"]) / "src/app.py").read_text(),
            "VALUE = 'fixed'\n",
        )
        self.assertEqual(evidence["attempt"]["observed_identity"]["harness"], "claude")
        self.assertEqual(evidence["attempt"]["native_ids"]["session_id"], "session-fixture")
        self.assertEqual(evidence["attempt"]["usage"]["total_tokens"], 19)
        self.assert_source_unchanged()

    def test_later_mutation_cannot_replay_a_frozen_candidate(self):
        workspace = Path(self.prepare()["path"])
        (workspace / "src/app.py").write_text("VALUE = 'candidate'\n")
        freeze_delivery_candidate(
            self.repo,
            workspace,
            self.baseline,
            ("src/app.py", "tests"),
            "run-1",
        )
        (workspace / "src/app.py").write_text("VALUE = 'stale'\n")
        with self.assertRaisesRegex(ContractError, "later workspace changes"):
            freeze_delivery_candidate(
                self.repo,
                workspace,
                self.baseline,
                ("src/app.py", "tests"),
                "run-1",
            )
        self.assert_source_unchanged()

    def test_out_of_scope_change_is_rejected_before_commit(self):
        workspace = Path(self.prepare()["path"])
        (workspace / "README.md").write_text("unauthorized\n")
        with self.assertRaisesRegex(ContractError, "outside write scope"):
            freeze_delivery_candidate(
                self.repo,
                workspace,
                self.baseline,
                ("src/app.py", "tests"),
                "run-1",
            )
        self.assertEqual(resolve_commit(workspace, "HEAD"), self.baseline)
        self.assert_source_unchanged()

    def test_new_symlink_cannot_escape_the_delivery_workspace(self):
        workspace = Path(self.prepare()["path"])
        outside = self.root / "outside-secret"
        outside.write_text("private\n")
        (workspace / "tests/leak").symlink_to(outside)
        with self.assertRaisesRegex(ContractError, "escapes its workspace"):
            freeze_delivery_candidate(
                self.repo,
                workspace,
                self.baseline,
                ("src/app.py", "tests"),
                "run-1",
            )
        self.assertEqual(resolve_commit(workspace, "HEAD"), self.baseline)
        self.assert_source_unchanged()

    def test_candidate_requires_a_change(self):
        workspace = Path(self.prepare()["path"])
        with self.assertRaisesRegex(ContractError, "contains no changes"):
            freeze_delivery_candidate(
                self.repo,
                workspace,
                self.baseline,
                ("src/app.py", "tests"),
                "run-1",
            )
        self.assert_source_unchanged()

    def test_durable_implementer_publishes_candidate_artifacts_once(self):
        service = Service(self.runtime)
        started = service.start(
            self.delivery_task(),
            "durable-delivery",
            _internal_implementation_fixture=self.implementation_fixture(),
        )
        status = self.wait_for_candidate(service, started["run_id"])
        self.assertEqual(status["phase"], None)

        store = Store(service.database, service.artifacts)
        self.addCleanup(store.close)
        run = store.run(started["run_id"])
        snapshot = json.loads(run["mutable_snapshot"])
        candidate = snapshot["candidate"]
        attempts = store.attempts_for_run(started["run_id"])
        artifacts = {item["name"]: item for item in store.artifacts_for_run(started["run_id"])}
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["role"], "implementer")
        self.assertEqual(attempts[0]["status"], "finished")
        self.assertEqual(store.worker_invocations(started["run_id"]), 1)
        self.assertIn("candidate-1.json", artifacts)
        self.assertIn("candidate-1.patch", artifacts)
        self.assertIn(
            f"implementation-attempt-{attempts[0]['id']}.json", artifacts,
        )
        self.assertEqual(
            artifacts["candidate-1.patch"]["sha256"], candidate["patch_sha256"],
        )
        self.assertEqual(
            resolve_commit(Path(snapshot["workspace"]["path"]), "HEAD"),
            candidate["commit_oid"],
        )
        self.assertEqual(
            resolve_commit(Path(snapshot["check_workspace"]["path"]), "HEAD"),
            candidate["commit_oid"],
        )
        event_types = [
            event["type"] for event in store.events_for_run(started["run_id"])
        ]
        self.assertEqual(event_types.count("delivery.candidate_ready"), 1)
        self.assert_source_unchanged()

    def test_exact_candidate_is_reviewed_checked_and_published_for_lead(self):
        service = Service(self.runtime)
        started = service.start(
            self.delivery_task(),
            "reviewed-delivery",
            _internal_implementation_fixture=self.implementation_fixture(),
            _internal_review_fixture=self.clean_review_fixture(),
        )
        self.wait_for_candidate(service, started["run_id"])
        resumed = service.resume(started["run_id"])
        self.assertTrue(resumed["launched"])
        status = self.wait_for_handoff(service, started["run_id"])
        claimed = service.handoff_claim(
            started["run_id"], status["version"], "fixture-host",
        )
        packet = claimed["handoff"]["packet"]

        store = Store(service.database, service.artifacts)
        self.addCleanup(store.close)
        snapshot = json.loads(store.run(started["run_id"])["mutable_snapshot"])
        attempts = store.attempts_for_run(started["run_id"])
        self.assertEqual([item["role"] for item in attempts], ["implementer", "reviewer"])
        self.assertEqual(packet["workflow"], "issue-delivery")
        self.assertEqual(
            packet["candidate_sha256"], snapshot["candidate"]["candidate_sha256"],
        )
        self.assertTrue(packet["evaluation"]["accept_allowed"])
        self.assertEqual(packet["checks"][0]["status"], "passed")
        self.assertEqual(
            {item["name"] for item in packet["artifacts"]},
            {
                f"review-{attempts[1]['id']}.json",
                f"checks-{attempts[1]['id']}.json",
                f"evaluation-{attempts[1]['id']}.json",
                f"review-attempt-{attempts[1]['id']}.json",
            },
        )
        self.assert_source_unchanged()

    def test_host_accept_publishes_complete_delivery_receipt(self):
        service = Service(self.runtime)
        started = service.start(
            self.delivery_task(),
            "accepted-delivery",
            _internal_implementation_fixture=self.implementation_fixture(),
            _internal_review_fixture=self.clean_review_fixture(),
        )
        self.wait_for_candidate(service, started["run_id"])
        service.resume(started["run_id"])
        status = self.wait_for_handoff(service, started["run_id"])
        claimed = service.handoff_claim(
            started["run_id"], status["version"], "fixture-host",
        )
        packet = claimed["handoff"]["packet"]
        completed = service.handoff_complete(
            started["run_id"],
            claimed["claim"],
            self.decision(packet, "accept-delivery", "accept", "Candidate accepted."),
        )
        self.assertEqual(completed["state"], "succeeded")
        self.assertEqual(completed["continuation"]["action"], "terminal")
        result = service.result(started["run_id"])
        receipt_artifact = next(
            item for item in result["artifacts"] if item["name"] == "receipt.json"
        )
        receipt = json.loads(Path(receipt_artifact["path"]).read_text())
        self.assertEqual(receipt["workflow"], "issue-delivery")
        self.assertEqual(receipt["candidate"]["sha256"], packet["candidate_sha256"])
        self.assertEqual(
            [attempt["role"] for attempt in receipt["attempts"]],
            ["implementer", "reviewer"],
        )
        self.assertEqual(receipt["delivery_iterations"][0]["iteration"], 1)
        self.assertEqual(receipt["accounting"]["worker_invocations"], 2)
        self.assertEqual(receipt["lead"]["disposition"], "accept")
        self.assert_source_unchanged()

    def test_required_failure_blocks_delivery_accept_and_allows_reject(self):
        task = self.delivery_task()
        task["checks"][0] = {
            **task["checks"][0],
            "argv": ["python3", "-c", "raise SystemExit(1)"],
        }
        service = Service(self.runtime)
        started = service.start(
            task,
            "rejected-delivery",
            _internal_implementation_fixture=self.implementation_fixture(),
            _internal_review_fixture=self.clean_review_fixture(),
        )
        self.wait_for_candidate(service, started["run_id"])
        service.resume(started["run_id"])
        status = self.wait_for_handoff(service, started["run_id"])
        claimed = service.handoff_claim(
            started["run_id"], status["version"], "fixture-host",
        )
        packet = claimed["handoff"]["packet"]
        with self.assertRaisesRegex(ContractError, "blocked by required evidence"):
            service.handoff_complete(
                started["run_id"],
                claimed["claim"],
                self.decision(packet, "blocked-accept", "accept", "Accept anyway."),
            )
        completed = service.handoff_complete(
            started["run_id"],
            claimed["claim"],
            self.decision(packet, "reject-delivery", "reject", "Required check failed."),
        )
        self.assertEqual(completed["state"], "failed")
        receipt_artifact = next(
            item for item in service.result(started["run_id"])["artifacts"]
            if item["name"] == "receipt.json"
        )
        receipt = json.loads(Path(receipt_artifact["path"]).read_text())
        self.assertEqual(receipt["error"]["error"], "REVIEW_REJECTED")
        self.assertEqual(receipt["evaluation"]["required_failures"], ["fixture-check"])
        self.assert_source_unchanged()

    def test_revision_returns_to_implementer_and_replaces_candidate_evidence(self):
        task = self.delivery_task()
        task["checks"][0] = {
            **task["checks"][0],
            "argv": [
                "python3", "-c",
                "from pathlib import Path; "
                "assert Path('src/app.py').read_text() == \"VALUE = 'fixed'\\n\"",
            ],
        }
        service = Service(self.runtime)
        started = service.start(
            task,
            "repaired-delivery",
            _internal_implementation_fixture=self.repair_fixtures(),
            _internal_review_fixture=self.clean_review_fixture(),
        )
        self.wait_for_candidate(service, started["run_id"])
        service.resume(started["run_id"])
        first_wait = self.wait_for_handoff(service, started["run_id"])
        first_claim = service.handoff_claim(
            started["run_id"], first_wait["version"], "fixture-host-one",
        )
        first_packet = first_claim["handoff"]["packet"]
        self.assertFalse(first_packet["evaluation"]["accept_allowed"])
        revised = service.handoff_complete(
            started["run_id"], first_claim["claim"],
            self.decision(
                first_packet, "repair-delivery", "revise", "Fix the required value.",
            ),
        )
        self.assertEqual(revised["continuation"]["action"], "requeued")
        self.assertTrue(revised["launched"])
        self.wait_for_candidate(service, started["run_id"])

        store = Store(service.database, service.artifacts)
        try:
            snapshot = json.loads(store.run(started["run_id"])["mutable_snapshot"])
            self.assertEqual(len(snapshot["delivery_iterations"]), 2)
            candidates = [
                item["candidate"] for item in snapshot["delivery_iterations"]
            ]
            self.assertNotEqual(
                candidates[0]["candidate_sha256"], candidates[1]["candidate_sha256"],
            )
            self.assertNotEqual(candidates[0]["commit_oid"], candidates[1]["commit_oid"])
            self.assertEqual(
                candidates[1]["baseline_oid"], candidates[0]["baseline_oid"],
            )
            self.assertEqual(
                snapshot["delivery_iterations"][1]["revision_request"][
                    "previous_candidate_sha256"
                ],
                candidates[0]["candidate_sha256"],
            )
            self.assertEqual(
                [item["role"] for item in store.attempts_for_run(started["run_id"])],
                ["implementer", "reviewer", "implementer"],
            )
            stale_evidence = {
                field: first_packet[field]
                for field in (
                    "schema_version", "workflow", "candidate_sha256", "base_oid",
                    "target_oid", "review", "checks", "evaluation", "attempt",
                )
            }
            with self.assertRaisesRegex(
                ContractError, "changes frozen candidate_sha256",
            ):
                validate_branch_review_evidence(stale_evidence, snapshot)
        finally:
            store.close()

        service.resume(started["run_id"])
        second_wait = self.wait_for_handoff(service, started["run_id"])
        second_claim = service.handoff_claim(
            started["run_id"], second_wait["version"], "fixture-host-two",
        )
        second_packet = second_claim["handoff"]["packet"]
        self.assertNotEqual(
            first_packet["candidate_sha256"], second_packet["candidate_sha256"],
        )
        self.assertTrue(second_packet["evaluation"]["accept_allowed"])
        completed = service.handoff_complete(
            started["run_id"], second_claim["claim"],
            self.decision(
                second_packet, "accept-repair", "accept", "Repair accepted.",
            ),
        )
        self.assertEqual(completed["state"], "succeeded")
        receipt_artifact = next(
            item for item in service.result(started["run_id"])["artifacts"]
            if item["name"] == "receipt.json"
        )
        receipt = json.loads(Path(receipt_artifact["path"]).read_text())
        self.assertEqual(
            [item["role"] for item in receipt["attempts"]],
            ["implementer", "implementer", "reviewer", "reviewer"],
        )
        self.assertEqual(
            [item["disposition"] for item in receipt["dispositions"]],
            ["revise", "accept"],
        )
        self.assertEqual(receipt["revisions"]["executed"], 1)
        with self.assertRaises(ConflictError):
            service.handoff_complete(
                started["run_id"], first_claim["claim"],
                self.decision(
                    first_packet, "stale-reject", "reject", "Stale evidence.",
                ),
            )
        self.assert_source_unchanged()

    def test_delivery_revision_and_invocation_budgets_fail_before_new_writer(self):
        for suffix, max_revisions, max_invocations in (
            ("revision", 0, 5),
            ("invocation", 1, 3),
        ):
            with self.subTest(budget=suffix):
                task = self.delivery_task()
                task["budget"]["max_revisions"] = max_revisions
                task["budget"]["max_worker_invocations"] = max_invocations
                service = Service(self.runtime / suffix)
                started = service.start(
                    task,
                    f"exhausted-{suffix}",
                    _internal_implementation_fixture=self.implementation_fixture(),
                    _internal_review_fixture=self.clean_review_fixture(),
                )
                self.wait_for_candidate(service, started["run_id"])
                service.resume(started["run_id"])
                waiting = self.wait_for_handoff(service, started["run_id"])
                claimed = service.handoff_claim(
                    started["run_id"], waiting["version"], f"host-{suffix}",
                )
                packet = claimed["handoff"]["packet"]
                completed = service.handoff_complete(
                    started["run_id"], claimed["claim"],
                    self.decision(
                        packet, f"revise-{suffix}", "revise", "Request repair.",
                    ),
                )
                self.assertEqual(completed["state"], "failed")
                self.assertFalse(completed["launched"])
                store = Store(service.database, service.artifacts)
                try:
                    self.assertEqual(
                        [item["role"] for item in store.attempts_for_run(
                            started["run_id"]
                        )],
                        ["implementer", "reviewer"],
                    )
                finally:
                    store.close()
                receipt_artifact = next(
                    item for item in service.result(started["run_id"])["artifacts"]
                    if item["name"] == "receipt.json"
                )
                receipt = json.loads(Path(receipt_artifact["path"]).read_text())
                self.assertEqual(receipt["error"]["error"], "BUDGET_EXHAUSTED")
                self.assertEqual(receipt["lead"]["disposition"], "revise")

    def test_headless_delivery_lead_accepts_the_candidate(self):
        task = self.delivery_task()
        task["lead"] = {"mode": "headless"}
        service = Service(self.runtime)
        started = service.start(
            task,
            "headless-delivery",
            _internal_implementation_fixture=self.implementation_fixture(),
            _internal_review_fixture=self.clean_review_fixture(),
            _internal_lead_fixture={
                "disposition": "accept",
                "reason": "All required evidence passes.",
            },
        )
        self.wait_for_candidate(service, started["run_id"])
        service.resume(started["run_id"])
        terminal = self.wait_for_terminal(service, started["run_id"])
        self.assertEqual(terminal["state"], "succeeded")
        receipt_artifact = next(
            item for item in service.result(started["run_id"])["artifacts"]
            if item["name"] == "receipt.json"
        )
        receipt = json.loads(Path(receipt_artifact["path"]).read_text())
        self.assertEqual(receipt["lead"]["mode"], "headless")
        self.assertEqual(receipt["lead"]["disposition"], "accept")
        self.assertEqual(
            [item["role"] for item in receipt["attempts"]],
            ["implementer", "reviewer"],
        )
        self.assertEqual(len(receipt["lead"]["attempts"]), 1)
        self.assertEqual(receipt["accounting"]["worker_invocations"], 3)

    def test_rate_limited_implementer_uses_frozen_same_permission_fallback(self):
        task = self.delivery_task()
        task["budget"]["max_fallbacks_per_step"] = 1
        fixture = {
            **self.implementation_fixture(),
            "fail_profile_ids": ["fixture-implementer"],
        }
        service = Service(self.runtime)
        started = service.start(
            task,
            "implementation-fallback",
            _internal_implementation_fixture=fixture,
            _internal_review_fixture=self.clean_review_fixture(),
        )
        self.wait_for_candidate(service, started["run_id"])
        service.resume(started["run_id"])
        waiting = self.wait_for_handoff(service, started["run_id"])
        claimed = service.handoff_claim(
            started["run_id"], waiting["version"], "fallback-host",
        )
        packet = claimed["handoff"]["packet"]
        service.handoff_complete(
            started["run_id"], claimed["claim"],
            self.decision(packet, "accept-fallback", "accept", "Fallback accepted."),
        )
        receipt_artifact = next(
            item for item in service.result(started["run_id"])["artifacts"]
            if item["name"] == "receipt.json"
        )
        receipt = json.loads(Path(receipt_artifact["path"]).read_text())
        implementers = [
            item for item in receipt["attempts"] if item["role"] == "implementer"
        ]
        self.assertEqual(len(implementers), 2)
        self.assertEqual(implementers[0]["status"], "failed")
        self.assertEqual(implementers[0]["error"]["error"], "RATE_LIMITED")
        self.assertEqual(
            [item["selected_profile"]["profile_id"] for item in implementers],
            ["fixture-implementer", "fixture-implementer-fallback"],
        )
        self.assertEqual(
            {item["selected_profile"]["profile"]["permission_policy"]
             for item in implementers},
            {"workspace_write"},
        )

    def test_cancelled_repair_retains_prior_candidate_attempts_and_disposition(self):
        fixtures = self.repair_fixtures()
        fixtures["iterations"][1]["delay_seconds"] = 5
        service = Service(self.runtime)
        started = service.start(
            self.delivery_task(),
            "cancelled-repair",
            _internal_implementation_fixture=fixtures,
            _internal_review_fixture=self.clean_review_fixture(),
        )
        self.wait_for_candidate(service, started["run_id"])
        service.resume(started["run_id"])
        waiting = self.wait_for_handoff(service, started["run_id"])
        claimed = service.handoff_claim(
            started["run_id"], waiting["version"], "cancel-repair-host",
        )
        packet = claimed["handoff"]["packet"]
        service.handoff_complete(
            started["run_id"], claimed["claim"],
            self.decision(packet, "cancel-repair", "revise", "Repair then cancel."),
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = service.status(started["run_id"])
            if status["state"] == "running":
                break
            time.sleep(0.02)
        else:
            self.fail("repair implementer never entered running state")
        service.cancel(started["run_id"])
        terminal = self.wait_for_terminal(service, started["run_id"])
        self.assertEqual(terminal["state"], "cancelled")
        receipt_artifact = next(
            item for item in service.result(started["run_id"])["artifacts"]
            if item["name"] == "receipt.json"
        )
        receipt = json.loads(Path(receipt_artifact["path"]).read_text())
        self.assertEqual(receipt["workflow"], "issue-delivery")
        self.assertEqual(
            [item["disposition"] for item in receipt["dispositions"]], ["revise"],
        )
        self.assertEqual(
            [item["role"] for item in receipt["attempts"]],
            ["implementer", "reviewer", "implementer"],
        )
        self.assertEqual(receipt["attempts"][-1]["status"], "cancelled")
        self.assertEqual(receipt["candidate"]["sha256"], packet["candidate_sha256"])

    def test_killed_repair_supervisor_never_launches_a_duplicate_writer(self):
        fixtures = self.repair_fixtures()
        fixtures["iterations"][1]["delay_seconds"] = 3
        service = Service(self.runtime)
        started = service.start(
            self.delivery_task(),
            "killed-repair-supervisor",
            _internal_implementation_fixture=fixtures,
            _internal_review_fixture=self.clean_review_fixture(),
        )
        self.wait_for_candidate(service, started["run_id"])
        service.resume(started["run_id"])
        waiting = self.wait_for_handoff(service, started["run_id"])
        claimed = service.handoff_claim(
            started["run_id"], waiting["version"], "kill-repair-host",
        )
        packet = claimed["handoff"]["packet"]
        service.handoff_complete(
            started["run_id"], claimed["claim"],
            self.decision(packet, "kill-repair", "revise", "Repair candidate."),
        )
        deadline = time.monotonic() + 10
        attempt = None
        while time.monotonic() < deadline:
            store = Store(service.database, service.artifacts)
            try:
                current = store.attempt(started["run_id"])
                if (current and current["status"] == "running"
                        and current["role"] == "implementer"
                        and Path(current["child_record"]).is_file()):
                    attempt = current
                    break
            finally:
                store.close()
            time.sleep(0.02)
        if attempt is None:
            self.fail("repair writer did not publish its child identity")
        os.kill(attempt["pid"], signal.SIGKILL)
        time.sleep(0.1)
        recovered = Service(self.runtime).resume(
            started["run_id"],
            {"attempt_id": attempt["id"], "disposition": "retain_ownership"},
        )
        self.assertFalse(recovered["launched"])
        self.assertEqual(recovered["disposition"], "retain_ownership")
        store = Store(service.database, service.artifacts)
        try:
            attempts = store.attempts_for_run(started["run_id"])
            self.assertEqual(
                [item["role"] for item in attempts],
                ["implementer", "reviewer", "implementer"],
            )
        finally:
            store.close()
        cancelled = service.cancel(started["run_id"])
        self.assertEqual(cancelled["state"], "cancelled")
        self.assert_source_unchanged()

    def test_live_implementer_cannot_be_resumed_into_a_second_writer(self):
        service = Service(self.runtime)
        started = service.start(
            self.delivery_task(),
            "one-writer-delivery",
            _internal_implementation_fixture=self.implementation_fixture(0.5),
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = service.status(started["run_id"])
            if status["state"] == "running":
                break
            time.sleep(0.02)
        else:
            self.fail("implementer did not enter running state")
        resumed = service.resume(started["run_id"])
        self.assertEqual(resumed["disposition"], "live")
        self.assertFalse(resumed["launched"])
        self.wait_for_candidate(service, started["run_id"])
        store = Store(service.database, service.artifacts)
        try:
            attempts = store.attempts_for_run(started["run_id"])
            self.assertEqual(len(attempts), 1)
            self.assertEqual(store.worker_invocations(started["run_id"]), 1)
        finally:
            store.close()
        self.assert_source_unchanged()

    def test_durable_out_of_scope_implementation_cannot_publish_a_candidate(self):
        service = Service(self.runtime)
        fixture = {
            "writes": [{"path": "README.md", "content": "unauthorized\n"}],
            "delay_seconds": 0,
        }
        started = service.start(
            self.delivery_task(),
            "out-of-scope-delivery",
            _internal_implementation_fixture=fixture,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status = service.status(started["run_id"])
            if status["state"] == "failed":
                break
            time.sleep(0.05)
        else:
            self.fail("out-of-scope delivery did not fail")
        result = service.result(started["run_id"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["state"], "failed")
        self.assertNotIn(
            "candidate-1.json", {item["name"] for item in result["artifacts"]},
        )
        self.assertTrue({
            "receipt.json", "receipt.md", "events.jsonl",
            "artifact-manifest.json", "result-receipt.json",
        } <= {item["name"] for item in result["artifacts"]})
        self.assert_source_unchanged()

    def test_revision_resume_after_prelaunch_crash_creates_one_repair_writer(self):
        service = Service(self.runtime)
        started = service.start(
            self.delivery_task(),
            "revision-prelaunch-crash",
            _internal_implementation_fixture=self.repair_fixtures(),
            _internal_review_fixture=self.clean_review_fixture(),
        )
        self.wait_for_candidate(service, started["run_id"])
        service.resume(started["run_id"])
        waiting = self.wait_for_handoff(service, started["run_id"])
        claimed = service.handoff_claim(
            started["run_id"], waiting["version"], "fixture-crash-host",
        )
        packet = claimed["handoff"]["packet"]
        original_spawn = service._spawn_daemon
        service._spawn_daemon = lambda *args, **kwargs: 0
        try:
            saved = service.handoff_complete(
                started["run_id"], claimed["claim"],
                self.decision(
                    packet, "repair-after-crash", "revise", "Repair candidate.",
                ),
            )
        finally:
            service._spawn_daemon = original_spawn
        self.assertTrue(saved["launched"])
        self.assertEqual(service.status(started["run_id"])["state"], "queued")

        resumed = Service(self.runtime).resume(started["run_id"])
        self.assertTrue(resumed["launched"])
        self.wait_for_candidate(service, started["run_id"])
        store = Store(service.database, service.artifacts)
        try:
            attempts = store.attempts_for_run(started["run_id"])
            self.assertEqual(
                [item["role"] for item in attempts],
                ["implementer", "reviewer", "implementer"],
            )
            self.assertEqual(len({item["id"] for item in attempts}), 3)
            snapshot = json.loads(store.run(started["run_id"])["mutable_snapshot"])
            self.assertEqual(len(snapshot["delivery_iterations"]), 2)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
