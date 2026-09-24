from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "plugin" / "core"
sys.path.insert(0, str(CORE / "src"))

from devsquad.contracts import ContractError
from devsquad.service import Service
from devsquad.store import Store
from devsquad.workspaces import (
    freeze_delivery_candidate,
    prepare_delivery_workspace,
    resolve_commit,
)


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
                    "fixture-reviewer",
                    family="fixture-family-b",
                    model="fixture-review-model",
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
                    {"kind": "profile", "id": "fixture-implementer"}
                ],
                "reviewer": [
                    {"kind": "profile", "id": "fixture-reviewer"}
                ],
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
                "fixture-reviewer-subscription": {
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
    def clean_review_fixture() -> dict[str, object]:
        return {
            "verdict": "clean",
            "summary": "The exact frozen candidate satisfies the task.",
            "findings": [],
        }

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
        self.assert_source_unchanged()


if __name__ == "__main__":
    unittest.main()
