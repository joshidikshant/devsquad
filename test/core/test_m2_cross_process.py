"""Independent-process races for the public M2 service and writer fences."""
from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin" / "core" / "src"))

from devsquad.service import Service
from devsquad.store import Store


def service_start(runtime, task, key, barrier, results):
    try:
        barrier.wait(timeout=10)
        value = Service(Path(runtime)).start(task, key)
        results.put(("ok", value["run_id"], value["created"], value["state"]))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))


def service_cancel(runtime, run_id, barrier, results):
    try:
        barrier.wait(timeout=10)
        value = Service(Path(runtime)).cancel(run_id)
        results.put(("ok", value["state"], value["version"]))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))


def reserve_writer(database, artifacts, run_id, version, owner, barrier, results):
    store = None
    try:
        store = Store(Path(database), Path(artifacts))
        barrier.wait(timeout=10)
        reservation = store.reserve_attempt(run_id, version, owner, "package")
        results.put(("ok", run_id, reservation.attempt_id))
    except Exception as exc:
        results.put(("error", run_id, type(exc).__name__, str(exc)))
    finally:
        if store is not None:
            store.close()


class CrossProcessServiceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="devsquad-process-races-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.runtime = self.root / "runtime"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Test"], check=True)
        (self.repo / "profiles.json").write_text("{}\n")
        (self.repo / "policy.json").write_text("{}\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "base"], check=True)
        self.task = json.loads((ROOT / "docs/plans/engineering-team/examples/branch-review.json").read_text())
        self.task["project"] = {
            "repo_path": str(self.repo), "base_ref": "HEAD", "target_ref": "HEAD",
        }
        self.task["routing"]["profiles_file"] = "profiles.json"
        self.task["routing"]["policy_file"] = "policy.json"
        self.context = multiprocessing.get_context("spawn")

    def run_processes(self, targets):
        results = self.context.Queue()
        barrier = self.context.Barrier(len(targets))
        processes = [self.context.Process(target=target, args=(*args, barrier, results)) for target,args in targets]
        try:
            for process in processes:
                process.start()
            outcomes = [results.get(timeout=20) for _ in processes]
            for process in processes:
                process.join(timeout=5)
            self.assertTrue(all(process.exitcode == 0 for process in processes), processes)
            return outcomes
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=2)
            results.close()
            results.join_thread()

    def test_identical_public_starts_share_one_run_across_processes(self):
        args = (str(self.runtime), self.task, "same-process-key")
        outcomes = self.run_processes([(service_start,args),(service_start,args)])
        self.assertTrue(all(outcome[0] == "ok" for outcome in outcomes), outcomes)
        self.assertEqual(len({outcome[1] for outcome in outcomes}), 1)
        self.assertEqual(sorted(outcome[2] for outcome in outcomes), [False, True])
        run_id = outcomes[0][1]
        result = Service(self.runtime).result(run_id)
        self.assertTrue(result["ready"])
        self.assertEqual([item["name"] for item in result["artifacts"]], ["result-receipt.json"])

    def test_changed_body_conflicts_with_same_key_across_processes(self):
        changed = json.loads(json.dumps(self.task))
        changed["goal"] += " changed"
        outcomes = self.run_processes([
            (service_start,(str(self.runtime),self.task,"conflicting-process-key")),
            (service_start,(str(self.runtime),changed,"conflicting-process-key")),
        ])
        self.assertEqual(sorted(outcome[0] for outcome in outcomes), ["error", "ok"])
        error = next(outcome for outcome in outcomes if outcome[0] == "error")
        self.assertEqual(error[1], "ConflictError")

    def test_two_processes_cannot_reserve_two_worktree_writers(self):
        store = Store(self.runtime / "state.sqlite3", self.runtime / "artifacts")
        try:
            ready = []
            for key in ("writer-a", "writer-b"):
                claim = store.claim_start(self.repo, key, {"task": key}, "preflight")
                version = store.complete_preparation(claim.run_id, claim.fencing_token, {"head": key})
                ready.append((claim.run_id, version))
        finally:
            store.close()
        common = (str(self.runtime / "state.sqlite3"), str(self.runtime / "artifacts"))
        outcomes = self.run_processes([
            (reserve_writer,(*common,*ready[0],"owner-a")),
            (reserve_writer,(*common,*ready[1],"owner-b")),
        ])
        self.assertEqual(sorted(outcome[0] for outcome in outcomes), ["error", "ok"])
        self.assertEqual(next(item for item in outcomes if item[0] == "error")[2], "ConflictError")
        winner = next(item for item in outcomes if item[0] == "ok")[1]
        store = Store(self.runtime / "state.sqlite3", self.runtime / "artifacts")
        try:
            store.cancel_launching(winner)
        finally:
            store.close()

    def test_repeated_cancel_is_idempotent_across_processes(self):
        store = Store(self.runtime / "state.sqlite3", self.runtime / "artifacts")
        try:
            claim = store.claim_start(self.repo, "cancel-race", {"task": 1}, "preflight")
            store.complete_preparation(claim.run_id, claim.fencing_token, {"head": "fixed"})
        finally:
            store.close()
        args = (str(self.runtime), claim.run_id)
        outcomes = self.run_processes([(service_cancel,args),(service_cancel,args)])
        self.assertTrue(all(outcome[0:2] == ("ok", "cancelled") for outcome in outcomes), outcomes)
        self.assertEqual(len({outcome[2] for outcome in outcomes}), 1)
        result = Service(self.runtime).result(claim.run_id)
        self.assertEqual([item["name"] for item in result["artifacts"]], ["result-receipt.json"])


if __name__ == "__main__":
    unittest.main()
