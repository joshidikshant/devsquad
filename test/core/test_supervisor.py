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

from devsquad.contracts import ExecutionIdentity, LaunchSpec
from devsquad.store import ConflictError, Store
from devsquad.supervisor import Supervisor, _live_group_exists, inspect_process, process_start_identity


class SupervisorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devsquad-supervisor-")
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.store = Store(self.root / "state.sqlite3", self.root / "artifacts")
        self.supervisor = Supervisor(self.store, output_limit=32, grace_seconds=0.1)
        self.handles = []

    def tearDown(self):
        for handle in self.handles:
            if handle.process.poll() is None:
                try: os.killpg(handle.process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                handle.process.wait(timeout=2)
            handle.stdout.thread.join(timeout=1); handle.stderr.thread.join(timeout=1)
            if not handle.stdout.stream.closed: handle.stdout.stream.close()
            if not handle.stderr.stream.closed: handle.stderr.stream.close()
        self.store.close(); self.temp.cleanup()

    def prepared(self, key, argv):
        claim = self.store.claim_start(self.repo, key, {"task": key}, "prepare")
        version = self.store.complete_preparation(claim.run_id, claim.fencing_token, {"head": "fixed"})
        identity = ExecutionIdentity("fake", "1", None, None, None, None)
        spec = LaunchSpec(1, "fake", "cli_exec", tuple(argv), str(self.repo), None, 5, identity, {})
        return claim.run_id, version, spec

    def launch(self, key, code):
        run_id, version, spec = self.prepared(key, (sys.executable, "-c", code))
        handle = self.supervisor.launch(run_id, version, spec, "supervisor", "package-sha")
        self.handles.append(handle)
        return run_id, handle

    def test_strong_process_identity_is_stable_and_live(self):
        first = process_start_identity(os.getpid())
        self.assertIsNotNone(first)
        self.assertEqual(first, process_start_identity(os.getpid()))
        self.assertEqual(inspect_process(os.getpid(), os.getpgrp(), first), "live")

    def test_process_inventory_failure_never_means_absence(self):
        failed = subprocess.CompletedProcess(["/bin/ps"], 1, "", "denied")
        with mock.patch("devsquad.supervisor.subprocess.run", return_value=failed), self.assertRaises(RuntimeError):
            _live_group_exists(123)

    def test_zombie_only_group_is_dead_but_live_unknown_group_is_ambiguous(self):
        with (
            mock.patch("devsquad.supervisor.process_start_identity", return_value=None),
            mock.patch("devsquad.supervisor.os.killpg"),
            mock.patch("devsquad.supervisor._live_group_exists", return_value=False),
        ):
            self.assertEqual(inspect_process(123, 123, "expected"), "dead")
        with (
            mock.patch("devsquad.supervisor.process_start_identity", return_value=None),
            mock.patch("devsquad.supervisor.os.killpg"),
            mock.patch("devsquad.supervisor._live_group_exists", return_value=True),
        ):
            self.assertEqual(inspect_process(123, 123, "expected"), "ambiguous")

    def test_normal_and_fast_completion_persist_bounded_output(self):
        run_id, handle = self.launch("output", "import sys; print('o'*100); print('e'*100,file=sys.stderr)")
        self.assertEqual(self.supervisor.wait(handle, 3), 0)
        run = self.store.run(run_id)
        self.assertEqual(run["state"], "succeeded")
        attempt = self.store.connection.execute("SELECT * FROM attempts WHERE run_id=?", (run_id,)).fetchone()
        metadata = json.loads(attempt["output_metadata"])
        self.assertTrue(metadata["stdout"]["truncated"] and metadata["stderr"]["truncated"])
        for column in ("stdout_artifact_id", "stderr_artifact_id"):
            artifact = self.store.connection.execute("SELECT byte_size FROM artifacts WHERE id=?", (attempt[column],)).fetchone()
            self.assertLessEqual(artifact[0], 32)
        fast_id, version, spec = self.prepared("fast", ("/usr/bin/true",))
        fast = self.supervisor.launch(fast_id, version, spec, "supervisor", "package-sha")
        self.handles.append(fast)
        self.assertEqual(self.supervisor.wait(fast, 2), 0)
        self.assertEqual(self.store.run(fast_id)["state"], "succeeded")

    def test_one_active_writer_per_worktree(self):
        first_id, first = self.launch("one", "import time; time.sleep(30)")
        second_id, version, spec = self.prepared("two", (sys.executable, "-c", "print('never')"))
        with self.assertRaises(ConflictError):
            self.supervisor.launch(second_id, version, spec, "other", "package-sha")
        self.supervisor.cancel(first_id, first)

    def test_timeout_kills_term_ignoring_root_and_descendant(self):
        code = """import os,signal,time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
if os.fork()==0:
 signal.signal(signal.SIGTERM, signal.SIG_IGN)
 while True: time.sleep(1)
while True: time.sleep(1)
"""
        run_id, handle = self.launch("timeout", code)
        pgid = handle.process.pid
        self.assertEqual(self.supervisor.wait(handle, 0.2), 124)
        with self.assertRaises(ProcessLookupError): os.killpg(pgid, 0)
        self.assertEqual(self.store.run(run_id)["state"], "failed")

    def test_cancel_is_idempotent_after_confirmed_cleanup(self):
        run_id, handle = self.launch("cancel", "import time; time.sleep(30)")
        version = self.supervisor.cancel(run_id, handle)
        self.assertEqual(self.store.run(run_id)["state"], "cancelled")
        self.assertEqual(self.supervisor.cancel(run_id), version)

    def test_recovery_live_dead_and_reused_identity_never_relaunches_or_signals(self):
        live_id, live = self.launch("live", "import time; time.sleep(30)")
        self.assertEqual(Supervisor(self.store).recover(live_id), "live_owned")
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM attempts WHERE run_id=?", (live_id,)).fetchone()[0], 1)
        self.supervisor.cancel(live_id, live)

        dead_id, dead = self.launch("dead", "pass")
        dead.process.wait(timeout=2); dead.stdout.thread.join(1); dead.stderr.thread.join(1)
        self.assertEqual(self.supervisor.recover(dead_id), "RECOVERY_REQUIRED")
        self.assertEqual(self.store.run(dead_id)["state"], "blocked")

        reused_id, reused = self.launch("reused", "import time; time.sleep(30)")
        self.store.connection.execute("UPDATE attempts SET process_start_id='different' WHERE run_id=?", (reused_id,))
        with mock.patch("devsquad.supervisor.os.killpg") as signal_group:
            self.assertEqual(self.supervisor.recover(reused_id), "RECOVERY_REQUIRED")
            signal_group.assert_not_called()
        os.killpg(reused.process.pid, signal.SIGKILL); reused.process.wait(timeout=2)

    def test_spawn_failure_leaves_safe_blocked_reservation(self):
        run_id, version, spec = self.prepared("spawn-fail", (str(self.root / "missing"),))
        with self.assertRaises(OSError):
            self.supervisor.launch(run_id, version, spec, "supervisor", "package-sha")
        self.assertEqual((self.store.run(run_id)["state"], self.store.run(run_id)["phase"]), ("blocked", "recovery_required"))
        self.assertIsNone(self.store.active_attempt(run_id))


if __name__ == "__main__": unittest.main()
