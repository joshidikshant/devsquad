"""Independent adversarial gates for M2 process supervision."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin" / "core" / "src"))

from devsquad.contracts import ExecutionIdentity, LaunchSpec
from devsquad.store import ConflictError, Store
from devsquad.supervisor import Supervisor, inspect_process, process_start_identity


class SupervisorGateReview(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="devsquad-supervisor-gate-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.store = Store(self.root / "runtime.sqlite3", self.root / "artifacts")
        self.addCleanup(self.store.close)
        self.supervisor = Supervisor(self.store, output_limit=32, grace_seconds=0.1)

    def _ready_run(self, key: str):
        claim = self.store.claim_start(self.repo, key, {"task": key}, "preflight")
        version = self.store.complete_preparation(claim.run_id, claim.fencing_token, {"head": "fixed"})
        return claim.run_id, version

    def _spec(self, *argv: str, stdin_path: str | None = None) -> LaunchSpec:
        return LaunchSpec(
            1, "fake", "cli_exec", tuple(argv), str(self.repo), stdin_path, 5,
            ExecutionIdentity("fake", "1", "fixture", "fixture", "fixture", "low"),
        )

    @staticmethod
    def _force_cleanup(handle) -> None:
        if handle.process.poll() is None:
            try:
                os.killpg(handle.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            handle.process.wait(timeout=2)
        for drain in (handle.stdout, handle.stderr):
            drain.thread.join(timeout=2)
            drain.stream.close()

    def test_current_process_has_stable_strong_identity(self):
        first = process_start_identity(os.getpid())
        second = process_start_identity(os.getpid())
        self.assertIsInstance(first, str)
        self.assertEqual(first, second)
        self.assertEqual(inspect_process(os.getpid(), os.getpgid(os.getpid()), first), "live")

    def test_fast_success_is_a_valid_attempt(self):
        run_id, version = self._ready_run("fast")
        true_binary = shutil.which("true")
        self.assertIsNotNone(true_binary)
        handle = self.supervisor.launch(run_id, version, self._spec(true_binary), "owner", "package")
        self.assertEqual(self.supervisor.wait(handle, 2), 0)
        self.assertEqual(self.store.run(run_id)["state"], "succeeded")

    def test_spawn_failure_is_fenced_and_releases_worktree_writer(self):
        run_id, version = self._ready_run("missing")
        with self.assertRaises(FileNotFoundError):
            self.supervisor.launch(run_id, version, self._spec("/definitely/missing/devsquad-worker"), "owner", "package")
        self.assertEqual(self.store.run(run_id)["state"], "blocked")
        self.assertIsNone(self.store.active_attempt(run_id))
        second, second_version = self._ready_run("after-missing")
        reservation = self.store.reserve_attempt(second, second_version, "other-owner", "package")
        self.assertTrue(reservation.attempt_token)

    def test_output_is_bounded_but_full_stream_is_accounted(self):
        run_id, version = self._ready_run("output")
        code = "import sys;sys.stdout.write('o'*1000);sys.stderr.write('e'*2000)"
        handle = self.supervisor.launch(run_id, version, self._spec(sys.executable, "-c", code), "owner", "package")
        self.assertEqual(self.supervisor.wait(handle, 3), 0)
        attempt = self.store.connection.execute("SELECT * FROM attempts WHERE run_id=?", (run_id,)).fetchone()
        metadata = json.loads(attempt["output_metadata"])
        self.assertEqual(metadata["stdout"], {
            "total_bytes": 1000, "captured_bytes": 32, "truncated": True,
            "full_sha256": hashlib.sha256(b"o" * 1000).hexdigest(),
        })
        self.assertEqual(metadata["stderr"]["total_bytes"], 2000)
        self.assertEqual(metadata["stderr"]["captured_bytes"], 32)
        for column in ("stdout_artifact_id", "stderr_artifact_id"):
            artifact = self.store.connection.execute(
                "SELECT path,byte_size FROM artifacts WHERE id=?", (attempt[column],),
            ).fetchone()
            self.assertEqual(artifact["byte_size"], 32)
            self.assertEqual(Path(artifact["path"]).stat().st_size, 32)

    def test_database_fences_second_writer_for_same_worktree(self):
        first_run, first_version = self._ready_run("writer-one")
        handle = self.supervisor.launch(
            first_run, first_version, self._spec(sys.executable, "-c", "import time;time.sleep(30)"),
            "owner-one", "package",
        )
        self.addCleanup(self._force_cleanup, handle)
        second_run, second_version = self._ready_run("writer-two")
        with self.assertRaises(ConflictError):
            self.store.reserve_attempt(second_run, second_version, "owner-two", "package")
        self.supervisor.cancel(first_run, handle)
        self.assertEqual(self.store.run(first_run)["state"], "cancelled")

    def test_durable_timeout_is_failure_when_term_handler_exits_zero(self):
        run_id, version = self._ready_run("timeout-zero")
        code = (
            "import signal,sys,time;"
            "signal.signal(signal.SIGTERM,lambda *_:sys.exit(0));"
            "time.sleep(30)"
        )
        spec = LaunchSpec(
            1, "fake", "cli_exec", (sys.executable, "-c", code), str(self.repo), None, 1,
            ExecutionIdentity("fake", "1", "fixture", "fixture", "fixture", "low"),
        )
        source = str(ROOT / "plugin" / "core" / "src")
        with mock.patch.dict(os.environ, {"PYTHONPATH": source}):
            handle = self.supervisor.launch_durable(run_id, version, spec, "owner", "package")
        self.assertEqual(self.supervisor.wait_durable(handle, 1), 124)
        self.assertEqual(self.store.run(run_id)["state"], "failed")
        receipt_artifact = self.store.artifact_named(run_id, "result-receipt.json")
        receipt = json.loads(Path(receipt_artifact["path"]).read_text())
        self.assertTrue(receipt["timed_out"])
        self.assertEqual(receipt["returncode"], 0)
        self.assertEqual(receipt["error"], "TIMEOUT")
        terminal = self.store.connection.execute(
            "SELECT payload FROM events WHERE run_id=? AND type='run.failed'", (run_id,),
        ).fetchone()
        self.assertEqual(json.loads(terminal["payload"])["error"], "TIMEOUT")
        self.assertEqual(self.store.attempt(run_id)["status"], "finished")

    def test_recovery_never_signals_an_ambiguous_identity(self):
        run_id, version = self._ready_run("ambiguous")
        handle = self.supervisor.launch(
            run_id, version, self._spec(sys.executable, "-c", "import time;time.sleep(30)"),
            "owner", "package",
        )
        self.addCleanup(self._force_cleanup, handle)
        with mock.patch("devsquad.supervisor.process_start_identity", return_value="different-start"), \
             mock.patch("devsquad.supervisor.os.killpg") as killpg:
            self.assertEqual(self.supervisor.recover(run_id), "RECOVERY_REQUIRED")
            killpg.assert_not_called()
        self.assertEqual(self.store.run(run_id)["state"], "blocked")
        attempt = self.store.connection.execute(
            "SELECT status FROM attempts WHERE run_id=?", (run_id,),
        ).fetchone()
        self.assertEqual(attempt["status"], "ownership_ambiguous")
        second, second_version = self._ready_run("after-ambiguous")
        with self.assertRaises(ConflictError):
            self.store.reserve_attempt(second, second_version, "other-owner", "package")

    def test_confirmed_dead_recovery_releases_writer_fence(self):
        run_id, version = self._ready_run("dead")
        handle = self.supervisor.launch(
            run_id, version, self._spec(sys.executable, "-c", "pass"), "owner", "package",
        )
        self.addCleanup(self._force_cleanup, handle)
        handle.process.wait(timeout=2)
        handle.stdout.thread.join(timeout=2)
        handle.stderr.thread.join(timeout=2)
        self.assertEqual(self.supervisor.recover(run_id), "RECOVERY_REQUIRED")
        attempt = self.store.connection.execute(
            "SELECT status FROM attempts WHERE run_id=?", (run_id,),
        ).fetchone()
        self.assertEqual(attempt["status"], "recovery_required")
        second, second_version = self._ready_run("after-dead")
        reservation = self.store.reserve_attempt(second, second_version, "other-owner", "package")
        self.assertTrue(reservation.attempt_token)


if __name__ == "__main__":
    unittest.main()
