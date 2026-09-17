from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.contracts import ContractError
from devsquad.service import Service
from devsquad.store import ConflictError, Store, request_hash
from devsquad_test_fixtures import branch_review_routing_documents


class DurableBranchReviewTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="devsquad-review-runtime-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.runtime = self.root / "runtime"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.name", "Test"],
            check=True,
        )
        (self.repo / "src").mkdir()
        (self.repo / "tests").mkdir()
        (self.repo / "devsquad").mkdir()
        (self.repo / "src/app.py").write_text("VALUE = 'base'\n")
        (self.repo / "tests/test_app.py").write_text("# fixture\n")
        profiles, policy = branch_review_routing_documents()
        (self.repo / "devsquad/profiles.json").write_text(profiles)
        (self.repo / "devsquad/policy.json").write_text(policy)
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "base"], check=True)
        self.base = self.git_text("rev-parse", "HEAD").strip()
        (self.repo / "src/app.py").write_text("VALUE = 'candidate'\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "src/app.py"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-qm", "candidate"],
            check=True,
        )
        self.target = self.git_text("rev-parse", "HEAD").strip()
        self.task = json.loads(
            (ROOT / "docs/plans/engineering-team/examples/branch-review.json").read_text()
        )
        self.task["project"] = {
            "repo_path": str(self.repo),
            "base_ref": self.base,
            "target_ref": self.target,
        }
        self.task["checks"] = [{
            "id": "fixture-tests",
            "argv": [sys.executable, "-c", "print('reported failure'); raise SystemExit(7)"],
            "cwd": ".",
            "timeout_seconds": 10,
            "required_to_pass": False,
        }]
        self.service = Service(self.runtime)
        self.fixture = {
            "verdict": "findings",
            "summary": "The candidate changes the configured value.",
            "findings": [{
                "id": "F-1",
                "severity": "medium",
                "title": "Changed behavior needs confirmation",
                "description": "The new value differs from the baseline.",
                "path": "src/app.py",
                "start_line": 1,
                "end_line": 1,
                "evidence": "The target contains VALUE = 'candidate'.",
            }],
        }

    def git_bytes(self, *arguments):
        return subprocess.run(
            ["git", "-C", str(self.repo), *arguments],
            check=True,
            capture_output=True,
        ).stdout

    def git_text(self, *arguments):
        return self.git_bytes(*arguments).decode()

    def wait_state(self, run_id, states, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.service.status(run_id)
            if status["state"] in states:
                return status
            time.sleep(0.05)
        log = self.runtime / "private-logs" / f"{run_id}.supervisor.log"
        detail = log.read_text() if log.exists() else "no supervisor log"
        self.fail(f"run did not reach {states}: {self.service.status(run_id)}\n{detail}")

    @staticmethod
    def decision(packet, submission_id, disposition, reason):
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

    def start_waiting(self, key):
        started = self.service.start(
            self.task,
            key,
            _internal_review_fixture=self.fixture,
        )
        self.assertTrue(started["created"])
        waiting = self.wait_state(started["run_id"], {"awaiting_host", "failed"})
        self.assertEqual(waiting["state"], "awaiting_host")
        return started["run_id"], waiting

    def test_detached_review_imports_bound_evidence_and_publishes_host_handoff(self):
        (self.repo / "notes.txt").write_text("unrelated local work\n")
        before_head = self.git_bytes("rev-parse", "HEAD")
        before_status = self.git_bytes("status", "--porcelain=v1", "-z")
        before_index = hashlib.sha256((self.repo / ".git/index").read_bytes()).hexdigest()

        started = self.service.start(
            self.task,
            "durable-review",
            _internal_review_fixture=self.fixture,
        )
        self.assertTrue(started["created"])
        waiting = self.wait_state(started["run_id"], {"awaiting_host", "failed"})
        self.assertEqual(waiting["state"], "awaiting_host")
        self.assertEqual(waiting["next_action"], "claim_handoff")
        self.assertEqual(
            (self.git_bytes("rev-parse", "HEAD"),
             self.git_bytes("status", "--porcelain=v1", "-z"),
             hashlib.sha256((self.repo / ".git/index").read_bytes()).hexdigest()),
            (before_head, before_status, before_index),
        )

        store = Store(self.runtime / "state.sqlite3", self.runtime / "artifacts")
        try:
            run = store.run(started["run_id"])
            snapshot = json.loads(run["mutable_snapshot"])
            self.assertEqual(run["worktree_path"], snapshot["workspace"]["path"])
            self.assertNotEqual(
                snapshot["workspace"]["path"], snapshot["check_workspace"]["path"],
            )
            attempts = store.connection.execute(
                "SELECT status FROM attempts WHERE run_id=?", (started["run_id"],),
            ).fetchall()
            self.assertEqual([row["status"] for row in attempts], ["finished"])
            artifacts = store.result_snapshot(started["run_id"])[1]
            names = {item["name"] for item in artifacts}
            attempt_id = store.attempt(started["run_id"])["id"]
            self.assertTrue({
                f"review-{attempt_id}.json",
                f"checks-{attempt_id}.json",
                f"evaluation-{attempt_id}.json",
                f"review-attempt-{attempt_id}.json",
            } <= names)
            self.assertNotIn("result-receipt.json", names)
            handoff = store.handoff_snapshot(started["run_id"])
        finally:
            store.close()

        packet = handoff.packet
        self.assertEqual(packet["attempt_id"], attempt_id)
        self.assertEqual(packet["candidate_sha256"], snapshot["workspace"]["candidate_sha256"])
        self.assertEqual(packet["review"]["findings"][0]["id"], "F-1")
        self.assertEqual(packet["checks"][0]["status"], "failed")
        self.assertFalse(packet["checks"][0]["required_to_pass"])
        self.assertTrue(packet["evaluation"]["accept_allowed"])
        self.assertEqual(packet["evaluation"]["report_only_failures"], ["fixture-tests"])
        self.assertEqual(
            len(packet["artifacts"]),
            4,
        )
        self.assertTrue(all(reference["artifact_id"] for reference in packet["artifacts"]))

        claimed = self.service.handoff_claim(
            started["run_id"], waiting["version"], "host-terminal",
        )
        self.assertEqual(claimed["handoff"]["packet"], packet)
        self.assertFalse(self.service.result(started["run_id"])["ready"])

        decision = self.decision(packet, "accept-review", "accept", "Evidence accepted.")
        completed = self.service.handoff_complete(
            started["run_id"], claimed["claim"], decision,
        )
        self.assertEqual((completed["state"], completed["phase"]), ("succeeded", None))
        self.assertEqual(completed["continuation"]["action"], "terminal")
        self.assertFalse(completed["launched"])
        result = self.service.result(started["run_id"])
        self.assertTrue(result["ready"])
        names = {artifact["name"] for artifact in result["artifacts"]}
        self.assertTrue({
            "receipt.json", "receipt.md", "events.jsonl",
            "artifact-manifest.json", "result-receipt.json",
        } <= names)
        receipt_artifact = next(
            artifact for artifact in result["artifacts"]
            if artifact["name"] == "receipt.json"
        )
        receipt = json.loads(Path(receipt_artifact["path"]).read_text())
        self.assertEqual(receipt["state"], "succeeded")
        self.assertEqual(receipt["candidate"]["sha256"], packet["candidate_sha256"])
        self.assertEqual(receipt["evaluation"]["report_only_failures"], ["fixture-tests"])
        self.assertEqual(receipt["accounting"]["worker_invocations"], 1)
        self.assertIsNone(receipt["accounting"]["native_model_requests"])
        self.assertFalse(receipt["accounting"]["host_usage_measured"])
        self.assertEqual(receipt["lead"]["usage"]["source"], "unavailable")
        self.assertFalse(receipt["events_export"]["includes_terminal_event"])
        self.assertIn("offline fixture", receipt["limitations"][0])
        replay = self.service.handoff_complete(
            started["run_id"], claimed["claim"], decision,
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["state"], "succeeded")
        self.assertEqual(
            (self.git_bytes("rev-parse", "HEAD"),
             self.git_bytes("status", "--porcelain=v1", "-z"),
             hashlib.sha256((self.repo / ".git/index").read_bytes()).hexdigest()),
            (before_head, before_status, before_index),
        )

    def test_required_failure_blocks_accept_before_record_then_allows_reject(self):
        self.task["checks"][0]["required_to_pass"] = True
        run_id, waiting = self.start_waiting("required-failure")
        claimed = self.service.handoff_claim(run_id, waiting["version"], "host-required")
        packet = claimed["handoff"]["packet"]
        accept = self.decision(packet, "blocked-accept", "accept", "Accept anyway.")
        with self.assertRaisesRegex(ContractError, "blocked by required evidence"):
            self.service.handoff_complete(run_id, claimed["claim"], accept)
        status = self.service.status(run_id)
        self.assertEqual((status["state"], status["phase"]), ("awaiting_host", None))

        reject = self.decision(packet, "required-reject", "reject", "Required check failed.")
        completed = self.service.handoff_complete(run_id, claimed["claim"], reject)
        self.assertEqual(completed["state"], "failed")
        result = self.service.result(run_id)
        receipt_artifact = next(
            artifact for artifact in result["artifacts"]
            if artifact["name"] == "receipt.json"
        )
        receipt = json.loads(Path(receipt_artifact["path"]).read_text())
        self.assertEqual(receipt["error"]["error"], "REVIEW_REJECTED")
        self.assertEqual(receipt["evaluation"]["required_failures"], ["fixture-tests"])

    def test_decision_must_bind_every_presented_evidence_artifact(self):
        run_id, waiting = self.start_waiting("missing-evidence")
        claimed = self.service.handoff_claim(run_id, waiting["version"], "host-evidence")
        packet = claimed["handoff"]["packet"]
        decision = self.decision(packet, "missing-ref", "accept", "Incomplete evidence.")
        decision["evidence_refs"].pop()
        body = {key: value for key, value in decision.items() if key != "submission_hash"}
        decision["submission_hash"] = request_hash(body)
        with self.assertRaisesRegex(ContractError, "bind every presented"):
            self.service.handoff_complete(run_id, claimed["claim"], decision)
        self.assertEqual(self.service.status(run_id)["handoff"]["status"], "open")

    def test_revision_rechecks_clean_workspace_and_preserves_both_attempts(self):
        self.task["budget"]["max_revisions"] = 1
        self.task["budget"]["max_worker_invocations"] = 3
        self.task["checks"] = [{
            "id": "dirtying-check",
            "argv": [
                sys.executable,
                "-c",
                "from pathlib import Path; p=Path('generated.tmp'); "
                "assert not p.exists(); p.write_text('generated')",
            ],
            "cwd": ".",
            "timeout_seconds": 10,
            "required_to_pass": True,
        }]
        run_id, first_wait = self.start_waiting("one-revision")
        first_claim = self.service.handoff_claim(
            run_id, first_wait["version"], "host-revision-one",
        )
        first_packet = first_claim["handoff"]["packet"]
        revise = self.decision(
            first_packet, "request-revision", "revise", "Repeat the frozen review.",
        )
        requeued = self.service.handoff_complete(run_id, first_claim["claim"], revise)
        self.assertEqual(requeued["continuation"]["action"], "requeued")
        self.assertTrue(requeued["launched"])

        second_wait = self.wait_state(run_id, {"awaiting_host", "failed"})
        self.assertEqual(second_wait["state"], "awaiting_host")
        self.assertEqual(second_wait["handoff"]["sequence"], 2)
        second_claim = self.service.handoff_claim(
            run_id, second_wait["version"], "host-revision-two",
        )
        second_packet = second_claim["handoff"]["packet"]
        self.assertNotEqual(first_packet["attempt_id"], second_packet["attempt_id"])
        self.assertTrue(all(result["status"] == "passed" for result in second_packet["checks"]))
        self.assertTrue(
            {reference["name"] for reference in first_packet["artifacts"]}.isdisjoint(
                {reference["name"] for reference in second_packet["artifacts"]}
            )
        )
        accept = self.decision(
            second_packet, "accept-revision", "accept", "Second review accepted.",
        )
        completed = self.service.handoff_complete(run_id, second_claim["claim"], accept)
        self.assertEqual(completed["state"], "succeeded")
        receipt_artifact = next(
            artifact for artifact in self.service.result(run_id)["artifacts"]
            if artifact["name"] == "receipt.json"
        )
        receipt = json.loads(Path(receipt_artifact["path"]).read_text())
        self.assertEqual(len(receipt["attempts"]), 2)
        self.assertEqual(
            [item["disposition"] for item in receipt["dispositions"]],
            ["revise", "accept"],
        )
        self.assertEqual(receipt["revisions"]["executed"], 1)
        late = self.decision(
            first_packet, "late-first-host", "accept", "This claim is stale.",
        )
        with self.assertRaises(ConflictError):
            self.service.handoff_complete(run_id, first_claim["claim"], late)

    def test_zero_revision_budget_turns_revise_into_terminal_failure(self):
        self.task["budget"]["max_revisions"] = 0
        run_id, waiting = self.start_waiting("no-revisions")
        claimed = self.service.handoff_claim(run_id, waiting["version"], "host-no-revision")
        packet = claimed["handoff"]["packet"]
        revise = self.decision(packet, "revise-exhausted", "revise", "Try again.")
        completed = self.service.handoff_complete(run_id, claimed["claim"], revise)
        self.assertEqual(completed["state"], "failed")
        receipt_artifact = next(
            artifact for artifact in self.service.result(run_id)["artifacts"]
            if artifact["name"] == "receipt.json"
        )
        receipt = json.loads(Path(receipt_artifact["path"]).read_text())
        self.assertEqual(receipt["error"]["error"], "BUDGET_EXHAUSTED")
        self.assertEqual(receipt["lead"]["disposition"], "revise")

    def test_resume_finishes_submission_recorded_before_continuation(self):
        run_id, waiting = self.start_waiting("resume-submission")
        claimed = self.service.handoff_claim(run_id, waiting["version"], "host-crash")
        packet = claimed["handoff"]["packet"]
        decision = self.decision(packet, "saved-before-crash", "accept", "Accept evidence.")
        store = Store(self.runtime / "state.sqlite3", self.runtime / "artifacts")
        try:
            store.record_handoff_submission(
                run_id,
                self.service._decode_claim(claimed["claim"]),
                decision,
            )
        finally:
            store.close()
        self.assertEqual(self.service.status(run_id)["phase"], "handoff_submitted")
        resumed = self.service.resume(run_id)
        self.assertEqual((resumed["state"], resumed["disposition"]), ("succeeded", "terminal"))
        self.assertFalse(resumed["launched"])
        self.assertTrue(self.service.result(run_id)["ready"])

    def test_public_native_codex_driver_verifies_identity_usage_and_output(self):
        profiles = {
            "schema_version": 1,
            "profiles": [{
                "id": "native-codex-reviewer",
                "harness": "codex",
                "model_family": "gpt-fixture",
                "model_id": "gpt-fake-review",
                "effort": {"value": "low", "transport": "native"},
                "required_tools": ["read"],
                "permission_policy": "read_only",
                "account_pool_id": "codex-subscription",
                "billing_mode": "subscription",
                "quality_status": "proven",
                "evidence_refs": ["native-fixture"],
            }],
            "bindings": {
                "review.deep": {"profile_id": "native-codex-reviewer", "version": 1},
            },
        }
        policy_document = {
            "schema_version": 1,
            "id": "native-codex-policy",
            "version": 1,
            "roles": {"reviewer": [{"kind": "alias", "id": "review.deep"}]},
            "task_classes": {"fixture-review-small": "proven"},
            "require_different_model_for_review": True,
            "prefer_different_harness_for_review": True,
            "account_pools": {
                "codex-subscription": {
                    "allowed_billing_modes": ["subscription"],
                    "max_concurrency": 1,
                    "unknown_capacity_policy": "allow_bounded",
                },
            },
            "experiment_budget": {},
        }
        (self.repo / "devsquad/profiles.json").write_text(
            json.dumps(profiles, sort_keys=True) + "\n"
        )
        (self.repo / "devsquad/policy.json").write_text(
            json.dumps(policy_document, sort_keys=True) + "\n"
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "add", "devsquad"], check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-qm", "native review config"],
            check=True,
        )
        self.target = self.git_text("rev-parse", "HEAD").strip()
        self.task["project"]["target_ref"] = self.target
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir()
        (fake_bin / "codex").symlink_to(
            ROOT / "test/core/fakes/codex_review_cli.py"
        )
        fake_home = self.root / "fake-codex-home"
        fake_home.mkdir()
        (fake_home / "auth.json").write_text("{}\n")
        (fake_home / "auth.json").chmod(0o600)
        environment = {
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "CODEX_HOME": str(fake_home),
        }
        with patch.dict(os.environ, environment, clear=False):
            started = self.service.start(self.task, "native-codex-review")
        self.assertTrue(started["created"])
        waiting = self.wait_state(started["run_id"], {"awaiting_host", "failed"})
        self.assertEqual(waiting["state"], "awaiting_host")
        claimed = self.service.handoff_claim(
            started["run_id"], waiting["version"], "native-host",
        )
        packet = claimed["handoff"]["packet"]
        observed = packet["attempt"]["observed_identity"]
        self.assertEqual(
            (observed["harness"], observed["harness_version"], observed["model_id"]),
            ("codex", "codex-cli 0.153.4", "gpt-fake-review"),
        )
        self.assertEqual(packet["attempt"]["usage"], {
            "input_tokens": 120,
            "output_tokens": 40,
            "total_tokens": 160,
            "source": "native_reported",
        })
        self.assertEqual(packet["review"]["verdict"], "clean")
        decision = self.decision(packet, "accept-native", "accept", "Native review accepted.")
        completed = self.service.handoff_complete(
            started["run_id"], claimed["claim"], decision,
        )
        self.assertEqual(completed["state"], "succeeded")
        receipt_artifact = next(
            artifact for artifact in self.service.result(started["run_id"])["artifacts"]
            if artifact["name"] == "receipt.json"
        )
        receipt = json.loads(Path(receipt_artifact["path"]).read_text())
        self.assertEqual(receipt["accounting"]["attempt_usage"][0]["total_tokens"], 160)
        self.assertEqual(receipt["limitations"], [])

    def test_native_codex_faults_never_become_valid_reviews(self):
        profiles_text, policy_text = branch_review_routing_documents()
        profiles = json.loads(profiles_text)
        profile = profiles["profiles"][0]
        profile.update({
            "harness": "codex",
            "model_family": "gpt-fixture",
            "required_tools": ["read"],
            "permission_policy": "read_only",
        })
        fake_bin = self.root / "fault-bin"
        fake_bin.mkdir()
        (fake_bin / "codex").symlink_to(
            ROOT / "test/core/fakes/codex_review_cli.py"
        )
        fake_home = self.root / "fault-codex-home"
        fake_home.mkdir()
        (fake_home / "auth.json").write_text("{}\n")
        (fake_home / "auth.json").chmod(0o600)
        environment = {
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "CODEX_HOME": str(fake_home),
        }
        for mode in ("malformed", "denied", "disconnect", "identity-drift"):
            with self.subTest(mode=mode):
                profile["model_id"] = f"gpt-fake-{mode}"
                (self.repo / "devsquad/profiles.json").write_text(
                    json.dumps(profiles, sort_keys=True) + "\n"
                )
                (self.repo / "devsquad/policy.json").write_text(policy_text)
                subprocess.run(
                    ["git", "-C", str(self.repo), "add", "devsquad"], check=True,
                )
                subprocess.run(
                    ["git", "-C", str(self.repo), "commit", "-qm", f"fault {mode}"],
                    check=True,
                )
                target = self.git_text("rev-parse", "HEAD").strip()
                self.task["project"]["target_ref"] = target
                with patch.dict(os.environ, environment, clear=False):
                    started = self.service.start(self.task, f"native-fault-{mode}")
                self.assertEqual(started["state"], "queued")
                failed = self.wait_state(started["run_id"], {"awaiting_host", "failed"})
                self.assertEqual(failed["state"], "failed")
                self.assertIsNone(failed["handoff"])
                result = self.service.result(started["run_id"])
                self.assertTrue(result["ready"])
                self.assertNotIn(
                    "receipt.json", {artifact["name"] for artifact in result["artifacts"]},
                )

    def test_invalid_internal_review_fails_before_launch_with_a_receipt(self):
        invalid = dict(self.fixture, verdict="clean")
        started = self.service.start(
            self.task,
            "invalid-review",
            _internal_review_fixture=invalid,
        )
        self.assertEqual(started["state"], "failed")
        self.assertEqual(started["error"]["error"], "PREPARATION_FAILED")
        self.assertIn("verdict and findings disagree", started["error"]["message"])
        result = self.service.result(started["run_id"])
        self.assertTrue(result["ready"])
        self.assertEqual(
            [artifact["name"] for artifact in result["artifacts"]],
            ["result-receipt.json"],
        )


if __name__ == "__main__":
    unittest.main()
