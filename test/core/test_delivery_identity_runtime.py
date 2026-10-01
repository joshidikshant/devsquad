"""Native execution identity gates through public durable delivery operations."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.contracts import ContractError
from devsquad.reports import TERMINAL_REPORT_NAMES
from devsquad.service import Service
from devsquad.store import Store, canonical_json
import test_delivery_workflow as delivery_fixtures


class PublicDeliveryIdentityTest(unittest.TestCase):
    MODEL = "claude-sonnet-4-6"
    FALLBACK_MODEL = "claude-opus-4-6"

    def setUp(self):
        # Compose the existing fixture instead of inheriting all its test cases.
        self.fixture = delivery_fixtures.DeliveryWorkspaceTest(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.repo = self.fixture.root, self.fixture.repo
        self.service = Service(self.fixture.runtime)
        self.run_ids = []
        self.fallbacks = False
        self.addCleanup(self.cancel_unfinished_runs)
        self.output = self.root / "native-result.json"
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir()
        self.fake_claude = fake_bin / "claude"
        self.fake_claude.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--version\" ]; then\n"
            "  printf '%s\\n' '2.1.220 (Claude Code)'\n"
            "  exit 0\n"
            "fi\n"
            "printf '%s\\n' \"VALUE = 'fixed'\" > src/app.py\n"
            f"/bin/cat {shlex.quote(str(self.output))}\n"
        )
        self.fake_claude.chmod(0o700)
        (fake_bin / "codex").symlink_to(ROOT / "test/core/fakes/codex_review_cli.py")
        fake_auth = self.root / "native-auth.json"
        fake_auth.write_text("{}\n")
        fake_auth.chmod(0o600)
        self.environment = {"PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        # Only preflight's file lookup is patched. Both worker binaries and all
        # durable service/import/review paths execute normally in child processes.
        self.auth_patch = patch(
            "devsquad.codex_review_worker._subscription_auth_file",
            return_value=fake_auth,
        )
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)

    def cancel_unfinished_runs(self):
        for run_id in self.run_ids:
            if self.service.status(run_id)["state"] not in {"succeeded", "failed", "cancelled"}:
                self.service.cancel(run_id)

    @staticmethod
    def counters():
        return {"inputTokens": 12, "outputTokens": 7}

    def native_result(self):
        return {
            "type": "result", "subtype": "success", "is_error": False,
            "result": "Applied the scoped native fixture implementation.",
            "session_id": "native-delivery-session",
            "usage": {"input_tokens": 12, "output_tokens": 7},
            "modelUsage": {self.MODEL: self.counters()},
        }

    def configure(self, *, requested_model=None, reviewer_model="gpt-fake-review", fallback=False):
        profiles, policy = self.fixture.delivery_routing_documents()
        self.fallbacks = fallback
        profiles["profiles"] = [
            profile for profile in profiles["profiles"]
            if fallback or profile["id"] != "fixture-implementer-fallback"
        ]
        by_id = {profile["id"]: profile for profile in profiles["profiles"]}
        implementer = by_id["fixture-implementer"]
        reviewer = by_id["fixture-reviewer"]
        lead = by_id["fixture-lead"]
        implementer.update({
            "harness": "claude", "model_family": "writer-family-label",
            "model_id": requested_model or self.MODEL,
            "effort": {"value": "high", "transport": "native"},
        })
        if fallback:
            by_id["fixture-implementer-fallback"].update({
                "harness": "claude", "model_family": "fallback-family-label",
                "model_id": self.FALLBACK_MODEL,
                "effort": {"value": "high", "transport": "native"},
            })
        reviewer.update({
            "harness": "codex", "model_family": "distinct-reviewer-label",
            "model_id": reviewer_model,
        })
        lead.update({"harness": "codex", "model_id": "gpt-fake-lead"})
        if not fallback:
            policy["roles"]["implementer"] = policy["roles"]["implementer"][:1]
        for name, document in (("profiles", profiles), ("policy", policy)):
            (self.repo / "devsquad" / f"{name}.json").write_text(json.dumps(document))
        self.fixture.git(self.repo, "add", "devsquad")
        self.fixture.git(self.repo, "commit", "-qm", "native identity fixture")
        self.fixture.baseline = self.fixture.git(self.repo, "rev-parse", "HEAD").strip()
        self.fixture.source_refs = self.fixture.git(self.repo, "show-ref")
        self.fixture.source_status = self.fixture.git(self.repo, "status", "--porcelain")

    def task(self, mode="host"):
        task = self.fixture.delivery_task()
        task["lead"] = {"mode": mode}
        task["budget"]["wall_seconds"] = 60
        task["budget"]["max_fallbacks_per_step"] = int(self.fallbacks)
        return task

    def start(self, document, key, *, mode="host"):
        self.output.write_text(json.dumps(document))
        with patch.dict(os.environ, self.environment):
            started = self.service.start(self.task(mode), key)
        self.run_ids.append(started["run_id"])
        self.assertEqual(started["state"], "queued", started)
        return started["run_id"]

    def wait(self, run_id, *, candidate=False):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status = self.service.status(run_id)
            if status["state"] in {"succeeded", "failed", "cancelled", "awaiting_host"}:
                return status
            if (candidate and status["state"] == "queued"
                    and status.get("next_action") == "resume_candidate_review"):
                return status
            time.sleep(0.05)
        self.fail(f"identity run did not reach its next gate: {self.service.status(run_id)}")

    def snapshot(self, run_id):
        store = Store(self.service.database, self.service.artifacts)
        try:
            return json.loads(store.run(run_id)["mutable_snapshot"])
        finally:
            store.close()

    def result_documents(self, run_id):
        result = self.service.result(run_id)
        artifacts = {item["name"]: item for item in result["artifacts"]}
        self.assertTrue(TERMINAL_REPORT_NAMES <= set(artifacts))
        receipt = json.loads(Path(artifacts["receipt.json"]["path"]).read_text())
        return receipt, artifacts

    def finish_valid_delivery(self, requested_model):
        self.configure(requested_model=requested_model)
        run_id = self.start(self.native_result(), f"valid-{requested_model}")
        self.assertEqual(self.wait(run_id, candidate=True)["state"], "queued")
        snapshot = self.snapshot(run_id)
        attempt = snapshot["delivery_iterations"][0]["implementation"]["attempt"]
        self.assertEqual(attempt["selected_profile"]["profile"]["model_id"], requested_model)
        self.assertEqual(attempt["selected_profile"]["profile"]["effort"]["value"], "high")
        self.assertEqual(attempt["observed_identity"]["model_id"], self.MODEL)
        self.assertIsNone(attempt["observed_identity"]["effort"])
        self.assertIsNone(attempt["observed_identity"]["backing_revision"])
        self.assertEqual(attempt["usage"]["total_tokens"], 19)
        self.assertEqual(attempt["native_ids"]["session_id"], "native-delivery-session")
        self.assertTrue(self.service.resume(run_id)["launched"])
        status = self.wait(run_id)
        self.assertEqual(status["state"], "awaiting_host", status)
        claimed = self.service.handoff_claim(run_id, status["version"], "identity-host")
        packet = claimed["handoff"]["packet"]
        completed = self.service.handoff_complete(
            run_id, claimed["claim"], self.fixture.decision(
                packet, "identity-accept", "accept", "Independent native review passed.",
            ),
        )
        self.assertEqual(completed["state"], "succeeded")
        receipt, artifacts = self.result_documents(run_id)
        self.assertEqual([item["role"] for item in receipt["attempts"]], ["implementer", "reviewer"])
        self.assertEqual(receipt["accounting"]["worker_invocations"], 2)
        self.assertIn("candidate-1.patch", artifacts)
        self.fixture.assert_source_unchanged()

    def test_exact_native_identity_completes_public_delivery(self):
        self.finish_valid_delivery(self.MODEL)

    def test_alias_resolves_to_observed_identity_through_public_delivery(self):
        self.finish_valid_delivery("sonnet")

    def test_invalid_identity_never_publishes_candidate_and_retains_failed_evidence(self):
        self.configure()
        documents = {}
        documents["missing"] = self.native_result()
        del documents["missing"]["modelUsage"]
        documents["multiple"] = self.native_result()
        documents["multiple"]["modelUsage"]["claude-haiku-4-5-20251001"] = self.counters()
        documents["mismatch"] = self.native_result()
        documents["mismatch"]["modelUsage"] = {"claude-opus-4-6": self.counters()}
        for kind, document in documents.items():
            with self.subTest(kind=kind):
                run_id = self.start(document, f"invalid-{kind}")
                status = self.wait(run_id, candidate=True)
                self.assertEqual(status["state"], "failed", status)
                self.assertIsNone(status["handoff"])
                receipt, artifacts = self.result_documents(run_id)
                self.assertNotIn("candidate-1.json", artifacts)
                self.assertNotIn("candidate-1.patch", artifacts)
                self.assertEqual(receipt["state"], "failed")
                self.assertEqual(receipt["accounting"]["worker_invocations"], 1)
                self.assertEqual(len(receipt["attempts"]), 1)
                failed_attempt = receipt["attempts"][0]
                self.assertEqual(failed_attempt["role"], "implementer")
                diagnostics = failed_attempt["native_diagnostics"]
                self.assertEqual(diagnostics["schema_version"], 1)
                self.assertEqual(diagnostics["identity_status"], "unverified")
                self.assertTrue(diagnostics["reason"])
                self.assertEqual(diagnostics["usage"]["total_tokens"], 19)
                self.assertEqual(diagnostics["session_id"], "native-delivery-session")
                self.assertEqual(
                    diagnostics["output_sha256"],
                    hashlib.sha256(self.output.read_bytes()).hexdigest(),
                )
                self.assertEqual(diagnostics["output_bytes"], self.output.stat().st_size)
                # Bound native diagnostics must survive in the public receipt,
                # not only in an unreferenced private stderr traceback.
                for reported_model in document.get("modelUsage", {}):
                    self.assertIn(reported_model, diagnostics["model_usage"])
                captures = [name for name in artifacts if name.endswith((".stdout", ".stderr"))]
                self.assertEqual(len(captures), 2)
                reopened = Service(self.fixture.runtime)
                self.assertEqual(reopened.result(run_id), self.service.result(run_id))
                replayed = reopened.start(self.task(), f"invalid-{kind}")
                self.assertFalse(replayed["created"])
                self.assertEqual(replayed["run_id"], run_id)
                self.assertEqual(replayed["state"], "failed")
                store = Store(reopened.database, reopened.artifacts)
                try:
                    self.assertEqual(store.worker_invocations(run_id), 1)
                finally:
                    store.close()
                self.fixture.assert_source_unchanged()

    def test_same_reported_model_cannot_pass_host_independence_gate(self):
        self.assert_independence_blocked("host")

    def test_same_reported_model_cannot_pass_headless_independence_gate(self):
        self.assert_independence_blocked("headless")

    def fallback_output(self, *, delay=False):
        """Install bounded fake native outputs selected by frozen profile model."""
        fallback_result = self.native_result()
        fallback_result["modelUsage"] = {self.FALLBACK_MODEL: self.counters()}
        fallback_result["session_id"] = "native-fallback-session"
        output_path = self.root / "fallback-result.json"
        output_path.write_text(json.dumps(fallback_result))
        self.fallback_marker = self.root / "fallback-entered"
        pause = "/bin/sleep 5\n" if delay else ""
        self.fake_claude.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--version\" ]; then\n"
            "  printf '%s\\n' '2.1.220 (Claude Code)'\n"
            "  exit 0\n"
            "fi\n"
            "previous=''\n"
            "for argument in \"$@\"; do\n"
            f"  if [ \"$previous\" = '--model' ] && [ \"$argument\" = '{self.FALLBACK_MODEL}' ]; then\n"
            f"    printf '%s\\n' 'started' > {shlex.quote(str(self.fallback_marker))}\n"
            f"    {pause}"
            "    printf '%s\\n' \"VALUE = 'fixed'\" > src/app.py\n"
            f"    /bin/cat {shlex.quote(str(output_path))}\n"
            "    exit 0\n"
            "  fi\n"
            "  previous=$argument\n"
            "done\n"
            f"/bin/cat {shlex.quote(str(self.output))}\n"
        )
        failed_result = self.native_result()
        failed_result["modelUsage"] = {"claude-haiku-4-5-20251001": self.counters()}
        return failed_result

    def assert_failed_native_attempt_preserved(self, receipt):
        failed = receipt["attempts"][0]
        self.assertEqual(failed["role"], "implementer")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["selected_profile"]["profile_id"], "fixture-implementer")
        self.assertIsNone(failed["observed_identity"])
        self.assertEqual(failed["usage"]["total_tokens"], 19)
        self.assertEqual(failed["usage"]["source"], "native_reported")
        diagnostic = failed["native_diagnostics"]
        self.assertEqual(diagnostic, failed["error"]["native_diagnostics"])
        self.assertEqual(diagnostic["identity_status"], "unverified")
        self.assertEqual(diagnostic["session_id"], "native-delivery-session")
        self.assertEqual(diagnostic["usage"], failed["usage"])
        self.assertIn("claude-haiku-4-5-20251001", diagnostic["model_usage"])
        self.assertEqual(diagnostic["output_sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertEqual(receipt["accounting"]["attempt_usage"][0], failed["usage"])

    def ready_handoff(self, run_id):
        self.assertEqual(self.wait(run_id, candidate=True)["state"], "queued")
        self.assertTrue(self.service.resume(run_id)["launched"])
        status = self.wait(run_id)
        self.assertEqual(status["state"], "awaiting_host", status)
        return self.service.handoff_claim(run_id, status["version"], "identity-host")

    def test_native_fallback_keeps_failed_identity_and_usage_in_success_receipt(self):
        self.configure(fallback=True)
        run_id = self.start(self.fallback_output(), "native-identity-fallback")
        claimed = self.ready_handoff(run_id)
        self.service.handoff_complete(
            run_id, claimed["claim"], self.fixture.decision(
                claimed["handoff"]["packet"], "accept-native-fallback", "accept",
                "Verified fallback received independent native review.",
            ),
        )
        receipt, _ = self.result_documents(run_id)
        self.assertEqual(receipt["state"], "succeeded")
        self.assert_failed_native_attempt_preserved(receipt)
        implementers = [item for item in receipt["attempts"] if item["role"] == "implementer"]
        self.assertEqual(len(implementers), 2)
        self.assertEqual(implementers[1]["selected_profile"]["profile_id"], "fixture-implementer-fallback")
        self.assertEqual(implementers[1]["observed_identity"]["model_id"], self.FALLBACK_MODEL)
        self.assertEqual(
            {item["selected_profile"]["profile"]["permission_policy"] for item in implementers},
            {"workspace_write"},
        )
        self.assertEqual(receipt["accounting"]["worker_invocations"], 3)
        self.fixture.assert_source_unchanged()

    def test_cancellation_keeps_prior_failed_native_identity_and_usage(self):
        self.configure(fallback=True)
        run_id = self.start(self.fallback_output(delay=True), "cancel-native-fallback")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not self.fallback_marker.exists():
            self.assertNotIn(self.service.status(run_id)["state"], {"failed", "cancelled", "succeeded"})
            time.sleep(0.05)
        self.assertTrue(self.fallback_marker.exists(), "fallback never reached its bounded native call")
        self.service.cancel(run_id)
        self.assertEqual(self.wait(run_id)["state"], "cancelled")
        receipt, _ = self.result_documents(run_id)
        self.assert_failed_native_attempt_preserved(receipt)
        self.assertEqual(len(receipt["attempts"]), 2)
        self.assertEqual(receipt["attempts"][1]["status"], "cancelled")
        self.assertEqual(receipt["accounting"]["worker_invocations"], 2)
        self.assertEqual(receipt["lead"]["status"], "not_reached")
        self.fixture.assert_source_unchanged()

    def downgrade_saved_identity_for_legacy_fixture(self, run_id):
        """Simulate pre-v2 persisted identity; never use as qualification proof."""
        store = Store(self.service.database, self.service.artifacts)
        try:
            snapshot = json.loads(store.run(run_id)["mutable_snapshot"])
            implementation = snapshot["delivery_iterations"][0]["implementation"]
            implementation["schema_version"] = 1
            attempt = implementation["attempt"]
            old_fields = {
                "harness", "harness_version", "model_provider", "model_id",
                "effort", "permission_policy", "verification",
            }
            attempt["observed_identity"] = {
                key: value for key, value in attempt["observed_identity"].items()
                if key in old_fields
            }
            attempt["observed_identity"]["effort"] = attempt["selected_profile"]["profile"]["effort"]["value"]
            store.connection.execute(
                "UPDATE runs SET mutable_snapshot=? WHERE id=?", (canonical_json(snapshot), run_id),
            )
        finally:
            store.close()

    def test_legacy_identity_cannot_authorize_new_acceptance_but_rejection_replays(self):
        self.configure()
        run_id = self.start(self.native_result(), "legacy-new-acceptance")
        claimed = self.ready_handoff(run_id)
        packet = claimed["handoff"]["packet"]
        self.downgrade_saved_identity_for_legacy_fixture(run_id)
        self.service = Service(self.fixture.runtime)
        with self.assertRaisesRegex(ContractError, "requires v2"):
            self.service.handoff_complete(
                run_id, claimed["claim"], self.fixture.decision(
                    packet, "legacy-new-accept", "accept", "Try old copied identity evidence.",
                ),
            )
        decision = self.fixture.decision(packet, "legacy-reject", "reject", "New verification required.")
        self.service.handoff_complete(run_id, claimed["claim"], decision)
        receipt, _ = self.result_documents(run_id)
        self.assertEqual(receipt["state"], "failed")
        self.assertEqual(receipt["delivery_iterations"][0]["implementation"]["schema_version"], 1)
        replay = Service(self.fixture.runtime).handoff_complete(run_id, claimed["claim"], decision)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["state"], "failed")
        self.assertEqual(self.result_documents(run_id)[0], receipt)
        self.fixture.assert_source_unchanged()

    def test_historical_successful_legacy_receipt_reads_and_exact_acceptance_replays(self):
        self.configure()
        run_id = self.start(self.native_result(), "historical-legacy-acceptance")
        claimed = self.ready_handoff(run_id)
        self.downgrade_saved_identity_for_legacy_fixture(run_id)
        decision = self.fixture.decision(
            claimed["handoff"]["packet"], "historical-accept", "accept", "Historical fixture acceptance.",
        )
        # Construct the historical fixture under pre-v2 semantics, then remove
        # the bypass before any assertions. This is compatibility data only.
        with patch("devsquad.workflows.require_independent_delivery_review", return_value=None):
            self.service.handoff_complete(run_id, claimed["claim"], decision)
        self.service = Service(self.fixture.runtime)
        receipt, _ = self.result_documents(run_id)
        self.assertEqual(receipt["state"], "succeeded")
        self.assertEqual(receipt["delivery_iterations"][0]["implementation"]["schema_version"], 1)
        replay = self.service.handoff_complete(run_id, claimed["claim"], decision)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["state"], "succeeded")
        self.assertEqual(self.result_documents(run_id)[0], receipt)
        with self.assertRaises(ContractError):
            self.service.handoff_complete(
                run_id, claimed["claim"], self.fixture.decision(
                    claimed["handoff"]["packet"], "different-new-accept", "accept", "Not an exact replay.",
                ),
            )
        self.fixture.assert_source_unchanged()

    def assert_independence_blocked(self, mode):
        self.configure(requested_model="sonnet", reviewer_model=self.MODEL)
        run_id = self.start(self.native_result(), f"same-model-{mode}", mode=mode)
        status = self.wait(run_id, candidate=True)
        if status["state"] == "queued":
            resumed = self.service.resume(run_id)
            self.assertTrue(resumed["launched"], resumed)
            status = self.wait(run_id)
        if status["state"] == "awaiting_host":
            claimed = self.service.handoff_claim(run_id, status["version"], "same-model-host")
            packet = claimed["handoff"]["packet"]
            with self.assertRaises(ContractError):
                self.service.handoff_complete(
                    run_id, claimed["claim"], self.fixture.decision(
                        packet, "same-model-accept", "accept", "Try the same model despite new labels.",
                    ),
                )
            self.service.handoff_complete(
                run_id, claimed["claim"], self.fixture.decision(
                    packet, "same-model-reject", "reject", "Independent review was not established.",
                ),
            )
            status = self.service.status(run_id)
        self.assertEqual(status["state"], "failed", status)
        receipt, _ = self.result_documents(run_id)
        self.assertNotEqual(receipt["lead"].get("disposition"), "accept")
        self.assertIn("independen", json.dumps(receipt).lower())
        self.fixture.assert_source_unchanged()


if __name__ == "__main__":
    unittest.main()
