"""Public fixture/process Council gates against the shared R6 authority."""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import shlex
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "plugin/core/src"), str(ROOT / "test/core")]
import test_council_runtime as fixtures
from devsquad import cli, detached
from devsquad.contracts import ContractError
from devsquad.service import Service
from devsquad.store import ConflictError, Store, request_hash
from devsquad.supervisor import _live_group_exists


class CouncilReconciliationTest(unittest.TestCase):
    setUp = fixtures.CouncilRuntimeTest.setUp
    git = fixtures.CouncilRuntimeTest.git
    wait = fixtures.CouncilRuntimeTest.wait
    receipt = fixtures.CouncilRuntimeTest.receipt

    def host_run(self, key):
        self.task["lead"]["mode"] = "host"
        started = self.service.start(self.task, key, _internal_council_fixture=self.fixture)
        self.wait(started["run_id"], {"awaiting_host"})
        return started["run_id"]

    def invoke(self, argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main([*argv, "--runtime-dir", str(self.runtime)])
        return code, output.getvalue()

    def finish(self, run):
        return self.service.finish(run, "accept", "--frozen 'choice'", chosen="synthesis",
            supported_claims=["--no blind retry"], discarded_alternatives=["'blind' retry"], validation="--duplicate test")

    def test_human_omitted_id_explicit_choice_and_exact_retry_round_trip(self):
        run = self.host_run("normal-council-finish")
        code, output = self.invoke(["status", "--project-dir", str(self.repo)])
        self.assertEqual(code, 0, output)
        self.assertIn("Proposal A:", output)
        self.assertIn("Proposal B:", output)
        self.assertIn("Dissent retention", output)
        self.assertIn("explicit disposition, --choose", output)
        self.assertNotIn("--accept --reason=", output)  # no automatic choice
        code, output = self.invoke(["finish", "--project-dir", str(self.repo), "--accept", "--reason", "no choice"])
        self.assertEqual(code, 64, output)
        before = self.service.status(run)["version"]
        with patch.object(self.service, "handoff_complete", side_effect=RuntimeError("claim committed")):
            with self.assertRaises(RuntimeError):
                self.finish(run)
        version = self.service.status(run)["version"]
        self.assertEqual(version, before + 1)
        code, output = self.invoke(["status", run])
        self.assertEqual(code, 0, output)
        command = shlex.split(next(line[6:] for line in output.splitlines() if line.startswith("Next: ")))
        self.assertIn("--choose=synthesis", command)
        self.assertIn("--supported-claim=--no blind retry", command)
        code, output = self.invoke(["status", run, "--json"])
        self.assertNotIn("handoff_view", json.loads(output)["data"])
        code, output = self.invoke(command[1:])
        self.assertEqual(code, 0, output)
        self.assertEqual(self.receipt(run)["lead"]["choice"]["validation"], "--duplicate test")
        code, output = self.invoke(["result", "--project-dir", str(self.repo)])
        self.assertEqual(code, 0, output)
        self.assertIn("receipt.json:", output)

    def test_host_expiry_rejection_is_audited_then_recovers_exact_intent(self):
        run = self.host_run("host-expiry-at-submission")
        captured = {}
        original_complete = self.service.handoff_complete
        def expire(run_id, claim, decision):
            captured.update(claim=claim, decision=decision)
            captured["later"] = datetime.fromisoformat(claim["expires_at"]) + timedelta(seconds=1)
            with patch("devsquad.store._authoritative_now", return_value=captured["later"]):
                return original_complete(run_id, claim, decision)
        with patch.object(self.service, "handoff_complete", side_effect=expire):
            with self.assertRaisesRegex(ConflictError, "expired_claim"):
                self.finish(run)
        store = self.service._store()
        try:
            rejected = dict(store.connection.execute("SELECT * FROM handoff_submissions WHERE handoff_id=?", (captured["claim"]["handoff_id"],)).fetchone())
            marker = store.terminal_finish_decision(run, captured["claim"]["handoff_id"])
            self.assertEqual(marker, captured["decision"])
        finally:
            store.close()
        with patch("devsquad.store._authoritative_now", return_value=captured["later"]):
            with self.assertRaisesRegex(ConflictError, "different terminal finish intent"):
                self.service.finish(run, "reject", "changed intent", chosen="A", validation="check")
            self.assertEqual(Service(self.runtime).resume(run)["state"], "succeeded")
        store = self.service._store()
        try:
            events = store.events_for_run(run)
            recovered = [e for e in events if e["type"] == "handoff.completion_recovered"]
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0]["payload"]["rejected_submission"], rejected)
            self.assertEqual(recovered[0]["payload"]["rejected_submission_sha256"], request_hash(rejected))
            self.assertEqual(recovered[0]["payload"]["fencing_token"], captured["claim"]["fencing_token"] + 1)
            self.assertEqual(len(store.outcomes_for_run(run)), 1)
        finally:
            store.close()

    def test_guided_finish_never_adopts_app_claim_even_same_owner_expired(self):
        run = self.host_run("app-claim-no-terminal-authority")
        claim = self.service.handoff_claim(run, self.service.status(run)["version"], "terminal-operator")["claim"]
        for now in (datetime.now(timezone.utc), datetime.fromisoformat(claim["expires_at"]) + timedelta(seconds=1)):
            version = self.service.status(run)["version"]
            with patch("devsquad.store._authoritative_now", return_value=now):
                with self.assertRaisesRegex(ConflictError, "already has a host claim"):
                    self.finish(run)
                with self.assertRaisesRegex(ConflictError, "no exact current"):
                    Service(self.runtime).resume(run)
            self.assertEqual(self.service.status(run)["version"], version)
        self.service.cancel(run)

    def controlled_stage(self, run_id):
        # Scheduling seam only: public start + real durable subprocess workers;
        # coordinator auto-resume is suppressed at the exact durable barrier.
        store = self.service._store()
        try:
            run = store.run(run_id)
        finally:
            store.close()
        with patch.dict(os.environ, {"PYTHONPATH": run["package_path"]}), patch.object(Service, "resume", return_value={}):
            self.assertEqual(detached.main(["--database", str(self.service.database), "--artifacts", str(self.service.artifacts),
                "--run-id", run_id, "--expected-version", str(run["version"]), "--package-digest", run["package_digest"]]), 0)

    def headless_at_imported_lead(self, key):
        self.task["lead"]["mode"] = "headless"
        with patch.object(Service, "_spawn_daemon", return_value=0):
            started = self.service.start(self.task, key, _internal_council_fixture=self.fixture)
            for _ in range(3):
                self.controlled_stage(started["run_id"])
            self.assertEqual(self.service.resume(started["run_id"])["state"], "queued")
            self.controlled_stage(started["run_id"])
        return started["run_id"]

    def test_headless_claim_and_submitted_crashes_recover_without_extra_workers(self):
        for boundary in ("record_handoff_submission", "complete_handoff_terminal"):
            for expired in (False, True):
                with self.subTest(boundary=boundary, expired=expired):
                    run = self.headless_at_imported_lead(f"headless-{boundary}-{expired}")
                    with patch.object(Store, boundary, side_effect=RuntimeError("after durable boundary")):
                        with self.assertRaises(RuntimeError):
                            self.service.resume(run)
                    store = self.service._store()
                    try:
                        handoff = store.handoff_snapshot(run)
                        old = dict(store.connection.execute("SELECT * FROM claims WHERE run_id=?", (run,)).fetchone())
                        submitted = store.recorded_handoff_submission(run, handoff.handoff_id)
                    finally:
                        store.close()
                    future = datetime.fromisoformat(old["lease_expires_at"]) + timedelta(seconds=1)
                    class FutureDateTime(datetime):
                        @classmethod
                        def now(cls, tz=None):
                            return future
                    with contextlib.ExitStack() as stack:
                        if expired:
                            stack.enter_context(patch("devsquad.council_runtime.datetime", FutureDateTime))
                            stack.enter_context(patch("devsquad.store._authoritative_now", return_value=future))
                        result = Service(self.runtime).resume(run)
                    self.assertEqual(result["state"], "succeeded")
                    receipt = self.receipt(run)
                    self.assertEqual(receipt["worker_invocations"], 4)
                    self.assertEqual(receipt["identity_scope"], "all_fixture")
                    store = self.service._store()
                    try:
                        attempts = store.attempts_for_run(run)
                        self.assertTrue(all(not _live_group_exists(a["pgid"]) for a in attempts))
                        latest = dict(store.connection.execute("SELECT * FROM claims WHERE run_id=?", (run,)).fetchone())
                        # Recorded submissions need no new lease or fence.
                        self.assertEqual(latest["fencing_token"], old["fencing_token"] + int(expired and submitted is None))
                        self.assertFalse(latest["active"])
                        self.assertEqual(len(store.outcomes_for_run(run)), 1)
                    finally:
                        store.close()

    def test_headless_expired_submission_is_retained_and_new_fence_completes_saved_lead(self):
        run = self.headless_at_imported_lead("headless-expiry-at-submission")
        with self.assertRaisesRegex(ConflictError, "does not accept a host claim"):
            self.service.handoff_claim(run, self.service.status(run)["version"], "app-owner")
        captured = {}
        original_record = Store.record_handoff_submission
        def expire(store, run_id, claim, decision, **kwargs):
            captured.update(claim=claim, decision=decision)
            captured["later"] = datetime.fromisoformat(claim.expires_at) + timedelta(seconds=1)
            return original_record(store, run_id, claim, decision, now=captured["later"])
        with patch.object(Store, "record_handoff_submission", new=expire):
            with self.assertRaisesRegex(ConflictError, "expired_claim"):
                self.service.resume(run)
        future = captured["later"]
        class FutureDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return future
        with patch("devsquad.council_runtime.datetime", FutureDateTime), patch("devsquad.store._authoritative_now", return_value=future):
            self.assertEqual(Service(self.runtime).resume(run)["state"], "succeeded")
        store = self.service._store()
        try:
            submissions = [dict(row) for row in store.connection.execute("SELECT * FROM handoff_submissions WHERE handoff_id=?", (captured["claim"].handoff_id,))]
            self.assertEqual(len(submissions), 2)
            rejected = next(row for row in submissions if row["outcome"] == "rejected")
            recorded = next(row for row in submissions if row["outcome"] == "recorded")
            self.assertEqual(rejected["rejection_code"], "expired_claim")
            self.assertEqual(recorded["fencing_token"], rejected["fencing_token"] + 1)
            self.assertNotEqual(recorded["submission_id"], rejected["submission_id"])
            self.assertEqual(json.loads(recorded["decision_json"])["council_choice"], captured["decision"]["council_choice"])
            self.assertEqual(len(store.attempts_for_run(run)), 4)
            self.assertEqual(len(store.outcomes_for_run(run)), 1)
            self.assertTrue(all(not _live_group_exists(a["pgid"]) for a in store.attempts_for_run(run)))
            with self.assertRaisesRegex(ConflictError, "expired_claim"):
                store.record_handoff_submission(run, captured["claim"], captured["decision"])
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
