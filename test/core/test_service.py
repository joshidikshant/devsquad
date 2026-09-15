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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.service import Service
from devsquad.store import ConflictError, Store
from devsquad.supervisor import Supervisor, inspect_process


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


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devsquad-service-")
        self.root = Path(self.temp.name); self.repo = self.root / "repo"; self.runtime = self.root / "runtime"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Test"], check=True)
        (self.repo / "profiles.json").write_text("{}\n"); (self.repo / "policy.json").write_text("{}\n")
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

    def test_orphan_artifact_is_not_a_result(self):
        with mock.patch.object(self.service,"_spawn_daemon",return_value=0):
            started=self.service.start(self.task,"orphan")
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try: store.finalize_artifact(started["run_id"],"orphan",b"bytes")
        finally: store.close()
        artifacts=self.service.result(started["run_id"])["artifacts"]
        self.assertEqual([item["name"] for item in artifacts],["result-receipt.json"])

    def test_pre_attempt_failure_and_cancellation_have_durable_receipts(self):
        failed=self.service.start(self.task,"capability-unavailable")
        self.assertEqual(failed["state"],"failed")
        failure_result=self.service.result(failed["run_id"])
        self.assertTrue(failure_result["ready"])
        failure_receipt=json.loads(Path(failure_result["artifacts"][0]["path"]).read_text())
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

    def test_invalid_predecessor_fails_with_run_context_and_receipt(self):
        started=self.service.start(
            self.task,"invalid-predecessor","does-not-exist",_internal_fake_delay=.01,
        )
        self.assertEqual(started["state"],"failed")
        self.assertEqual(started["error"]["error"],"PREPARATION_FAILED")
        self.assertIn("superseded run",started["error"]["message"])
        result=self.service.result(started["run_id"])
        self.assertTrue(result["ready"])
        self.assertEqual([item["name"] for item in result["artifacts"]],["result-receipt.json"])
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try:
            self.assertIsNone(store.run(started["run_id"])["supersedes_run_id"])
        finally:
            store.close()

    def test_post_claim_snapshot_failure_has_run_context_and_receipt(self):
        task=json.loads(json.dumps(self.task))
        task["project"]["base_ref"]="refs/heads/does-not-exist"
        started=self.service.start(task,"invalid-moving-ref",_internal_fake_delay=.01)
        self.assertEqual(started["state"],"failed")
        self.assertEqual(started["error"]["error"],"PREPARATION_FAILED")
        self.assertIn("does not resolve",started["error"]["message"])
        result=self.service.result(started["run_id"])
        self.assertTrue(result["ready"])
        receipt=json.loads(Path(result["artifacts"][0]["path"]).read_text())
        self.assertEqual(receipt["run_id"],started["run_id"])
        self.assertEqual(receipt["error"],started["error"])

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
