from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.contracts import ContractError
from devsquad.service import Service
from devsquad.store import ConflictError, Store, request_hash


class HandoffServiceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="devsquad-handoff-service-")
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
        (self.repo / "README").write_text("base\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "README"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "base"], check=True)
        self.service = Service(self.runtime)

    def waiting_run(self, key: str = "handoff") -> tuple[str, dict, int]:
        packet = {
            "schema_version": 1,
            "candidate_sha256": "c" * 64,
            "instructions": "Review the frozen candidate.",
        }
        store = Store(self.runtime / "state.sqlite3", self.runtime / "artifacts")
        try:
            claim = store.claim_start(self.repo, key, {"task": key}, "preflight")
            version = store.complete_preparation(
                claim.run_id,
                claim.fencing_token,
                {"base_oid": "a" * 40, "target_oid": "b" * 40},
                package_path="/frozen/package",
                package_digest="package-digest",
            )
            reservation = store.reserve_attempt(
                claim.run_id, version, "supervisor", "package-digest",
            )
            version = store.mark_attempt_running(
                reservation, 101, 101, "process-start-id",
            )
            snapshot = store.publish_handoff(
                claim.run_id,
                version,
                reservation.attempt_token,
                reservation.supervisor_token,
                packet,
            )
            return claim.run_id, packet, snapshot.run_version
        finally:
            store.close()

    @staticmethod
    def decision(
        submission_id: str = "submission-1",
        disposition: str = "accept",
        reason: str = "accepted",
    ) -> dict:
        body = {
            "schema_version": 1,
            "submission_id": submission_id,
            "disposition": disposition,
            "reason": reason,
            "evidence_refs": [],
        }
        return {**body, "submission_hash": request_hash(body)}

    def test_status_claim_complete_and_exact_replay_share_one_saved_run(self):
        run_id, packet, version = self.waiting_run()
        waiting = self.service.status(run_id)
        self.assertEqual(
            (waiting["state"], waiting["phase"], waiting["version"], waiting["next_action"]),
            ("awaiting_host", None, version, "claim_handoff"),
        )
        self.assertEqual(waiting["handoff"]["status"], "open")
        self.assertNotIn("packet", waiting["handoff"])
        self.assertNotIn("fencing_token", waiting["handoff"])

        acquired = self.service.handoff_claim(run_id, version, "terminal-a")
        self.assertEqual(acquired["action"], "acquired")
        self.assertEqual(acquired["handoff"]["packet"], packet)
        self.assertEqual(acquired["handoff"]["claimed_by"], "terminal-a")
        public_claim = acquired["claim"]
        self.assertEqual(set(public_claim), {
            "schema_version", "run_id", "handoff_id", "owner",
            "fencing_token", "expires_at", "run_version",
        })

        renewed = self.service.handoff_claim(
            run_id, acquired["version"], "terminal-a", public_claim,
        )
        self.assertEqual(renewed["action"], "renewed")
        self.assertEqual(
            renewed["claim"]["fencing_token"], public_claim["fencing_token"],
        )
        with self.assertRaises(ConflictError):
            self.service.handoff_claim(
                run_id, renewed["version"], "terminal-a", public_claim,
            )

        decision = self.decision()
        completed = self.service.handoff_complete(run_id, renewed["claim"], decision)
        self.assertEqual(
            (completed["state"], completed["phase"], completed["disposition"]),
            ("awaiting_host", "handoff_submitted", "accept"),
        )
        self.assertFalse(completed["replayed"])
        replay = self.service.handoff_complete(run_id, renewed["claim"], decision)
        self.assertTrue(replay["replayed"])
        self.assertEqual(
            replay["recorded_run_version"], completed["recorded_run_version"],
        )

    def test_cancel_awaiting_host_is_terminal_and_keeps_replay_idempotent(self):
        run_id, _, version = self.waiting_run("cancel-wait")
        acquired = self.service.handoff_claim(run_id, version, "terminal-a")
        decision = self.decision()
        completed = self.service.handoff_complete(run_id, acquired["claim"], decision)
        cancelled = self.service.cancel(run_id)
        self.assertEqual(cancelled["state"], "cancelled")
        result = self.service.result(run_id)
        self.assertTrue(result["ready"])
        self.assertEqual(result["state"], "cancelled")
        replay = self.service.handoff_complete(run_id, acquired["claim"], decision)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["state"], "cancelled")
        self.assertEqual(
            replay["recorded_run_version"], completed["recorded_run_version"],
        )

    def test_public_claim_shape_and_run_binding_are_strict(self):
        run_id, _, version = self.waiting_run("strict-claim")
        with self.assertRaises(ContractError):
            self.service.handoff_complete(run_id, {"schema_version": 1}, self.decision())
        acquired = self.service.handoff_claim(run_id, version, "terminal-a")
        wrong_run = dict(acquired["claim"], run_id="different-run")
        with self.assertRaises(ConflictError):
            self.service.handoff_complete(run_id, wrong_run, self.decision())
        naive_expiry = dict(acquired["claim"], expires_at="2026-09-15T05:00:00")
        with self.assertRaises(ContractError):
            self.service.handoff_complete(run_id, naive_expiry, self.decision())

    def test_independent_cli_processes_claim_and_complete_the_saved_handoff(self):
        run_id, packet, version = self.waiting_run("cli-handoff")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "plugin/core/src")

        def invoke(arguments):
            result = subprocess.run(
                [sys.executable, "-P", "-m", "devsquad.cli", *arguments],
                cwd=self.root,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.stderr, "")
            self.assertEqual(result.returncode, 0, result.stdout)
            return json.loads(result.stdout)

        claimed = invoke([
            "handoff", "claim", run_id,
            "--expected-version", str(version),
            "--owner", "second-terminal",
            "--runtime-dir", str(self.runtime),
            "--json",
        ])
        self.assertTrue(claimed["ok"])
        self.assertEqual(claimed["data"]["handoff"]["packet"], packet)
        claim_file = self.root / "host-claim.json"
        claim_file.write_text(json.dumps(claimed["data"]["claim"]))
        decision = self.decision()
        decision_file = self.root / "host-decision.json"
        decision_file.write_text(json.dumps(decision))

        completed = invoke([
            "handoff", "complete", run_id,
            "--claim-file", str(claim_file),
            "--decision-file", str(decision_file),
            "--runtime-dir", str(self.runtime),
            "--json",
        ])
        self.assertTrue(completed["ok"])
        self.assertEqual(completed["data"]["phase"], "handoff_submitted")
        self.assertEqual(completed["data"]["submission_hash"], decision["submission_hash"])


if __name__ == "__main__":
    unittest.main()
