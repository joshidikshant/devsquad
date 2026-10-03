"""Independent-process races for the public M2 service and writer fences."""
from __future__ import annotations

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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin" / "core" / "src"))

from devsquad.service import Service
from devsquad.reports import TERMINAL_REPORT_NAMES
from devsquad.store import Store, request_hash
from devsquad.supervisor import inspect_process
from devsquad_test_fixtures import branch_review_routing_documents


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


def service_handoff_claim(runtime, run_id, version, owner, barrier, results):
    try:
        barrier.wait(timeout=10)
        value = Service(Path(runtime)).handoff_claim(run_id, version, owner)
        results.put((
            "ok", value["claim"]["owner"], value["claim"]["fencing_token"],
        ))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))


def service_handoff_complete(runtime, run_id, claim, decision, barrier, results):
    try:
        barrier.wait(timeout=10)
        value = Service(Path(runtime)).handoff_complete(run_id, claim, decision)
        results.put((
            "ok", value["replayed"], value["recorded_run_version"],
        ))
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
        profiles_json, policy_json = branch_review_routing_documents()
        (self.repo / "profiles.json").write_text(profiles_json)
        (self.repo / "policy.json").write_text(policy_json)
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

    def waiting_handoff(self, key):
        store = Store(self.runtime / "state.sqlite3", self.runtime / "artifacts")
        try:
            claim = store.claim_start(self.repo, key, {"task": key}, "preflight")
            version = store.complete_preparation(
                claim.run_id,
                claim.fencing_token,
                {"head": "fixed"},
                package_path="/frozen/package",
                package_digest="package-digest",
            )
            reservation = store.reserve_attempt(
                claim.run_id, version, "supervisor", "package-digest",
            )
            version = store.mark_attempt_running(
                reservation, 101, 101, "process-start-id",
            )
            handoff = store.publish_handoff(
                claim.run_id,
                version,
                reservation.attempt_token,
                reservation.supervisor_token,
                {"schema_version": 1, "candidate_sha256": "c" * 64},
            )
            return claim.run_id, handoff.run_version
        finally:
            store.close()

    def wait_state(self, run_id, expected, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = Service(self.runtime).status(run_id)
            if status["state"] in expected:
                return status
            time.sleep(0.05)
        self.fail(f"run did not reach {expected}: {Service(self.runtime).status(run_id)}")

    def test_identical_public_starts_share_one_run_across_processes(self):
        args = (str(self.runtime), self.task, "same-process-key")
        outcomes = self.run_processes([(service_start,args),(service_start,args)])
        self.assertTrue(all(outcome[0] == "ok" for outcome in outcomes), outcomes)
        self.assertEqual(len({outcome[1] for outcome in outcomes}), 1)
        self.assertEqual(sorted(outcome[2] for outcome in outcomes), [False, True])
        run_id = outcomes[0][1]
        result = Service(self.runtime).result(run_id)
        self.assertTrue(result["ready"])
        self.assertEqual(
            {item["name"] for item in result["artifacts"]},
            set(TERMINAL_REPORT_NAMES),
        )

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

    def test_running_cancel_race_reaps_runner_and_worker_once(self):
        service = Service(self.runtime)
        started = service.start(
            self.task, "running-cancel-race", _internal_fake_delay=30,
        )
        self.wait_state(started["run_id"], {"running"})
        store = Store(self.runtime / "state.sqlite3", self.runtime / "artifacts")
        child = None
        try:
            attempt = store.attempt(started["run_id"])
            child_path = Path(attempt["child_record"])
            deadline = time.monotonic() + 5
            while not child_path.is_file() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(child_path.is_file())
            child = json.loads(child_path.read_text())
        finally:
            store.close()
        try:
            args = (str(self.runtime), started["run_id"])
            outcomes = self.run_processes([(service_cancel, args), (service_cancel, args)])
            self.assertTrue(all(outcome[0] == "ok" for outcome in outcomes), outcomes)
            self.assertTrue(all(outcome[1] in {"cancelling", "cancelled"} for outcome in outcomes))
            self.wait_state(started["run_id"], {"cancelled"})

            deadline = time.monotonic() + 5
            while (inspect_process(
                    attempt["pid"], attempt["pgid"], attempt["process_start_id"],
                    ) != "dead" and time.monotonic() < deadline):
                time.sleep(0.02)
            self.assertEqual(
                inspect_process(attempt["pid"], attempt["pgid"], attempt["process_start_id"]),
                "dead",
            )
            self.assertEqual(
                inspect_process(child["pid"], child["pgid"], child["process_start_id"]),
                "dead",
            )
            result = service.result(started["run_id"])
            self.assertTrue(result["ready"])
            store = Store(self.runtime / "state.sqlite3", self.runtime / "artifacts")
            try:
                types = [
                    event["type"]
                    for event in store.events_page(started["run_id"], limit=1000)["events"]
                ]
                self.assertEqual(types.count("run.cancelling"), 1)
                self.assertEqual(types.count("run.cancelled"), 1)
            finally:
                store.close()
        finally:
            if child and inspect_process(
                    child["pid"], child["pgid"], child["process_start_id"],
                    ) == "live":
                os.killpg(child["pgid"], signal.SIGKILL)

    def test_two_processes_cannot_claim_one_host_handoff(self):
        run_id, version = self.waiting_handoff("handoff-claim-race")
        outcomes = self.run_processes([
            (service_handoff_claim, (str(self.runtime), run_id, version, "host-a")),
            (service_handoff_claim, (str(self.runtime), run_id, version, "host-b")),
        ])
        self.assertEqual(sorted(outcome[0] for outcome in outcomes), ["error", "ok"])
        self.assertEqual(
            next(outcome for outcome in outcomes if outcome[0] == "error")[1],
            "ConflictError",
        )

    def test_identical_handoff_completions_replay_across_processes(self):
        run_id, version = self.waiting_handoff("handoff-complete-race")
        acquired = Service(self.runtime).handoff_claim(run_id, version, "host-a")
        body = {
            "schema_version": 1,
            "submission_id": "submission-1",
            "disposition": "accept",
            "reason": "accepted",
            "evidence_refs": [],
        }
        decision = {**body, "submission_hash": request_hash(body)}
        common = (str(self.runtime), run_id, acquired["claim"], decision)
        outcomes = self.run_processes([
            (service_handoff_complete, common),
            (service_handoff_complete, common),
        ])
        self.assertTrue(all(outcome[0] == "ok" for outcome in outcomes), outcomes)
        self.assertEqual(sorted(outcome[1] for outcome in outcomes), [False, True])
        self.assertEqual(len({outcome[2] for outcome in outcomes}), 1)


if __name__ == "__main__":
    unittest.main()
