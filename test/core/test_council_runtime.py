from __future__ import annotations

import copy
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
from devsquad.service import Service
from devsquad.store import Store, request_hash, ConflictError
from devsquad.contracts import ContractError
from devsquad.council_worker import role_packet
from devsquad.contracts import ExecutionIdentity, LaunchSpec
from devsquad.supervisor import Supervisor, _live_group_exists


class CouncilRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devsquad-council-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.runtime = self.root / "runtime"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Public controlled fixture")
        (self.repo / "README").write_text("A retry duplicates a non-idempotent request.\n")
        profiles = []
        for role, model in (("proposer_a", "model-a"), ("proposer_b", "model-b"), ("critic", "model-c")):
            profiles.append({"id": role, "harness": "fixture", "model_family": model,
                "model_id": model, "effort": {"value": "low", "transport": "native"},
                "required_tools": [], "permission_policy": "read_only", "account_pool_id": "shared",
                "billing_mode": "subscription", "quality_status": "proven", "evidence_refs": ["public-fixture"]})
        (self.repo / "profiles.json").write_text(json.dumps({"schema_version": 1, "profiles": profiles, "bindings": {}}))
        policy = {"schema_version": 1, "id": "controlled-council", "version": 1,
            "roles": {role: [{"kind": "profile", "id": role}] for role in ("proposer_a", "proposer_b", "critic")},
            "task_classes": {"fixture-council": "proven"}, "require_different_model_for_review": True,
            "prefer_different_harness_for_review": True, "account_pools": {"shared": {
                "allowed_billing_modes": ["subscription"], "max_concurrency": 1,
                "unknown_capacity_policy": "allow_bounded"}}, "experiment_budget": {}}
        policy["roles"]["lead"] = [{"kind": "profile", "id": "critic"}]
        (self.repo / "policy.json").write_text(json.dumps(policy))
        self.git("add", ".")
        self.git("commit", "-qm", "public frozen input")
        self.source_oid = self.git("rev-parse", "HEAD")
        self.task = {"schema_version": 1, "project": {"repo_path": str(self.repo), "base_ref": "HEAD", "target_ref": "HEAD"},
            "workflow": "council-decision", "goal": "Choose a safe retry policy", "task_class": "fixture-council",
            "acceptance": [{"id": "safe-retry", "description": "Avoid duplicate side effects", "evidence_kind": "review"}],
            "checks": [{"id": "public-check", "argv": ["git", "diff", "--check"], "cwd": ".", "timeout_seconds": 5, "required_to_pass": True}],
            "scope": {"read_paths": ["README"], "write_paths": []}, "lead": {"mode": "headless"},
            "routing": {"profiles_file": "profiles.json", "policy_file": "policy.json"},
            "budget": {"wall_seconds": 60, "max_worker_invocations": 4, "max_revisions": 0, "max_fallbacks_per_step": 0},
            "origin": {"surface": "cli"}, "council": {"schema_version": 1, "enabled": True, "automatic": False,
                "reason": "Compare independent retry alternatives", "min_valid_proposals": 2, "required_critics": 1,
                "max_invocations": 4, "seed": "a" * 64, "evidence": [],
                "rubric": [{"id": "safety", "description": "Does not duplicate side effects"}]}}
        proposal = {"summary": "Use an idempotency key", "approach": "Bounded retries with a stable key",
            "claims": [{"text": "A stable key avoids duplicate effects", "evidence_ids": []}],
            "validation": "Exercise a repeated request"}
        self.fixture = {"proposer_a": {"document": proposal}, "proposer_b": {"document": dict(proposal, summary="Do not retry blindly")},
            "critic": {"document": {"summary": "Both avoid a blind retry", "assessments": [
                {"label": label, "criterion_id": "safety", "status": "supported", "reason": "Requires duplicate protection", "evidence_ids": []} for label in ("A", "B")],
                "objections": [{"id": "retention", "label": "A", "reason": "Key retention must cover retries", "evidence_ids": []}]}},
            "lead": {"document": {"disposition": "accept", "chosen": "synthesis", "reason": "Retain both safeguards",
                "supported_claims": ["Never retry a non-idempotent request blindly"], "discarded_alternatives": ["Blind retry"],
                "unresolved_objections": ["retention"], "validation": "Test duplicates and retention expiry"}}}
        self.service = Service(self.runtime)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    def wait(self, run_id, states, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.service.status(run_id)
            if status["state"] in states:
                return status
            if status["state"] == "awaiting_host" and self.task["lead"]["mode"] == "headless":
                try:
                    self.service.resume(run_id)
                except ConflictError:
                    pass  # Another detached coordinator may win this exact fence.
            time.sleep(.05)
        self.fail(f"Council did not reach {states}: {self.service.status(run_id)}")

    def receipt(self, run_id):
        result = self.service.result(run_id)
        artifact = next(a for a in result["artifacts"] if a["name"] == "receipt.json")
        return json.loads(Path(artifact["path"]).read_bytes())

    def test_public_process_flow_seals_proposals_and_preserves_dissent_and_unknown_usage(self):
        started = self.service.start(self.task, "public-council", _internal_council_fixture=self.fixture)
        self.assertEqual(self.wait(started["run_id"], {"succeeded", "failed"})["state"], "succeeded")
        receipt = self.receipt(started["run_id"])
        self.assertEqual([a["role"] for a in receipt["attempts"]], ["proposer_a", "proposer_b", "critic", "lead"])
        self.assertEqual(receipt["worker_invocations"], 4)
        self.assertEqual(receipt["identity_scope"], "all_fixture")
        self.assertTrue(all(a["usage"] is None and a["observed_identity"] is None for a in receipt["attempts"]))
        self.assertEqual(receipt["dissent"][0]["id"], "retention")
        self.assertFalse(receipt["automatic_enabled"])
        self.assertEqual(self.git("rev-parse", "HEAD"), self.source_oid)
        self.assertEqual(self.git("status", "--porcelain"), "")
        store = Store(self.runtime / "state.sqlite3", self.runtime / "artifacts")
        self.addCleanup(store.close)
        snapshot = json.loads(store.run(started["run_id"])["mutable_snapshot"])
        outcomes = store.outcomes_for_run(started["run_id"])
        self.assertEqual(len(outcomes), 1)
        outcome = outcomes[0]["outcome"]
        self.assertEqual(outcome["kind"], "final")
        self.assertEqual({item["role"] for item in outcome["contributions"]}, {"proposer_a", "proposer_b", "critic", "lead"})
        self.assertTrue(all(item["status"] == "passed" for item in outcome["criteria"]))
        self.service.result(started["run_id"])
        self.service.learning_report(str(self.repo))
        self.assertEqual(len(store.outcomes_for_run(started["run_id"])), 1)
        for role in ("proposer_a", "proposer_b"):
            packet = role_packet(snapshot, role)
            self.assertEqual(set(packet), {"brief"})
            self.assertNotIn("profile", json.dumps(packet))
        critic = role_packet(snapshot, "critic")
        self.assertEqual(set(critic["proposals"]), {"A", "B"})
        self.assertNotIn("model-a", json.dumps(critic))
        self.assertNotIn("model-b", json.dumps(critic))

    def test_invalid_critic_cannot_become_consensus(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["critic"]["document"]["assessments"].pop()
        started = self.service.start(self.task, "invalid-critic", _internal_council_fixture=fixture)
        self.assertEqual(self.wait(started["run_id"], {"failed"})["state"], "failed")
        receipt = self.receipt(started["run_id"])
        self.assertEqual(receipt["worker_invocations"], 3)
        self.assertIsNone(receipt["lead"]["disposition"])

    def test_cancel_owned_gated_launcher_never_publishes_terminal_before_cleanup(self):
        with patch.object(self.service, "_spawn_daemon", return_value=0):
            started = self.service.start(self.task, "gated-owned-cancel", _internal_council_fixture=self.fixture)
        store = Store(self.runtime / "state.sqlite3", self.runtime / "artifacts")
        self.addCleanup(store.close)
        run = store.run(started["run_id"])
        snapshot = json.loads(run["mutable_snapshot"])
        selected = snapshot["routing"]["roles"]["proposer_a"]["selected"]
        identity = ExecutionIdentity("fixture", "controlled", None, selected["profile"]["model_family"],
            selected["profile"]["model_id"], "low", permissions="read_only", account_pool="shared")
        spec = LaunchSpec(1, "fixture", "cli_exec", (sys.executable, "-P", "-m", "devsquad.fake_step", "--delay", "30"),
            run["worktree_path"], None, 5, identity,
            {"DEVSQUAD_WORKER": "1", "DEVSQUAD_COUNCIL_ROLE": "proposer_a"})
        original_release = Supervisor._release_runner_gate
        owned = []

        def cancel_before_gate_release(descriptor):
            attempt = store.attempt(started["run_id"])
            owned.append(attempt["pgid"])
            self.assertTrue(_live_group_exists(attempt["pgid"]))
            cancelled = self.service.cancel(started["run_id"])
            self.assertEqual(cancelled["state"], "cancelling")
            self.assertIsNone(store.artifact_named(started["run_id"], "receipt.json"))
            original_release(descriptor)

        supervisor = Supervisor(store)
        with patch.dict(os.environ, {"PYTHONPATH": str(Path(run["package_path"]))}), patch.object(Supervisor, "_release_runner_gate", side_effect=cancel_before_gate_release):
            handle = supervisor.launch_durable(started["run_id"], run["version"], spec, "controlled-gated-owner", run["package_digest"],
                role="proposer_a", profile_id=selected["profile_id"], profile_index=0)
        supervisor.wait_durable(handle, 5)
        self.assertEqual(self.service.status(started["run_id"])["state"], "cancelled")
        self.assertTrue(all(not _live_group_exists(group) for group in owned))
        self.assertIsNone(self.receipt(started["run_id"])["lead"]["disposition"])

    def test_failed_mandatory_check_cannot_be_overridden_by_lead(self):
        self.task["checks"][0]["argv"] = ["false"]
        started = self.service.start(self.task, "failed-check", _internal_council_fixture=self.fixture)
        self.assertEqual(self.wait(started["run_id"], {"failed"})["state"], "failed")
        receipt = self.receipt(started["run_id"])
        self.assertIsNone(receipt["lead"]["disposition"])

    def test_host_exact_claim_choice_and_terminal_replay(self):
        self.task["lead"]["mode"] = "host"
        self.task["budget"]["max_worker_invocations"] = 3
        started = self.service.start(self.task, "host", _internal_council_fixture=self.fixture)
        waiting = self.wait(started["run_id"], {"awaiting_host"})
        acquired = self.service.handoff_claim(started["run_id"], waiting["version"], "fixture-host")
        packet = acquired["handoff"]["packet"]
        choice = self.fixture["lead"]["document"]
        body = {"schema_version": 1, "submission_id": "host-choice", "disposition": "accept", "reason": choice["reason"],
            "evidence_refs": [{"artifact_id": a["artifact_id"], "sha256": a["sha256"]} for a in packet["artifacts"]], "council_choice": choice}
        decision = {**body, "submission_hash": request_hash(body)}
        result = self.service.handoff_complete(started["run_id"], acquired["claim"], decision)
        self.assertEqual(result["state"], "succeeded")
        self.assertTrue(self.service.handoff_complete(started["run_id"], acquired["claim"], decision)["replayed"])

    def test_guided_host_finish_has_explicit_choice_and_no_json(self):
        self.task["lead"]["mode"] = "host"
        started = self.service.start(self.task, "guided-host", _internal_council_fixture=self.fixture)
        self.wait(started["run_id"], {"awaiting_host"})
        view = self.service.council_handoff_view(started["run_id"])
        self.assertIn("Council handoff", view["report"])
        result = self.service.finish_council(started["run_id"], "accept", "retain safeguards", chosen="synthesis",
            supported_claims=["No blind retries"], discarded_alternatives=["Blind retry"], validation="Test retention expiry")
        self.assertEqual(result["state"], "succeeded")
        with self.assertRaises(ConflictError):
            self.service.finish_council(started["run_id"], "accept", "replay", chosen="A",
                supported_claims=["claim"], discarded_alternatives=[], validation="check")

    def test_missing_proposer_empty_output_and_missing_critic_never_form_quorum(self):
        for index, (role, empty) in enumerate((("proposer_a", False), ("proposer_b", True), ("critic", False))):
            fixture = copy.deepcopy(self.fixture)
            if empty:
                fixture[role] = {"document": {}}
            else:
                fixture.pop(role)
            started = self.service.start(self.task, f"missing-{index}", _internal_council_fixture=fixture)
            self.assertEqual(self.wait(started["run_id"], {"failed"})["state"], "failed")
            receipt = self.receipt(started["run_id"])
            self.assertIsNone(receipt["lead"]["disposition"])
            self.assertLess(receipt["worker_invocations"], 4)

    def test_cancel_active_role_and_saved_host_wait_never_accept(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["proposer_a"]["delay_seconds"] = 4
        started = self.service.start(self.task, "active-cancel", _internal_council_fixture=fixture)
        self.wait(started["run_id"], {"running"})
        self.service.cancel(started["run_id"])
        self.assertEqual(self.wait(started["run_id"], {"cancelled"})["state"], "cancelled")
        self.assertIsNone(self.receipt(started["run_id"])["lead"]["disposition"])
        self.task["lead"]["mode"] = "host"
        started = self.service.start(self.task, "waiting-cancel", _internal_council_fixture=self.fixture)
        self.wait(started["run_id"], {"awaiting_host"})
        self.assertEqual(self.service.cancel(started["run_id"])["state"], "cancelled")
        self.assertEqual(self.receipt(started["run_id"])["worker_invocations"], 3)

    def test_queued_cancel_and_restart_use_saved_exact_snapshot(self):
        with patch.object(self.service, "_spawn_daemon"):
            started = self.service.start(self.task, "queued-cancel", _internal_council_fixture=self.fixture)
        self.assertEqual(self.service.cancel(started["run_id"])["state"], "cancelled")
        self.assertEqual(self.receipt(started["run_id"])["worker_invocations"], 0)
        with patch.object(self.service, "_spawn_daemon"):
            started = self.service.start(self.task, "restart", _internal_council_fixture=self.fixture)
        self.service = Service(self.runtime)
        self.service.resume(started["run_id"])
        self.assertEqual(self.wait(started["run_id"], {"succeeded", "failed"})["state"], "succeeded")

    def test_guided_claim_crash_and_submitted_crash_resume_exact_decision(self):
        self.task["lead"]["mode"] = "host"
        for boundary in ("record_handoff_submission", "complete_handoff_terminal"):
            started = self.service.start(self.task, f"crash-{boundary}", _internal_council_fixture=self.fixture)
            self.wait(started["run_id"], {"awaiting_host"})
            with patch.object(Store, boundary, side_effect=RuntimeError("injected crash")):
                with self.assertRaises(RuntimeError):
                    self.service.finish_council(started["run_id"], "accept", "frozen crash choice", chosen="A",
                        supported_claims=["No blind retry"], discarded_alternatives=["Blind retry"], validation="Duplicate request test")
            self.service = Service(self.runtime)
            result = self.service.resume(started["run_id"])
            self.assertEqual(result["state"], "succeeded")
            self.assertEqual(self.receipt(started["run_id"])["lead"]["choice"]["reason"], "frozen crash choice")

    def test_expired_guided_claim_reacquires_only_its_frozen_intent(self):
        self.task["lead"]["mode"] = "host"
        started = self.service.start(self.task, "expired-guided", _internal_council_fixture=self.fixture)
        self.wait(started["run_id"], {"awaiting_host"})
        with patch.object(Store, "record_handoff_submission", side_effect=RuntimeError("crash after claim")):
            with self.assertRaises(RuntimeError):
                self.service.finish_council(started["run_id"], "accept", "intent before expiry", chosen="B",
                    supported_claims=["claim"], discarded_alternatives=[], validation="check")
        store = Store(self.runtime / "state.sqlite3", self.runtime / "artifacts")
        from datetime import datetime, timedelta, timezone
        expires = store.handoff_snapshot(started["run_id"]).claim.expires_at
        store.close()
        future = datetime.fromisoformat(expires) + timedelta(seconds=1)
        class FutureDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return future
        with patch("devsquad.council_runtime.datetime", FutureDateTime), patch("devsquad.store._authoritative_now", return_value=future):
            self.assertEqual(Service(self.runtime).resume(started["run_id"])["state"], "succeeded")

    def test_same_owner_low_level_takeover_does_not_recover_old_guided_intent(self):
        self.task["lead"]["mode"] = "host"
        started = self.service.start(self.task, "intent-race", _internal_council_fixture=self.fixture)
        self.wait(started["run_id"], {"awaiting_host"})
        with patch.object(Store, "record_handoff_submission", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.service.finish_council(started["run_id"], "accept", "old intent", chosen="A",
                    supported_claims=["claim"], discarded_alternatives=[], validation="check")
        from datetime import datetime, timedelta
        store = Store(self.runtime / "state.sqlite3", self.runtime / "artifacts")
        future = datetime.fromisoformat(store.handoff_snapshot(started["run_id"]).claim.expires_at) + timedelta(seconds=1)
        store.close()
        with patch("devsquad.store._authoritative_now", return_value=future):
            version = self.service.status(started["run_id"])["version"]
            self.service.handoff_claim(started["run_id"], version, "terminal-operator")
        with self.assertRaises(ConflictError):
            self.service.resume(started["run_id"])
        self.assertEqual(self.service.status(started["run_id"])["state"], "awaiting_host")

    def test_preflight_quota_exhaustion_is_zero_launches_not_consensus(self):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        self.service.capacity_observe({"schema_version": 1, "observation_id": "quota-zero", "pool_id": "shared", "window_id": "subscription",
            "applies_to": {"harnesses": [], "model_families": [], "model_ids": []},
            "source": "native_reported", "observed_at": now.isoformat(), "expires_at": (now + timedelta(minutes=5)).isoformat(),
            "used": 100, "limit": 100, "unit": "percent", "resets_at": (now + timedelta(minutes=5)).isoformat(), "confidence": "confirmed"})
        started = self.service.start(self.task, "quota", _internal_council_fixture=self.fixture)
        self.assertEqual(started["state"], "failed")
        self.assertEqual(self.receipt(started["run_id"])["worker_invocations"], 0)


if __name__ == "__main__":
    unittest.main()
