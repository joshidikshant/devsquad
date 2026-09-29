import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.contracts import ExecutionIdentity, LaunchSpec
from devsquad.reports import TERMINAL_REPORT_NAMES
from devsquad.service import Service
from devsquad.store import ConflictError, Store, canonical_json
from devsquad.supervisor import Supervisor, inspect_process
from devsquad_test_fixtures import branch_review_routing_documents


def concurrent_receipt_import(database, artifacts, run_id, barrier, results):
    store = None
    try:
        store = Store(Path(database), Path(artifacts))
        barrier.wait(timeout=10)
        results.put(Supervisor(store).import_durable(run_id))
    except Exception as exc:
        results.put(f"{type(exc).__name__}: {exc}")
    finally:
        if store is not None:
            store.close()


def crash_after_attempt_reservation(database, artifacts, run_id, expected_version, package_digest):
    store = Store(Path(database), Path(artifacts))
    store.reserve_attempt(run_id, expected_version, "crashed-supervisor", package_digest)
    os._exit(23)


def crash_after_runner_identity(
    database, artifacts, run_id, expected_version, package_path,
    package_digest, repo, marker,
):
    os.environ["PYTHONPATH"] = package_path
    store = Store(Path(database), Path(artifacts))
    identity = ExecutionIdentity("fixture", "1", None, None, None, None)
    command = (
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({marker!r}).write_text('executed')",
    )
    spec = LaunchSpec(
        1, "fixture", "cli_exec", command, repo, None, 30, identity,
    )
    supervisor = Supervisor(store)
    supervisor._release_runner_gate = lambda _: os._exit(24)
    supervisor.launch_durable(
        run_id, expected_version, spec, "crashed-after-identity", package_digest,
    )
    os._exit(99)


def crash_after_artifact_finalize_before_import(database, artifacts, run_id):
    store = Store(Path(database), Path(artifacts))
    store.commit_durable_import = lambda *args, **kwargs: os._exit(25)
    Supervisor(store).import_durable(run_id)
    os._exit(99)


