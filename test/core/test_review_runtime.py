from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.service import Service
from devsquad.store import Store
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
            self.assertTrue({
                "review.json", "checks.json", "evaluation.json", "review-attempt.json",
            } <= names)
            self.assertNotIn("result-receipt.json", names)
            handoff = store.handoff_snapshot(started["run_id"])
        finally:
            store.close()

        packet = handoff.packet
        self.assertEqual(packet["candidate_sha256"], snapshot["workspace"]["candidate_sha256"])
        self.assertEqual(packet["review"]["findings"][0]["id"], "F-1")
        self.assertEqual(packet["checks"][0]["status"], "failed")
        self.assertFalse(packet["checks"][0]["required_to_pass"])
        self.assertTrue(packet["evaluation"]["accept_allowed"])
        self.assertEqual(packet["evaluation"]["report_only_failures"], ["fixture-tests"])
        self.assertEqual(
            {reference["name"] for reference in packet["artifacts"]},
            {"review.json", "checks.json", "evaluation.json", "review-attempt.json"},
        )
        self.assertTrue(all(reference["artifact_id"] for reference in packet["artifacts"]))

        claimed = self.service.handoff_claim(
            started["run_id"], waiting["version"], "host-terminal",
        )
        self.assertEqual(claimed["handoff"]["packet"], packet)
        self.assertFalse(self.service.result(started["run_id"])["ready"])

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
