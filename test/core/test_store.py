import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.contracts import BudgetExhausted, ContractError
from devsquad.store import ConflictError, SchemaVersionError, Store, git_common_dir, SUPPORTED_SCHEMA_VERSION


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
        self.assertEqual((run["state"], run["version"]), ("cancelled", 3))
        self.assertIsNotNone(self.store.artifact_named(first.run_id, "result-receipt.json"))

    def test_two_recovery_claimants_yield_one_owner_and_fence_the_old_token(self):
        original = self.store.claim_start(self.repo, "recover", {"task": "fixed"}, "old-owner")
        expected_version = self.store.run(original.run_id)["version"]
        barrier = threading.Barrier(2)
        successes, conflicts = [], []

        def reclaim(owner):
            connection = Store(self.database, self.artifacts)
            try:
                barrier.wait()
                successes.append(connection.reclaim_preparation(original.run_id, expected_version, owner))
            except ConflictError as exc:
                conflicts.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=reclaim, args=(owner,)) for owner in ("recovery-a", "recovery-b")]
        for thread in threads: thread.start()
        for thread in threads: thread.join()

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(successes[0].fencing_token, (original.fencing_token or 0) + 1)
        with self.assertRaises(ConflictError):
            self.store.complete_preparation(original.run_id, original.fencing_token, {"branch": "stale"})
        version = self.store.complete_preparation(
            original.run_id, successes[0].fencing_token, {"branch": "recovered"},
        )
        self.assertEqual(version, successes[0].version + 1)

    def test_queued_and_launching_cancellation_publish_one_receipt(self):
        queued = self.store.claim_start(self.repo, "cancel-queued", {"task": 1}, "owner")
        queued_version = self.store.complete_preparation(
            queued.run_id, queued.fencing_token, {"branch": "frozen"},
        )
        cancelled_version = self.store.cancel_queued(queued.run_id)
        self.assertEqual(cancelled_version, queued_version + 2)
        self.assertIsNotNone(self.store.artifact_named(queued.run_id, "result-receipt.json"))
        self.assertEqual(self.store.cancel_queued(queued.run_id), cancelled_version)

        launching = self.store.claim_start(self.repo, "cancel-launching", {"task": 2}, "owner")
        launching_version = self.store.complete_preparation(
            launching.run_id, launching.fencing_token, {"branch": "frozen"},
        )
        reservation = self.store.reserve_attempt(
            launching.run_id, launching_version, "supervisor", "package-digest",
        )
        final_version = self.store.cancel_launching(launching.run_id)
        self.assertEqual(final_version, reservation.version + 2)
        self.assertIsNotNone(self.store.artifact_named(launching.run_id, "result-receipt.json"))
        with self.assertRaises(ConflictError):
            self.store.mark_attempt_running(reservation, 10, 10, "stale-process")

        for run_id,phase in ((queued.run_id,"queued"),(launching.run_id,"launching")):
            artifact=self.store.artifact_named(run_id,"result-receipt.json")
            content=Path(artifact["path"]).read_bytes()
            receipt=json.loads(content)
            self.assertEqual(hashlib.sha256(content).hexdigest(),artifact["sha256"])
            self.assertEqual(
                (receipt["run_id"],receipt["state"],receipt["phase"],receipt["cancelled"]),
                (run_id,"cancelled",phase,True),
            )

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

    def test_preparation_can_pin_a_same_project_frozen_worktree(self):
        frozen = self.root / "frozen-review"
        subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "add", "--detach", "-q",
             str(frozen), "HEAD"],
            check=True,
        )
        target_oid = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        claim = self.store.claim_start(self.repo, "frozen", {"task": 1}, "owner")
        version = self.store.complete_preparation(
            claim.run_id,
            claim.fencing_token,
            {"target_oid": target_oid},
            package_path="/frozen/package",
            package_digest="package",
            worktree_path=str(frozen),
        )
        self.assertEqual(self.store.run(claim.run_id)["worktree_path"], str(frozen.resolve()))
        reservation = self.store.reserve_attempt(
            claim.run_id, version, "supervisor", "package",
        )
        attempt = self.store.connection.execute(
            "SELECT worktree_path FROM attempts WHERE id=?", (reservation.attempt_id,),
        ).fetchone()
        self.assertEqual(attempt["worktree_path"], str(frozen.resolve()))

        other = self.root / "other"
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        subprocess.run(
            ["git", "-C", str(other), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "config", "user.name", "Test"], check=True,
        )
        (other / "README").write_text("other\n")
        subprocess.run(["git", "-C", str(other), "add", "README"], check=True)
        subprocess.run(["git", "-C", str(other), "commit", "-qm", "other"], check=True)
        rejected = self.store.claim_start(self.repo, "wrong-project", {"task": 2}, "owner")
        with self.assertRaisesRegex(ContractError, "different project"):
            self.store.complete_preparation(
                rejected.run_id,
                rejected.fencing_token,
                {"target_oid": target_oid},
                worktree_path=str(other),
            )
        self.assertEqual(
            (self.store.run(rejected.run_id)["state"], self.store.run(rejected.run_id)["phase"]),
            ("queued", "preparing"),
        )

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

    @staticmethod
    def routed_snapshot(
        wall_seconds=300,
        *,
        max_concurrency=1,
        status="available",
        unknown_capacity_policy="allow_bounded",
    ):
        return {
            "task": {"budget": {"wall_seconds": wall_seconds}},
            "routing": {
                "roles": {
                    "reviewer": {
                        "selected": {
                            "profile": {"account_pool_id": "shared-pool"},
                        },
                    },
                },
                "capacity": {
                    "shared-pool": {
                        "max_concurrency": max_concurrency,
                        "status": status,
                        "unknown_capacity_policy": unknown_capacity_policy,
                    },
                },
            },
        }

    def test_shared_pool_reservation_is_transactional_and_releases_on_finish(self):
        linked = self.root / "pool-linked"
        subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "add", "--detach", "-q",
             str(linked), "HEAD"],
            check=True,
        )
        first = self.store.claim_start(
            self.repo, "pool-first", {"task": {"budget": {"wall_seconds": 300}}},
            "owner",
        )
        first_version = self.store.complete_preparation(
            first.run_id,
            first.fencing_token,
            self.routed_snapshot(),
            worktree_path=str(self.repo),
        )
        second = self.store.claim_start(
            linked, "pool-second", {"task": {"budget": {"wall_seconds": 300}}},
            "owner",
        )
        second_version = self.store.complete_preparation(
            second.run_id,
            second.fencing_token,
            self.routed_snapshot(),
            worktree_path=str(linked),
        )
        reservation = self.store.reserve_attempt(
            first.run_id,
            first_version,
            "supervisor-one",
            "package",
            "reviewer",
            account_pool_id="shared-pool",
        )
        self.assertEqual(self.store.active_pool_counts(), {"shared-pool": 1})
        pool_row = self.store.connection.execute(
            "SELECT attempt_id,reconciled_at FROM pool_reservations WHERE run_id=?",
            (first.run_id,),
        ).fetchone()
        self.assertEqual(pool_row["attempt_id"], reservation.attempt_id)
        self.assertIsNone(pool_row["reconciled_at"])
        with self.assertRaisesRegex(ConflictError, "account pool concurrency"):
            self.store.reserve_attempt(
                second.run_id,
                second_version,
                "supervisor-two",
                "package",
                "reviewer",
                account_pool_id="shared-pool",
            )
        self.store.mark_attempt_running(reservation, 1001, 1001, "fixture-process")
        self.store.finish_attempt(
            first.run_id, reservation.attempt_token, "succeeded", {},
        )
        self.assertEqual(self.store.active_pool_counts(), {})
        pool_row = self.store.connection.execute(
            "SELECT reconciled_at,reconcile_reason FROM pool_reservations "
            "WHERE attempt_id=?",
            (reservation.attempt_id,),
        ).fetchone()
        self.assertIsNotNone(pool_row["reconciled_at"])
        self.assertEqual(pool_row["reconcile_reason"], "attempt_status_finished")
        released = self.store.reserve_attempt(
            second.run_id,
            second_version,
            "supervisor-two",
            "package",
            "reviewer",
            account_pool_id="shared-pool",
        )
        self.assertEqual(released.run_id, second.run_id)

    def test_post_preflight_exhaustion_is_rederived_before_reservation(self):
        claim = self.store.claim_start(
            self.repo, "capacity-changed", {"task": {"budget": {"wall_seconds": 300}}},
            "owner",
        )
        version = self.store.complete_preparation(
            claim.run_id, claim.fencing_token, self.routed_snapshot(status="available"),
        )
        now = datetime.now(timezone.utc)
        self.store.record_pool_observation({
            "schema_version": 1,
            "observation_id": "weekly-exhausted-after-preflight",
            "pool_id": "shared-pool",
            "window_id": "weekly",
            "applies_to": {
                "harnesses": [], "model_families": [], "model_ids": [],
            },
            "observed_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
            "source": "native_reported",
            "used": 100,
            "limit": 100,
            "unit": "percent",
            "resets_at": (now + timedelta(days=1)).isoformat(),
            "confidence": "confirmed",
        }, now=now)
        with self.assertRaisesRegex(ConflictError, "capacity is exhausted"):
            self.store.reserve_attempt(
                claim.run_id, version, "supervisor", "package", "reviewer",
                account_pool_id="shared-pool",
            )
        self.assertEqual(self.store.active_pool_counts(), {})

    def test_ambiguous_attempt_keeps_pool_until_ownership_is_reconciled(self):
        claim = self.store.claim_start(
            self.repo, "ambiguous-capacity", {"task": {"budget": {"wall_seconds": 300}}},
            "owner",
        )
        version = self.store.complete_preparation(
            claim.run_id, claim.fencing_token, self.routed_snapshot(),
        )
        reservation = self.store.reserve_attempt(
            claim.run_id, version, "supervisor", "package", "reviewer",
            account_pool_id="shared-pool",
        )
        self.store.mark_attempt_running(reservation, 1001, 1001, "fixture-process")
        self.store.block_recovery(
            claim.run_id, reservation.attempt_token, "identity ambiguous",
        )
        self.assertEqual(self.store.active_pool_counts(), {"shared-pool": 1})
        self.store.request_recovery_cancel(claim.run_id, reservation.attempt_token)
        self.assertEqual(self.store.active_pool_counts(), {"shared-pool": 1})
        self.store.finish_recovery_cancel(
            claim.run_id, reservation.attempt_token, "absence confirmed",
        )
        self.assertEqual(self.store.active_pool_counts(), {})

    def test_unknown_pool_allows_only_one_transactional_trial(self):
        linked = self.root / "unknown-pool-linked"
        subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "add", "--detach", "-q",
             str(linked), "HEAD"],
            check=True,
        )
        snapshot = self.routed_snapshot(max_concurrency=3, status="unknown")
        claims = [
            self.store.claim_start(
                repository,
                key,
                {"task": {"budget": {"wall_seconds": 300}}},
                "owner",
            )
            for repository, key in (
                (self.repo, "unknown-pool-first"),
                (linked, "unknown-pool-second"),
            )
        ]
        versions = [
            self.store.complete_preparation(
                claim.run_id,
                claim.fencing_token,
                snapshot,
                worktree_path=str(repository),
            )
            for claim, repository in zip(claims, (self.repo, linked))
        ]
        self.store.reserve_attempt(
            claims[0].run_id,
            versions[0],
            "unknown-supervisor-one",
            "package",
            "reviewer",
            account_pool_id="shared-pool",
        )
        with self.assertRaisesRegex(ConflictError, "account pool concurrency"):
            self.store.reserve_attempt(
                claims[1].run_id,
                versions[1],
                "unknown-supervisor-two",
                "package",
                "reviewer",
                account_pool_id="shared-pool",
            )
        self.assertEqual(self.store.active_pool_counts(), {"shared-pool": 1})

    def test_wall_budget_counts_preflight_and_prior_attempts_cumulatively(self):
        claim = self.store.claim_start(
            self.repo,
            "wall-budget",
            {"task": {"budget": {"wall_seconds": 5}}},
            "owner",
        )
        version = self.store.complete_preparation(
            claim.run_id, claim.fencing_token, self.routed_snapshot(5),
        )
        started = datetime(2026, 9, 18, 1, 0, tzinfo=timezone.utc)
        self.store.connection.execute(
            "UPDATE runs SET created_at=?,updated_at=? WHERE id=?",
            (started.isoformat(), (started + timedelta(seconds=5)).isoformat(),
             claim.run_id),
        )
        self.store.connection.execute(
            "UPDATE events SET created_at=? WHERE run_id=? AND type='run.preparing'",
            (started.isoformat(), claim.run_id),
        )
        self.store.connection.execute(
            "UPDATE events SET created_at=? WHERE run_id=? AND type='run.queued'",
            ((started + timedelta(milliseconds=200)).isoformat(), claim.run_id),
        )
        run = self.store.run(claim.run_id)
        self.store.connection.execute(
            "INSERT INTO attempts(id,run_id,project_id,worktree_path,attempt_token,"
            "status,heartbeat_at,package_digest,created_at,finished_at,role,account_pool_id) "
            "VALUES('prior-attempt',?,?,?,?, 'finished',?,?,?,?, 'reviewer','shared-pool')",
            (claim.run_id, run["project_id"], run["worktree_path"], "prior-token",
             (started + timedelta(seconds=5)).isoformat(), "package",
             (started + timedelta(seconds=1)).isoformat(),
             (started + timedelta(seconds=5, milliseconds=100)).isoformat()),
        )
        current = started + timedelta(seconds=6)
        self.assertEqual(
            self.store.remaining_wall_seconds(claim.run_id, now=current), 0,
        )
        with self.assertRaises(BudgetExhausted):
            self.store.reserve_attempt(
                claim.run_id, version, "supervisor", "package", "reviewer",
                account_pool_id="shared-pool",
            )

    def test_migration_records_version_and_refuses_newer_database(self):
        self.assertEqual(self.store.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], SUPPORTED_SCHEMA_VERSION)
        self.store.connection.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(?,'future')", (SUPPORTED_SCHEMA_VERSION + 1,))
        self.store.close()
        with self.assertRaises(SchemaVersionError):
            Store(self.database, self.artifacts)
        self.store = sqlite3.connect(":memory:")  # tearDown-compatible close

    def test_version_one_fixture_migrates_to_current(self):
        old_db = self.root / "old.sqlite3"
        connection = sqlite3.connect(old_db)
        sql = (ROOT / "plugin/core/src/devsquad/migrations/001_initial.sql").read_text()
        connection.executescript(sql)
        connection.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(1,'fixture')")
        connection.commit(); connection.close()
        upgraded = Store(old_db, self.root / "old-artifacts")
        self.addCleanup(upgraded.close)
        self.assertEqual(upgraded.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], SUPPORTED_SCHEMA_VERSION)
        self.assertTrue(upgraded.connection.execute("SELECT 1 FROM sqlite_master WHERE name='attempts'").fetchone())
        attempt_columns = {
            row[1] for row in upgraded.connection.execute("PRAGMA table_info(attempts)")
        }
        self.assertIn("role", attempt_columns)
        self.assertIn("account_pool_id", attempt_columns)
        self.assertIn("profile_id", attempt_columns)
        self.assertIn("profile_index", attempt_columns)

    def test_version_three_fixture_adds_run_snapshot_columns(self):
        old_db=self.root/"v3.sqlite3"; connection=sqlite3.connect(old_db)
        for version,name in ((1,"001_initial.sql"),(2,"002_supervisor.sql"),(3,"003_durable_io.sql")):
            connection.executescript((ROOT/"plugin/core/src/devsquad/migrations"/name).read_text())
            connection.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)",(version,"fixture"))
        connection.commit(); connection.close()
        upgraded=Store(old_db,self.root/"v3-artifacts"); self.addCleanup(upgraded.close)
        self.assertEqual(upgraded.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], SUPPORTED_SCHEMA_VERSION)
        columns={row[1] for row in upgraded.connection.execute("PRAGMA table_info(runs)")}
        self.assertTrue({"package_path","package_digest","supersedes_run_id"} <= columns)


if __name__ == "__main__":
    unittest.main()