def crash_after_recovery_cancel_commits(database, artifacts, run_id):
    store = Store(Path(database), Path(artifacts))
    request_recovery_cancel = store.request_recovery_cancel

    def request_then_crash(cancel_run_id, attempt_token):
        request_recovery_cancel(cancel_run_id, attempt_token)
        os._exit(26)

    store.request_recovery_cancel = request_then_crash
    Supervisor(store).cancel_orphan(run_id)
    os._exit(99)


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devsquad-service-")
        self.root = Path(self.temp.name); self.repo = self.root / "repo"; self.runtime = self.root / "runtime"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Test"], check=True)
        self.profiles_json, self.policy_json = branch_review_routing_documents()
        (self.repo / "src").mkdir()
        (self.repo / "tests").mkdir()
        (self.repo / "src/app.py").write_text("VALUE = 'base'\n")
        (self.repo / "tests/test_app.py").write_text("# fixture test\n")
        (self.repo / "profiles.json").write_text(self.profiles_json)
        (self.repo / "policy.json").write_text(self.policy_json)
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "base"], check=True)
        self.task = json.loads((ROOT / "docs/plans/engineering-team/examples/branch-review.json").read_text())
        self.task["project"] = {"repo_path": str(self.repo), "base_ref": "HEAD", "target_ref": "HEAD"}
        self.task["routing"]["profiles_file"] = "profiles.json"; self.task["routing"]["policy_file"] = "policy.json"
        self.service = Service(self.runtime)

    def tearDown(self): self.temp.cleanup()

    def wait_state(self, run_id, states, timeout=8):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            status=self.service.status(run_id)
            if status["state"] in states: return status
            time.sleep(.05)
        self.fail(f"run did not reach {states}: {self.service.status(run_id)}")

    def configure_decision_helper(self, mode):
        profiles = json.loads((self.repo / "profiles.json").read_text())
        if not any(item["id"] == "fixture-reviewer-b" for item in profiles["profiles"]):
            second = dict(profiles["profiles"][0])
            second.update({
                "id": "fixture-reviewer-b",
                "model_id": "fixture-review-model-b",
                "evidence_refs": ["tracked-fixture-b"],
            })
            profiles["profiles"].append(second)
        policy = json.loads((self.repo / "policy.json").read_text())
        policy["roles"]["reviewer"] = [
            {"kind": "alias", "id": "review.deep"},
            {"kind": "profile", "id": "fixture-reviewer-b"},
        ]
        policy["decision_helper"] = {
            "schema_version": 1,
            "mode": mode,
            "purpose": {
                "id": "profile-ranking", "version": 1,
                "question_sha256": "a" * 64,
                "rubric_sha256": "b" * 64,
            },
            "adapter": {
                "id": "fixture", "model": "fixture-v1",
                "runtime_revision": "fixture-runtime-1",
                "calibration_version": None,
            },
            "language": "en",
            "min_confidence": 0.7,
            "gate_evidence_sha256": "c" * 64 if mode == "advisory" else None,
            "budget": {
                "max_calls": 1, "max_input_bytes": 4096,
                "wall_seconds": 2, "max_cost_usd": 0.01,
            },
        }
        (self.repo / "profiles.json").write_text(
            json.dumps(profiles, sort_keys=True) + "\n",
        )
        (self.repo / "policy.json").write_text(
            json.dumps(policy, sort_keys=True) + "\n",
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "add", "profiles.json", "policy.json"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-qm", f"decision {mode}"],
            check=True,
        )

    @staticmethod
    def decision_fixture():
        return {
            "rankings": {
                "reviewer": ["fixture-reviewer-b", "fixture-reviewer"],
            },
            "confidence": 0.9,
            "elapsed_ms": 3,
        }

    def test_start_is_idempotent_and_result_events_are_durable(self):
        first=self.service.start(self.task,"same",_internal_fake_delay=.01)
        second=self.service.start(self.task,"same",_internal_fake_delay=.01)
        self.assertEqual(first["run_id"],second["run_id"]); self.assertFalse(second["created"])
        self.wait_state(first["run_id"],{"succeeded"})
        result=self.service.result(first["run_id"])
        self.assertTrue(result["ready"]); self.assertEqual(len(result["artifacts"]),3)
        page=self.service.events(first["run_id"],0,2)
        self.assertEqual(len(page["events"]),2); self.assertIsNotNone(page["next_cursor"])
        with self.assertRaises(ConflictError): self.service.resume(first["run_id"])

    def test_decision_helper_default_off_has_no_runtime_or_call_effect(self):
        started = self.service.start(
            self.task, "decision-off",
            _internal_review_fixture={
                "verdict": "clean", "summary": "off fixture", "findings": [],
            },
        )
        self.assertEqual(started["state"], "queued", started)
        self.wait_state(started["run_id"], {"awaiting_host"})
        store = Store(self.runtime / "state.sqlite3", self.runtime / "artifacts")
        try:
            snapshot = json.loads(store.run(started["run_id"])["mutable_snapshot"])
            observations = store.connection.execute(
                "SELECT COUNT(*) FROM run_decision_observations WHERE run_id=?",
                (started["run_id"],),
            ).fetchone()[0]
        finally:
            store.close()
        self.assertNotIn("decision_helper", snapshot["routing"])
        self.assertNotIn("decision_observation", snapshot)
        self.assertEqual(observations, 0)

    def test_shadow_decision_is_cached_and_never_changes_selection(self):
        self.configure_decision_helper("shadow")
        fixture = self.decision_fixture()
        run_ids = []
        for key in ("decision-shadow-one", "decision-shadow-two"):
            started = self.service.start(
                self.task, key,
                _internal_review_fixture={
                    "verdict": "clean", "summary": key, "findings": [],
                },
                _internal_decision_fixture=fixture,
            )
            self.assertEqual(started["state"], "queued", started)
            self.wait_state(started["run_id"], {"awaiting_host"})
            run_ids.append(started["run_id"])
        store = Store(self.runtime / "state.sqlite3", self.runtime / "artifacts")
        try:
            snapshots = [
                json.loads(store.run(run_id)["mutable_snapshot"])
                for run_id in run_ids
            ]
            observations = [
                store.decision_observation(run_id, "profile-ranking")
                for run_id in run_ids
            ]
            cache_rows = store.connection.execute(
                "SELECT COUNT(*) FROM decision_cache",
            ).fetchone()[0]
        finally:
            store.close()
        for snapshot in snapshots:
            reviewer = snapshot["routing"]["roles"]["reviewer"]
            self.assertEqual(reviewer["selected"]["profile_id"], "fixture-reviewer")
            self.assertEqual(
                snapshot["routing"]["decision_helper"]["role_status"],
                {"reviewer": "shadow_mode"},
            )
        self.assertEqual(observations[0]["cache_key"], observations[1]["cache_key"])
        self.assertEqual([item["billable_calls"] for item in observations], [1, 1])
        self.assertEqual(cache_rows, 1)
        self.assertNotIn(self.task["goal"], json.dumps(observations[0]["request"]))
        status = self.service.status(run_ids[0])
        self.assertEqual(status["decision_helper"][0]["status"], "succeeded")
        self.assertEqual(status["decision_helper"][0]["billable_calls"], 1)

    def test_advisory_reorders_only_eligible_profiles_and_freezes_effect(self):
        self.configure_decision_helper("advisory")
        started = self.service.start(
            self.task, "decision-advisory",
            _internal_review_fixture={
                "verdict": "clean", "summary": "advisory fixture", "findings": [],
            },
            _internal_decision_fixture=self.decision_fixture(),
        )
        self.assertEqual(started["state"], "queued", started)
        self.wait_state(started["run_id"], {"awaiting_host"})
        store = Store(self.runtime / "state.sqlite3", self.runtime / "artifacts")
        try:
            snapshot = json.loads(store.run(started["run_id"])["mutable_snapshot"])
            observation = store.decision_observation(
                started["run_id"], "profile-ranking",
            )
        finally:
            store.close()
        reviewer = snapshot["routing"]["roles"]["reviewer"]
        self.assertEqual(reviewer["selected"]["profile_id"], "fixture-reviewer-b")
        self.assertEqual(
            [item["profile_id"] for item in reviewer["fallbacks"]],
            ["fixture-reviewer"],
        )
        self.assertEqual(
            snapshot["routing"]["decision_helper"]["applied_roles"], ["reviewer"],
        )
        self.assertTrue(observation["applied"])
        self.assertEqual(observation["billable_calls"], 1)

    def test_missing_decision_adapter_preserves_route_and_records_zero_calls(self):
        self.configure_decision_helper("shadow")
        started = self.service.start(
            self.task, "decision-unavailable",
            _internal_review_fixture={
                "verdict": "clean", "summary": "unavailable fixture", "findings": [],
            },
        )
        self.assertEqual(started["state"], "queued", started)
        self.wait_state(started["run_id"], {"awaiting_host"})
        store = Store(self.runtime / "state.sqlite3", self.runtime / "artifacts")
        try:
            snapshot = json.loads(store.run(started["run_id"])["mutable_snapshot"])
            observation = store.decision_observation(
                started["run_id"], "profile-ranking",
            )
        finally:
            store.close()
        self.assertEqual(
            snapshot["routing"]["roles"]["reviewer"]["selected"]["profile_id"],
            "fixture-reviewer",
        )
        self.assertEqual(observation["status"], "unavailable")
        self.assertEqual(observation["billable_calls"], 0)

    def test_capacity_observation_drives_preflight_and_status_evidence(self):
        now = datetime.now(timezone.utc)
        observed = self.service.capacity_observe({
            "schema_version": 1,
            "observation_id": "service-capacity-short",
            "pool_id": "fixture-subscription",
            "window_id": "short",
            "applies_to": {
                "harnesses": [], "model_families": [], "model_ids": [],
            },
            "observed_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
            "source": "manual_reported",
            "used": 10,
            "limit": 100,
            "unit": "percent",
            "resets_at": (now + timedelta(hours=1)).isoformat(),
            "confidence": "reported",
        })
        self.assertFalse(observed["record"]["replayed"])
        self.assertEqual(observed["capacity"]["status"], "available")
        started = self.service.start(
            self.task,
            "capacity-status",
            _internal_review_fixture={
                "verdict": "clean", "summary": "capacity fixture", "findings": [],
            },
        )
        self.assertEqual(started["state"], "queued", started)
        status = self.wait_state(started["run_id"], {"awaiting_host"})
        self.assertEqual(
            status["capacity"]["frozen"]["fixture-subscription"]["status"],
            "available",
        )
        current = status["capacity"]["current"]["fixture-reviewer"]
        self.assertEqual(current["status"], "available")
        self.assertEqual(current["windows"][0]["window_id"], "short")

    def test_outcome_add_and_project_report_share_saved_ledger(self):
        started = self.service.start(
            self.task, "learning-report", _internal_fake_delay=.01,
        )
        self.wait_state(started["run_id"], {"succeeded"})
        now = datetime.now(timezone.utc)
        recorded = self.service.outcome_add(started["run_id"], {
            "schema_version": 1,
            "outcome_id": "service-final-outcome",
            "kind": "final",
            "verdict": "succeeded",
            "selection_mode": "automatic",
            "observed_at": now.isoformat(),
            "corrects_outcome_id": None,
            "summary": "The saved fixture run completed successfully.",
            "criteria": [],
            "contributions": [],
            "lead_repairs": [],
            "evidence_refs": ["result-receipt.json"],
        })
        self.assertFalse(recorded["replayed"])
        report = self.service.learning_report(self.repo)
        self.assertEqual(report["sample_size"], 1)
        self.assertEqual(report["terminal_run_count"], 1)
        self.assertEqual(report["final_successes"], 1)
        self.assertEqual(report["missingness"]["finals_without_contributions"], 1)
        proposed = self.service.learning_propose(self.repo)
        self.assertEqual(proposed["proposal"]["verdict"], "no_change")
        self.assertFalse(proposed["proposal"]["active_policy_changed"])
        self.assertEqual(
            proposed["proposal"]["reasons"], ["no_evaluated_experiment"],
        )
        for artifact in proposed["artifacts"].values():
            content = Path(artifact["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), artifact["sha256"])
            self.assertEqual(len(content), artifact["byte_size"])

    def test_abandoned_preparation_is_reclaimed_from_the_submitted_request(self):
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            claim=store.claim_start(
                self.repo,
                "abandoned-preparation",
                {"task":self.task,"supersedes_run_id":None,"_internal_fake_delay":.01},
                "dead-preflight-owner",
            )
        finally:
            store.close()
        resumed=self.service.resume(claim.run_id)
        self.assertTrue(resumed["launched"])
        self.assertEqual(resumed["disposition"],"continued")
        self.wait_state(claim.run_id,{"succeeded"})
        events=self.service.events(claim.run_id)["events"]
        self.assertEqual(sum(event["type"]=="run.preparation_reclaimed" for event in events),1)
        self.assertTrue(self.service.result(claim.run_id)["ready"])

    def test_preparation_recovery_rejects_a_retargeted_repository_symlink(self):
        link=self.root/"repo-link"
        link.symlink_to(self.repo,target_is_directory=True)
        task=json.loads(json.dumps(self.task))
        task["project"]["repo_path"]=str(link)
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            claim=store.claim_start(
                link,
                "retargeted-repository",
                {"task":task,"supersedes_run_id":None,"_internal_fake_delay":.01},
                "dead-preflight-owner",
            )
        finally:
            store.close()

        other=self.root/"other-repo"
        subprocess.run(["git","init","-q",str(other)],check=True)
        subprocess.run(["git","-C",str(other),"config","user.email","test@example.invalid"],check=True)
        subprocess.run(["git","-C",str(other),"config","user.name","Test"],check=True)
        (other/"profiles.json").write_text('{"source":"other"}\n')
        (other/"policy.json").write_text('{"source":"other"}\n')
        subprocess.run(["git","-C",str(other),"add","."],check=True)
        subprocess.run(["git","-C",str(other),"commit","-qm","other"],check=True)
        link.unlink()
        link.symlink_to(other,target_is_directory=True)

        resumed=self.service.resume(claim.run_id)
        self.assertFalse(resumed["launched"])
        self.assertEqual(resumed["disposition"],"preparation_failed")
        self.assertIn("claimed worktree",resumed["error"]["message"])
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            run=store.run(claim.run_id)
            self.assertEqual(run["state"],"failed")
            self.assertEqual(run["worktree_path"],str(self.repo.resolve()))
            self.assertIsNone(run["mutable_snapshot"])
        finally:
            store.close()

    def test_new_process_inspects_run_after_launching_process_exits(self):
        task_file=self.root/"task.json"; task_file.write_text(json.dumps(self.task))
        env=os.environ.copy(); env["PYTHONPATH"]=str(ROOT/"plugin/core/src")
        command=[sys.executable,"-m","devsquad.cli","start","--task-file",str(task_file),"--idempotency-key","shell","--runtime-dir",str(self.runtime),"--json"]
        started=subprocess.run(command,text=True,capture_output=True,env=env,check=True)
        run_id=json.loads(started.stdout)["data"]["run_id"]
        status_command=[sys.executable,"-m","devsquad.cli","status",run_id,"--runtime-dir",str(self.runtime),"--json"]
        observed=subprocess.run(status_command,text=True,capture_output=True,env=env,check=True)
        self.assertEqual(json.loads(observed.stdout)["data"]["run_id"],run_id)
        self.assertEqual(self.service.status(run_id)["state"],"failed")

    def test_enqueue_crash_resumes_once_and_cancel_is_prompt(self):
        with mock.patch.object(self.service,"_spawn_daemon",return_value=0):
            started=self.service.start(self.task,"enqueue-crash",_internal_fake_delay=.2)
        self.assertEqual(self.service.status(started["run_id"])["state"],"queued")
        resumed=self.service.resume(started["run_id"]); self.assertTrue(resumed["launched"])
        self.wait_state(started["run_id"],{"running","succeeded"})
        if self.service.status(started["run_id"])["state"]=="running":
            response=self.service.cancel(started["run_id"]); self.assertIn(response["state"],{"cancelling","cancelled"})
            self.wait_state(started["run_id"],{"cancelled"})

    def test_process_crash_after_attempt_reservation_is_recoverable(self):
        with mock.patch.object(self.service,"_spawn_daemon",return_value=0):
            started=self.service.start(self.task,"reservation-crash",_internal_fake_delay=.01)
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            run=store.run(started["run_id"])
            expected_version=run["version"]
            package_digest=run["package_digest"]
        finally:
            store.close()

        context=multiprocessing.get_context("spawn")
        process=context.Process(
            target=crash_after_attempt_reservation,
            args=(
                str(self.runtime/"state.sqlite3"),str(self.runtime/"artifacts"),
                started["run_id"],expected_version,package_digest,
            ),
        )
        process.start(); process.join(timeout=10)
        if process.is_alive():
            process.terminate(); process.join(timeout=2)
        self.assertEqual(process.exitcode,23)
        self.assertEqual(self.service.status(started["run_id"])["phase"],"launching")

        with mock.patch.object(self.service,"_spawn_daemon",return_value=123) as spawn:
            resumed=self.service.resume(started["run_id"])
        self.assertTrue(resumed["launched"])
        spawn.assert_called_once()
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            run=store.run(started["run_id"])
            attempt=store.attempt(started["run_id"])
            self.assertEqual((run["state"],run["phase"]),("queued",None))
            self.assertEqual(attempt["status"],"recovery_required")
            event_types=[event["type"] for event in store.events_page(started["run_id"])["events"]]
            self.assertEqual(event_types.count("run.launch_recovered"),1)
        finally:
            store.close()

    def test_crash_after_runner_identity_before_gate_requeues_without_execution(self):
        with mock.patch.object(self.service,"_spawn_daemon",return_value=0):
            started=self.service.start(
                self.task,"identity-before-gate",_internal_fake_delay=.01,
            )
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            run=store.run(started["run_id"])
            expected_version=run["version"]
            package_path=run["package_path"]
            package_digest=run["package_digest"]
        finally:
            store.close()
        marker=self.root/"PRE_GATE_COMMAND_EXECUTED"
        context=multiprocessing.get_context("spawn")
        process=context.Process(
            target=crash_after_runner_identity,
            args=(
                str(self.runtime/"state.sqlite3"),str(self.runtime/"artifacts"),
                started["run_id"],expected_version,package_path,package_digest,
                str(self.repo),str(marker),
            ),
        )
        process.start(); process.join(timeout=10)
        if process.is_alive():
            process.terminate(); process.join(timeout=2)
        self.assertEqual(process.exitcode,24)

        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            attempt=store.attempt(started["run_id"])
            deadline=time.monotonic()+5
            while (inspect_process(
                    attempt["pid"],attempt["pgid"],attempt["process_start_id"],
                    )=="live" and time.monotonic()<deadline):
                time.sleep(.02)
            self.assertEqual(
                inspect_process(attempt["pid"],attempt["pgid"],attempt["process_start_id"]),
                "dead",
            )
            self.assertFalse(Path(attempt["child_record"]).exists())
            self.assertFalse(marker.exists())
        finally:
            store.close()

        resumed=self.service.resume(started["run_id"])
        self.assertTrue(resumed["launched"])
        self.assertEqual(resumed["disposition"],"continued")
        self.wait_state(started["run_id"],{"succeeded"})
        self.assertFalse(marker.exists())
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            attempts=store.connection.execute(
                "SELECT status FROM attempts WHERE run_id=? ORDER BY created_at",
                (started["run_id"],),
            ).fetchall()
            self.assertEqual([row["status"] for row in attempts],["recovery_required","finished"])
            events=store.events_page(started["run_id"],limit=1000)["events"]
            self.assertEqual(
                sum(event["type"]=="run.unstarted_attempt_recovered" for event in events),1,
            )
        finally:
            store.close()

    def test_dead_supervisor_with_live_child_never_relaunches(self):
        started=self.service.start(self.task,"daemon-crash",_internal_fake_delay=10)
        status=self.wait_state(started["run_id"],{"running"})
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            attempt=store.active_attempt(started["run_id"])
            owner=store.connection.execute("SELECT owner_id FROM supervisor_claims WHERE run_id=?",(started["run_id"],)).fetchone()[0]
            daemon_pid=int(owner.split(":",1)[1]); os.kill(daemon_pid,signal.SIGKILL)
            time.sleep(.1)
            resumed=self.service.resume(started["run_id"])
            self.assertFalse(resumed["launched"]); self.assertEqual(resumed["disposition"],"live")
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM attempts WHERE run_id=?",(started["run_id"],)).fetchone()[0],1)
            os.killpg(attempt["pgid"],signal.SIGKILL)
        finally: store.close()

    def test_runner_death_with_live_child_can_be_safely_cancelled(self):
        started=self.service.start(
            self.task,"orphan-child-cancel",_internal_fake_delay=30,
        )
        self.wait_state(started["run_id"],{"running"})
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        child=None
        try:
            attempt=store.attempt(started["run_id"])
            child_path=Path(attempt["child_record"])
            deadline=time.monotonic()+5
            while not child_path.is_file() and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertTrue(child_path.is_file())
            child=json.loads(child_path.read_text())
            self.assertEqual(
                inspect_process(child["pid"],child["pgid"],child["process_start_id"]),
                "live",
            )
            os.killpg(attempt["pgid"],signal.SIGKILL)
        finally:
            store.close()
        try:
            blocked=self.wait_state(started["run_id"],{"blocked"})
            self.assertEqual(blocked["phase"],"recovery_required")
            store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
            try:
                attempt=store.attempt(started["run_id"])
                self.assertEqual(attempt["status"],"ownership_ambiguous")
                self.assertEqual(
                    store.connection.execute(
                        "SELECT COUNT(*) FROM attempts WHERE run_id=?",(started["run_id"],),
                    ).fetchone()[0],1,
                )
            finally:
                store.close()
            retained=self.service.resume(started["run_id"],{
                "attempt_id":attempt["id"],"disposition":"retain_ownership",
            })
            self.assertFalse(retained["launched"])
            cancelled=self.service.cancel(started["run_id"])
            self.assertEqual(cancelled["state"],"cancelled")
            self.assertEqual(
                inspect_process(child["pid"],child["pgid"],child["process_start_id"]),
                "dead",
            )
            result=self.service.result(started["run_id"])
            receipt=json.loads(Path(next(
                artifact["path"] for artifact in result["artifacts"]
                if artifact["name"]=="result-receipt.json"
            )).read_text())
            self.assertEqual(receipt["attempt_id"],attempt["id"])
            self.assertEqual(receipt["phase"],"recovery_cleanup")
        finally:
            if child and inspect_process(
                    child["pid"],child["pgid"],child["process_start_id"],
                    )=="live":
                os.killpg(child["pgid"],signal.SIGKILL)

    def test_public_cancel_resumes_crashed_orphan_cleanup_once(self):
        started=self.service.start(
            self.task,"orphan-cancel-restart",_internal_fake_delay=30,
        )
        self.wait_state(started["run_id"],{"running"})
        child=None
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            attempt=store.attempt(started["run_id"])
            child_path=Path(attempt["child_record"])
            deadline=time.monotonic()+5
            while not child_path.is_file() and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertTrue(child_path.is_file())
            child=json.loads(child_path.read_text())
            self.assertEqual(
                inspect_process(child["pid"],child["pgid"],child["process_start_id"]),
                "live",
            )
            os.killpg(attempt["pgid"],signal.SIGKILL)
        finally:
            store.close()
        try:
            blocked=self.wait_state(started["run_id"],{"blocked"})
            self.assertEqual(blocked["phase"],"recovery_required")

            context=multiprocessing.get_context("spawn")
            process=context.Process(
                target=crash_after_recovery_cancel_commits,
                args=(
                    str(self.runtime/"state.sqlite3"),str(self.runtime/"artifacts"),
                    started["run_id"],
                ),
            )
            process.start(); process.join(timeout=10)
            if process.is_alive():
                process.terminate(); process.join(timeout=2)
            self.assertEqual(process.exitcode,26)
            interrupted=self.service.status(started["run_id"])
            self.assertEqual(
                (interrupted["state"],interrupted["phase"]),
                ("cancelling","recovery_cleanup"),
            )
            self.assertEqual(
                inspect_process(child["pid"],child["pgid"],child["process_start_id"]),
                "live",
            )

            first=self.service.cancel(started["run_id"])
            second=self.service.cancel(started["run_id"])
            self.assertEqual(first["state"],"cancelled")
            self.assertEqual(second,first)
            self.assertEqual(
                inspect_process(child["pid"],child["pgid"],child["process_start_id"]),
                "dead",
            )

            result=self.service.result(started["run_id"])
            receipts=[
                artifact for artifact in result["artifacts"]
                if artifact["name"]=="result-receipt.json"
            ]
            self.assertEqual(len(receipts),1)
            receipt=json.loads(Path(receipts[0]["path"]).read_text())
            self.assertEqual(receipt["phase"],"recovery_cleanup")
            events=self.service.events(started["run_id"],limit=1000)["events"]
            self.assertEqual(
                sum(event["type"]=="run.cancelling" for event in events),1,
            )
            self.assertEqual(
                sum(event["type"]=="run.cancelled" for event in events),1,
            )
        finally:
            if child and inspect_process(
                    child["pid"],child["pgid"],child["process_start_id"],
                    )=="live":
                os.killpg(child["pgid"],signal.SIGKILL)

    def test_orphan_artifact_is_not_a_result(self):
        with mock.patch.object(self.service,"_spawn_daemon",return_value=0):
            started=self.service.start(self.task,"orphan")
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try: store.finalize_artifact(started["run_id"],"orphan",b"bytes")
        finally: store.close()
        artifacts=self.service.result(started["run_id"])["artifacts"]
        self.assertEqual({item["name"] for item in artifacts},set(TERMINAL_REPORT_NAMES))
        self.assertNotIn("orphan", {item["name"] for item in artifacts})

    def test_pre_attempt_failure_and_cancellation_have_durable_receipts(self):
        failed=self.service.start(self.task,"capability-unavailable")
        self.assertEqual(failed["state"],"failed")
        failure_result=self.service.result(failed["run_id"])
        self.assertTrue(failure_result["ready"])
        failure_artifact=next(
            item for item in failure_result["artifacts"] if item["name"]=="receipt.json"
        )
        failure_receipt=json.loads(Path(failure_artifact["path"]).read_text())
        self.assertEqual(failure_receipt["state"],"failed")
        self.assertEqual(failure_receipt["error"]["error"],"CAPABILITY_UNAVAILABLE")

        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            preparing=store.claim_start(
                self.repo,"cancel-preparing",{"task":self.task,"supersedes_run_id":None},"owner",
            )
        finally:
            store.close()
        cancelled=self.service.cancel(preparing.run_id)
        self.assertEqual(cancelled["state"],"cancelled")
        cancellation_result=self.service.result(preparing.run_id)
        cancellation_receipt=json.loads(Path(cancellation_result["artifacts"][0]["path"]).read_text())
        self.assertTrue(cancellation_receipt["cancelled"])
        self.assertEqual(cancellation_receipt["phase"],"preparing")

    def test_public_preflight_freezes_profile_policy_and_alias_selection(self):
        base_oid=subprocess.run(
            ["git","-C",str(self.repo),"rev-parse","HEAD"],
            check=True,text=True,capture_output=True,
        ).stdout.strip()
        (self.repo/"src/app.py").write_text("VALUE = 'candidate'\n")
        subprocess.run(["git","-C",str(self.repo),"add","src/app.py"],check=True)
        subprocess.run(
            ["git","-C",str(self.repo),"commit","-qm","candidate"],check=True,
        )
        target_oid=subprocess.run(
            ["git","-C",str(self.repo),"rev-parse","HEAD"],
            check=True,text=True,capture_output=True,
        ).stdout.strip()
        self.task["project"]["base_ref"]=base_oid
        self.task["project"]["target_ref"]=target_oid
        (self.repo/"notes.txt").write_text("unrelated local work\n")
        before_head=subprocess.run(
            ["git","-C",str(self.repo),"rev-parse","HEAD"],
            check=True,capture_output=True,
        ).stdout
        before_status=subprocess.run(
            ["git","-C",str(self.repo),"status","--porcelain=v1","-z"],
            check=True,capture_output=True,
        ).stdout
        before_index=hashlib.sha256((self.repo/".git/index").read_bytes()).hexdigest()

        started=self.service.start(self.task,"frozen-routing")
        self.assertEqual(started["error"]["error"],"CAPABILITY_UNAVAILABLE")
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            snapshot=json.loads(store.run(started["run_id"])["mutable_snapshot"])
        finally:
            store.close()
        routed=snapshot["routing"]
        reviewer=routed["roles"]["reviewer"]
        self.assertEqual(reviewer["selected"]["profile_id"],"fixture-reviewer")
        self.assertEqual(
            reviewer["selected"]["binding"],
            {"alias":"review.deep","profile_id":"fixture-reviewer","version":1},
        )
        self.assertEqual(
            routed["profile_registry"]["sha256"],
            hashlib.sha256(self.profiles_json.encode()).hexdigest(),
        )
        self.assertEqual(
            routed["policy"]["sha256"],
            hashlib.sha256(self.policy_json.encode()).hexdigest(),
        )
        self.assertEqual(
            snapshot["configs"]["profiles_file"]["sha256"],
            routed["profile_registry"]["sha256"],
        )
        self.assertEqual(
            snapshot["configs"]["policy_file"]["sha256"],
            routed["policy"]["sha256"],
        )
        workspace=snapshot["workspace"]
        self.assertEqual(workspace["base_oid"],base_oid)
        self.assertEqual(workspace["target_oid"],target_oid)
        self.assertEqual(workspace["changed_paths"],["src/app.py"])
        identity={
            "schema_version":1,
            "base_oid":base_oid,
            "target_oid":target_oid,
            "changed_paths":["src/app.py"],
        }
        self.assertEqual(
            workspace["candidate_sha256"],
            hashlib.sha256(canonical_json(identity).encode()).hexdigest(),
        )
        frozen=Path(workspace["path"])
        check_workspace=Path(snapshot["check_workspace"]["path"])
        self.assertNotEqual(check_workspace,frozen)
        self.assertEqual(snapshot["check_workspace"]["target_oid"],target_oid)
        self.assertEqual((frozen/"src/app.py").read_text(),"VALUE = 'candidate'\n")
        self.assertEqual((check_workspace/"src/app.py").read_text(),"VALUE = 'candidate'\n")
        self.assertEqual(
            subprocess.run(
                ["git","-C",str(frozen),"rev-parse","--abbrev-ref","HEAD"],
                check=True,text=True,capture_output=True,
            ).stdout.strip(),
            "HEAD",
        )
        self.assertEqual(
            (
                subprocess.run(
                    ["git","-C",str(self.repo),"rev-parse","HEAD"],
                    check=True,capture_output=True,
                ).stdout,
                subprocess.run(
                    ["git","-C",str(self.repo),"status","--porcelain=v1","-z"],
                    check=True,capture_output=True,
                ).stdout,
                hashlib.sha256((self.repo/".git/index").read_bytes()).hexdigest(),
            ),
            (before_head,before_status,before_index),
        )

        (self.repo/"profiles.json").write_text("not valid after snapshot\n")
        (self.repo/"policy.json").write_text("also changed\n")
        replayed=self.service.start(self.task,"frozen-routing")
        self.assertEqual(replayed["run_id"],started["run_id"])
        self.assertFalse(replayed["created"])
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            self.assertEqual(
                json.loads(store.run(started["run_id"])["mutable_snapshot"]),snapshot,
            )
        finally:
            store.close()

    def test_public_preflight_reads_config_from_frozen_target_commit(self):
        frozen_target=subprocess.run(
            ["git","-C",str(self.repo),"rev-parse","HEAD"],
            check=True,text=True,capture_output=True,
        ).stdout.strip()
        (self.repo/"profiles.json").write_text("invalid current branch profile\n")
        (self.repo/"policy.json").write_text("invalid current branch policy\n")
        subprocess.run(["git","-C",str(self.repo),"add","profiles.json","policy.json"],check=True)
        subprocess.run(
            ["git","-C",str(self.repo),"commit","-qm","move current configs"],
            check=True,
        )
        task=json.loads(json.dumps(self.task))
        task["project"]["base_ref"]=frozen_target
        task["project"]["target_ref"]=frozen_target

        started=self.service.start(task,"target-config")
        self.assertEqual(started["error"]["error"],"CAPABILITY_UNAVAILABLE")
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            snapshot=json.loads(store.run(started["run_id"])["mutable_snapshot"])
        finally:
            store.close()
        self.assertEqual(snapshot["target_oid"],frozen_target)
        self.assertEqual(
            snapshot["configs"]["profiles_file"]["sha256"],
            hashlib.sha256(self.profiles_json.encode()).hexdigest(),
        )
        self.assertEqual(
            snapshot["configs"]["policy_file"]["sha256"],
            hashlib.sha256(self.policy_json.encode()).hexdigest(),
        )
        self.assertEqual(
            subprocess.run(
                ["git","-C",snapshot["workspace"]["path"],"rev-parse","HEAD"],
                check=True,text=True,capture_output=True,
            ).stdout.strip(),
            frozen_target,
        )

    def test_public_preflight_rejects_dirty_scoped_input_before_workspace(self):
        (self.repo/"src/uncommitted.py").write_text("dirty\n")
        started=self.service.start(self.task,"dirty-scope")
        self.assertEqual(started["error"]["error"],"PREPARATION_FAILED")
        self.assertIn("src/uncommitted.py",started["error"]["message"])
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            run=store.run(started["run_id"])
            self.assertIsNone(run["mutable_snapshot"])
            self.assertIsNotNone(store.artifact_named(
                started["run_id"],"result-receipt.json",
            ))
        finally:
            store.close()
        self.assertFalse(
            (self.runtime/"projects"/run["project_id"]/"runs"/
             started["run_id"]/"review-worktree").exists()
        )

    def test_invalid_predecessor_fails_with_run_context_and_receipt(self):
        started=self.service.start(
            self.task,"invalid-predecessor","does-not-exist",_internal_fake_delay=.01,
        )
        self.assertEqual(started["state"],"failed")
        self.assertEqual(started["error"]["error"],"PREPARATION_FAILED")
        self.assertIn("superseded run",started["error"]["message"])
        result=self.service.result(started["run_id"])
        self.assertTrue(result["ready"])
        self.assertEqual(
            {item["name"] for item in result["artifacts"]},
            set(TERMINAL_REPORT_NAMES),
        )
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            self.assertIsNone(store.run(started["run_id"])["supersedes_run_id"])
        finally:
            store.close()

    def test_public_active_and_foreign_predecessor_matrix(self):
        terminal=self.service.start(self.task,"terminal-predecessor")
        public=self.service.start(self.task,"public-successor",terminal["run_id"])

        with mock.patch.object(self.service,"_spawn_daemon",return_value=0):
            active=self.service.start(self.task,"active-predecessor",_internal_fake_delay=.2)
        against_active=self.service.start(
            self.task,"against-active",active["run_id"],_internal_fake_delay=.01,
        )

        other=self.root/"foreign-repo"
        subprocess.run(["git","init","-q",str(other)],check=True)
        subprocess.run(["git","-C",str(other),"config","user.email","test@example.invalid"],check=True)
        subprocess.run(["git","-C",str(other),"config","user.name","Test"],check=True)
        (other/"profiles.json").write_text(self.profiles_json)
        (other/"policy.json").write_text(self.policy_json)
        subprocess.run(["git","-C",str(other),"add","."],check=True)
        subprocess.run(["git","-C",str(other),"commit","-qm","foreign"],check=True)
        foreign_task=json.loads(json.dumps(self.task))
        foreign_task["project"]["repo_path"]=str(other)
        foreign=self.service.start(foreign_task,"foreign-predecessor")
        against_foreign=self.service.start(
            self.task,"against-foreign",foreign["run_id"],_internal_fake_delay=.01,
        )

        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            self.assertEqual(store.run(public["run_id"])["supersedes_run_id"],terminal["run_id"])
            self.assertIsNone(store.run(against_active["run_id"])["supersedes_run_id"])
            self.assertIsNone(store.run(against_foreign["run_id"])["supersedes_run_id"])
        finally:
            store.close()
        self.assertEqual(against_active["state"],"failed")
        self.assertEqual(against_foreign["state"],"failed")
        self.service.cancel(active["run_id"])

    def test_post_claim_snapshot_failure_has_run_context_and_receipt(self):
        task=json.loads(json.dumps(self.task))
        task["project"]["base_ref"]="refs/heads/does-not-exist"
        started=self.service.start(task,"invalid-moving-ref",_internal_fake_delay=.01)
        self.assertEqual(started["state"],"failed")
        self.assertEqual(started["error"]["error"],"PREPARATION_FAILED")
        self.assertIn("does not resolve",started["error"]["message"])
        result=self.service.result(started["run_id"])
        self.assertTrue(result["ready"])
        receipt_artifact=next(
            item for item in result["artifacts"] if item["name"]=="receipt.json"
        )
        receipt=json.loads(Path(receipt_artifact["path"]).read_text())
        self.assertEqual(receipt["run_id"],started["run_id"])
        self.assertEqual(receipt["error"],started["error"])

    def test_valid_predecessor_survives_an_unrelated_snapshot_failure(self):
        predecessor=self.service.start(self.task,"lineage-predecessor")
        task=json.loads(json.dumps(self.task))
        task["project"]["target_ref"]="refs/heads/does-not-exist"
        replacement=self.service.start(
            task,"lineage-replacement",predecessor["run_id"],_internal_fake_delay=.01,
        )
        self.assertEqual(replacement["state"],"failed")
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            run=store.run(replacement["run_id"])
            self.assertEqual(run["supersedes_run_id"],predecessor["run_id"])
        finally:
            store.close()

    def test_valid_predecessor_survives_repository_loss_during_recovery(self):
        predecessor=self.service.start(self.task,"lost-repo-predecessor")
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            replacement=store.claim_start(
                self.repo,
                "lost-repo-replacement",
                {
                    "task":self.task,
                    "supersedes_run_id":predecessor["run_id"],
                    "_internal_fake_delay":.01,
                },
                "dead-preflight-owner",
            )
        finally:
            store.close()
        self.repo.rename(self.root/"repo-moved-away")
        resumed=self.service.resume(replacement.run_id)
        self.assertEqual(resumed["disposition"],"preparation_failed")
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            run=store.run(replacement.run_id)
            self.assertEqual(run["supersedes_run_id"],predecessor["run_id"])
            self.assertIsNotNone(store.artifact_named(replacement.run_id,"result-receipt.json"))
        finally:
            store.close()

    def test_coordinator_crash_imports_runner_receipt_once(self):
        started=self.service.start(self.task,"receipt-recovery",_internal_fake_delay=.3)
        self.wait_state(started["run_id"],{"running"})
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            owner=store.connection.execute("SELECT owner_id FROM supervisor_claims WHERE run_id=?",(started["run_id"],)).fetchone()[0]
            os.kill(int(owner.split(":",1)[1]),signal.SIGKILL)
        finally: store.close()
        time.sleep(.6)
        first=self.service.resume(started["run_id"])
        self.assertIn(first["disposition"],{"succeeded","already_finalized"})
        self.assertTrue(self.service.result(started["run_id"])["ready"])
        with self.assertRaises(ConflictError): self.service.resume(started["run_id"])
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try: self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM attempts WHERE run_id=?",(started["run_id"],)).fetchone()[0],1)
        finally: store.close()

    def test_importer_crash_after_artifact_finalize_has_no_false_completion(self):
        started=self.service.start(
            self.task,"artifact-import-crash",_internal_fake_delay=.2,
        )
        self.wait_state(started["run_id"],{"running"})
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            attempt=store.attempt(started["run_id"])
            owner=store.connection.execute(
                "SELECT owner_id FROM supervisor_claims WHERE run_id=?",
                (started["run_id"],),
            ).fetchone()[0]
            os.kill(int(owner.split(":",1)[1]),signal.SIGKILL)
            receipt=Path(attempt["exit_record"])
            deadline=time.monotonic()+5
            while not receipt.is_file() and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertTrue(receipt.is_file())
            while (inspect_process(
                    attempt["pid"],attempt["pgid"],attempt["process_start_id"],
                    )=="live" and time.monotonic()<deadline):
                time.sleep(.02)
            self.assertNotEqual(
                inspect_process(attempt["pid"],attempt["pgid"],attempt["process_start_id"]),
                "live",
            )
        finally:
            store.close()

        context=multiprocessing.get_context("spawn")
        process=context.Process(
            target=crash_after_artifact_finalize_before_import,
            args=(
                str(self.runtime/"state.sqlite3"),
                str(self.runtime/"artifacts"),
                started["run_id"],
            ),
        )
        process.start(); process.join(timeout=10)
        if process.is_alive():
            process.terminate(); process.join(timeout=2)
        self.assertEqual(process.exitcode,25)

        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            self.assertEqual(store.run(started["run_id"])["state"],"running")
            self.assertEqual(store.artifacts_for_run(started["run_id"]),[])
            finalized=[
                path for path in (self.runtime/"artifacts"/started["run_id"]).iterdir()
                if path.is_file()
            ]
            self.assertEqual(len(finalized),3)
        finally:
            store.close()

        resumed=self.service.resume(started["run_id"])
        self.assertIn(resumed["disposition"],{"succeeded","already_finalized"})
        result=self.service.result(started["run_id"])
        self.assertTrue(result["ready"])
        self.assertEqual(len(result["artifacts"]),3)

    def test_two_processes_import_one_runner_receipt_atomically(self):
        started=self.service.start(self.task,"receipt-race",_internal_fake_delay=.3)
        self.wait_state(started["run_id"],{"running"})
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            attempt=store.attempt(started["run_id"])
            owner=store.connection.execute("SELECT owner_id FROM supervisor_claims WHERE run_id=?",(started["run_id"],)).fetchone()[0]
            os.kill(int(owner.split(":",1)[1]),signal.SIGKILL)
            receipt=Path(attempt["exit_record"])
            deadline=time.monotonic()+5
            while not receipt.is_file() and time.monotonic()<deadline: time.sleep(.02)
            self.assertTrue(receipt.is_file())
            while inspect_process(attempt["pid"],attempt["pgid"],attempt["process_start_id"])=="live" and time.monotonic()<deadline: time.sleep(.02)
            self.assertNotEqual(inspect_process(attempt["pid"],attempt["pgid"],attempt["process_start_id"]),"live")
        finally: store.close()

        context=multiprocessing.get_context("spawn")
        barrier,results=context.Barrier(2),context.Queue()
        processes=[context.Process(target=concurrent_receipt_import,args=(str(self.runtime/"state.sqlite3"),str(self.runtime/"artifacts"),started["run_id"],barrier,results)) for _ in range(2)]
        try:
            for process in processes: process.start()
            outcomes=[results.get(timeout=15) for _ in processes]
            for process in processes: process.join(timeout=2)
            self.assertTrue(all(outcome in {"succeeded","already_finalized"} for outcome in outcomes),outcomes)
            self.assertTrue(all(process.exitcode==0 for process in processes))
        finally:
            for process in processes:
                if process.is_alive(): process.terminate()
                process.join(timeout=2)
            results.close(); results.join_thread()

        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            run=store.run(started["run_id"])
            self.assertEqual(run["state"],"succeeded")
            self.assertEqual(store.attempt(started["run_id"])["status"],"finished")
            self.assertEqual(len(store.artifacts_for_run(started["run_id"])),3)
            events=store.connection.execute("SELECT run_version,type FROM events WHERE run_id=?",(started["run_id"],)).fetchall()
            event_types=[row["type"] for row in events]
            self.assertEqual(event_types.count("attempt.output"),1)
            self.assertEqual(event_types.count("run.succeeded"),1)
            self.assertNotIn("run.blocked",event_types)
            self.assertEqual(run["version"],max(row["run_version"] for row in events))
        finally: store.close()

    def test_repository_devsquad_package_cannot_shadow_frozen_gate_modules(self):
        marker=self.root/"SHADOW_EXECUTED"
        shadow=self.repo/"devsquad"; shadow.mkdir()
        sentinel=f"from pathlib import Path\nPath({str(marker)!r}).write_text('shadowed')\n"
        (shadow/"__init__.py").write_text(sentinel)
        for module in ("attempt_runner.py","worker_gate.py","fake_step.py"):
            (shadow/module).write_text(sentinel+"raise SystemExit(91)\n")
        started=self.service.start(self.task,"repo-shadow",_internal_fake_delay=.01)
        self.wait_state(started["run_id"],{"succeeded"})
        self.assertFalse(marker.exists())

    def test_supersedes_requires_terminal_same_project(self):
        predecessor=self.service.start(self.task,"predecessor")
        replacement=self.service.start(self.task,"replacement",predecessor["run_id"],_internal_fake_delay=.01)
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try: self.assertEqual(store.run(replacement["run_id"])["supersedes_run_id"],predecessor["run_id"])
        finally: store.close()
        self.wait_state(replacement["run_id"],{"succeeded"})


if __name__=="__main__": unittest.main()
