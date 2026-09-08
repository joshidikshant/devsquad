import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.store import ConflictError, SchemaVersionError, Store, git_common_dir


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Test"], check=True)
        (self.repo / "README").write_text("base\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "README"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "base"], check=True)
        self.database = self.root / "runtime/ledger.sqlite3"
        self.artifacts = self.root / "runtime/artifacts"
        self.store = Store(self.database, self.artifacts)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_concurrent_identical_start_claims_one_run_and_conflicting_body_fails(self):
        barrier = threading.Barrier(2)
        results, errors = [], []
        def start(body):
            connection = Store(self.database, self.artifacts)
            try:
                barrier.wait()
                results.append(connection.claim_start(self.repo, "same-key", body, "owner"))
            except Exception as exc:
                errors.append(exc)
            finally:
                connection.close()
        threads = [threading.Thread(target=start, args=({"task": "same", "n": 1},)) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len({result.run_id for result in results}), 1)
        self.assertEqual(sorted(result.created for result in results), [False, True])
        with self.assertRaises(ConflictError):
            self.store.claim_start(self.repo, "same-key", {"task": "different"}, "owner-2")

    def test_request_is_claimed_before_snapshot_and_cancel_fences_late_preflight(self):
        first = self.store.claim_start(self.repo, "k", {"task": "fixed"}, "owner")
        run = self.store.run(first.run_id)
        self.assertEqual((run["state"], run["phase"]), ("queued", "preparing"))
        self.store.cancel_preparing(first.run_id)
        with self.assertRaises(ConflictError):
            self.store.complete_preparation(first.run_id, first.fencing_token, {"branch": "moved"})
        run = self.store.run(first.run_id)
        self.assertEqual((run["state"], run["version"]), ("cancelled", 2))

    def test_event_and_projection_compare_and_swap_share_transaction(self):
        claim = self.store.claim_start(self.repo, "events", {"task": "x"}, "owner")
        with self.assertRaises(ConflictError):
            self.store.append_event(claim.run_id, 1, "unfenced", {})
        version = self.store.complete_preparation(claim.run_id, claim.fencing_token, {"head": "abc"})
        barrier = threading.Barrier(2)
        successes, conflicts = [], []
        def mutate(label):
            connection = Store(self.database, self.artifacts)
            try:
                barrier.wait()
                successes.append(connection.append_event(claim.run_id, version, f"run.{label}", {"label": label}))
            except ConflictError as exc:
                conflicts.append(exc)
            finally:
                connection.close()
        threads = [threading.Thread(target=mutate, args=(label,)) for label in ("a", "b")]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(successes, [3])
        self.assertEqual(len(conflicts), 1)
        events = self.store.connection.execute("SELECT run_version FROM events WHERE run_id=? ORDER BY id", (claim.run_id,)).fetchall()
        self.assertEqual([row[0] for row in events], [1, 2, 3])

    def test_git_common_dir_unifies_linked_worktrees(self):
        linked = self.root / "linked"
        subprocess.run(["git", "-C", str(self.repo), "worktree", "add", "-q", "-b", "linked", str(linked)], check=True)
        self.assertEqual(git_common_dir(self.repo), git_common_dir(linked))
        a = self.store.claim_start(self.repo, "root", {"task": 1}, "a")
        b = self.store.claim_start(linked, "linked", {"task": 2}, "b")
        self.assertEqual(a.project_id, b.project_id)

    def test_artifact_is_finalized_and_verified_before_reference(self):
        claim = self.store.claim_start(self.repo, "artifact", {"task": 1}, "owner")
        path, digest, size = self.store.finalize_artifact(claim.run_id, "result.json", b'{"ok":true}')
        self.assertEqual(size, path.stat().st_size)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 0)
        path.write_bytes(b"tampered")
        with self.assertRaises(ConflictError):
            self.store.reference_artifact(claim.run_id, "result.json", path, digest)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 0)
        artifact_id = self.store.store_artifact(claim.run_id, "final.json", b'{"done":true}')
        row = self.store.connection.execute("SELECT sha256,byte_size FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        self.assertEqual(row[1], 13)

    def test_migration_records_version_and_refuses_newer_database(self):
        self.assertEqual(self.store.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 2)
        self.store.connection.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(3,'future')")
        self.store.close()
        with self.assertRaises(SchemaVersionError):
            Store(self.database, self.artifacts)
        self.store = sqlite3.connect(":memory:")  # tearDown-compatible close

    def test_version_one_fixture_migrates_to_version_two(self):
        old_db = self.root / "old.sqlite3"
        connection = sqlite3.connect(old_db)
        sql = (ROOT / "plugin/core/src/devsquad/migrations/001_initial.sql").read_text()
        connection.executescript(sql)
        connection.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(1,'fixture')")
        connection.commit(); connection.close()
        upgraded = Store(old_db, self.root / "old-artifacts")
        self.addCleanup(upgraded.close)
        self.assertEqual(upgraded.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 2)
        self.assertTrue(upgraded.connection.execute("SELECT 1 FROM sqlite_master WHERE name='attempts'").fetchone())


if __name__ == "__main__":
    unittest.main()
