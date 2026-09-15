"""Independent M2 checks for persisted ownership and artifact integrity."""
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

from devsquad.contracts import ContractError
from devsquad.store import ConflictError, Store


def concurrent_open(database, artifacts, barrier, results):
    store = None
    try:
        barrier.wait(timeout=10)
        store = Store(Path(database), Path(artifacts))
        results.put(None)
    except Exception as exc:
        results.put(f"{type(exc).__name__}: {exc}")
    finally:
        if store is not None:
            store.close()


class StoreIntegrityReview(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="devsquad-store-review-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.store = Store(self.root / "state.sqlite3", self.root / "artifacts")
        self.addCleanup(self.store.close)
        self.task = json.loads((ROOT / "docs/plans/engineering-team/examples/branch-review.json").read_text())
        self.task["project"]["repo_path"] = str(self.repo)
        self.claim = self.store.claim_start(self.repo, "review-key", self.task, "first-owner")

    def test_duplicate_artifact_never_changes_existing_receipt_content(self):
        artifact_id = self.store.store_artifact(self.claim.run_id, "receipt.json", b"original")
        row = self.store.connection.execute("SELECT path,sha256 FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        path, original_digest = Path(row["path"]), row["sha256"]
        try:
            self.store.store_artifact(self.claim.run_id, "receipt.json", b"replacement")
        except ContractError:
            pass
        self.assertEqual(path.read_bytes(), b"original")
        row = self.store.connection.execute("SELECT sha256 FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        self.assertEqual(row["sha256"], original_digest)

    def test_artifact_finalization_cannot_escape_with_run_id(self):
        outside = self.root / "outside"
        for run_id in (str(outside), "../outside"):
            with self.subTest(run_id=run_id), self.assertRaises(ContractError):
                self.store.finalize_artifact(run_id, "receipt.json", b"unowned")
        self.assertFalse(outside.exists())

    def test_queued_preparation_cannot_be_made_runnable_by_generic_event(self):
        run = self.store.run(self.claim.run_id)
        self.assertEqual(run["state"], "queued")
        self.assertEqual(run["phase"], "preparing")
        with self.assertRaises(ConflictError):
            self.store.append_event(self.claim.run_id, run["version"], "run.started", {}, state="running")
        self.assertEqual(self.store.run(self.claim.run_id)["phase"], "preparing")

    def test_cancelled_preparation_cannot_publish_late_snapshot(self):
        version = self.store.cancel_preparing(self.claim.run_id)
        with self.assertRaises(ConflictError):
            self.store.complete_preparation(self.claim.run_id, self.claim.fencing_token, {"base": "late"})
        self.assertEqual(self.store.run(self.claim.run_id)["state"], "cancelled")
        self.assertEqual(self.store.cancel_preparing(self.claim.run_id), version)

    def test_artifact_reference_is_versioned_and_terminal_run_is_immutable(self):
        before = self.store.run(self.claim.run_id)["version"]
        self.store.store_artifact(self.claim.run_id, "input.json", b"frozen input")
        after = self.store.run(self.claim.run_id)["version"]
        self.assertEqual(after, before + 1)
        event = self.store.connection.execute(
            "SELECT run_version FROM events WHERE run_id=? ORDER BY id DESC LIMIT 1",
            (self.claim.run_id,),
        ).fetchone()
        self.assertEqual(event["run_version"], after)
        terminal_version = self.store.cancel_preparing(self.claim.run_id)
        with self.assertRaises(ConflictError):
            self.store.store_artifact(self.claim.run_id, "late.json", b"late write")
        self.assertEqual(self.store.run(self.claim.run_id)["version"], terminal_version)
        count = self.store.connection.execute(
            "SELECT COUNT(*) FROM artifacts WHERE run_id=?", (self.claim.run_id,),
        ).fetchone()[0]
        self.assertEqual(count, 2)
        self.assertIsNotNone(self.store.artifact_named(self.claim.run_id, "result-receipt.json"))


class StoreInitializationReview(unittest.TestCase):
    def test_independent_processes_can_open_one_new_database(self):
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory(prefix="devsquad-store-race-") as directory:
            root = Path(directory)
            barrier, results = context.Barrier(4), context.Queue()
            processes = [context.Process(target=concurrent_open, args=(str(root / "db"), str(root / "artifacts"), barrier, results)) for _ in range(4)]
            try:
                for process in processes:
                    process.start()
                outcomes = [results.get(timeout=15) for _ in processes]
                for process in processes:
                    process.join(timeout=2)
                self.assertEqual(outcomes, [None] * 4)
                self.assertTrue(all(process.exitcode == 0 for process in processes))
            finally:
                for process in processes:
                    if process.is_alive():
                        process.terminate()
                    process.join(timeout=2)
                results.close()
                results.join_thread()


if __name__ == "__main__":
    unittest.main()
