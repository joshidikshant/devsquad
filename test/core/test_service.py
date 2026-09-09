import json
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
        self.assertTrue(result["ready"]); self.assertEqual(len(result["artifacts"]),2)
        page=self.service.events(first["run_id"],0,2)
        self.assertEqual(len(page["events"]),2); self.assertIsNotNone(page["next_cursor"])
        with self.assertRaises(ConflictError): self.service.resume(first["run_id"])

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
            self.assertFalse(resumed["launched"]); self.assertEqual(resumed["disposition"],"live_owned")
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM attempts WHERE run_id=?",(started["run_id"],)).fetchone()[0],1)
            os.killpg(attempt["pgid"],signal.SIGKILL)
        finally: store.close()

    def test_orphan_artifact_is_not_a_result(self):
        with mock.patch.object(self.service,"_spawn_daemon",return_value=0):
            started=self.service.start(self.task,"orphan")
        store=Store(self.runtime/"state.sqlite3",self.runtime/"artifacts")
        try: store.finalize_artifact(started["run_id"],"orphan",b"bytes")
        finally: store.close()
        self.assertEqual(self.service.result(started["run_id"])["artifacts"],[])


if __name__=="__main__": unittest.main()
