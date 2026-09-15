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

from devsquad.contracts import ExecutionIdentity, LaunchSpec
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
        (other/"profiles.json").write_text("{}\n")
        (other/"policy.json").write_text("{}\n")
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
        receipt=json.loads(Path(result["artifacts"][0]["path"]).read_text())
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
