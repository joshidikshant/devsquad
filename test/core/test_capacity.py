import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.capacity import derive_pool_capacity, validate_observation
from devsquad.contracts import ContractError
from devsquad.store import ConflictError, Store


NOW = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)


def observation(
    observation_id="obs-short",
    *,
    window_id="short",
    observed_at=NOW - timedelta(minutes=1),
    expires_at=NOW + timedelta(minutes=10),
    source="native_reported",
    used=20,
    limit=100,
    unit="percent",
    confidence="confirmed",
    applies_to=None,
):
    return {
        "schema_version": 1,
        "observation_id": observation_id,
        "pool_id": "shared-pool",
        "window_id": window_id,
        "applies_to": applies_to or {
            "harnesses": [],
            "model_families": [],
            "model_ids": [],
        },
        "observed_at": observed_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "source": source,
        "used": used,
        "limit": limit,
        "unit": unit,
        "resets_at": (NOW + timedelta(hours=1)).isoformat(),
        "confidence": confidence,
    }


class CapacityContractTest(unittest.TestCase):
    @staticmethod
    def make_repository(path):
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        subprocess.run(
            ["git", "-C", str(path), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(path), "config", "user.name", "Test"],
            check=True,
        )
        (path / "README").write_text("fixture\n")
        subprocess.run(["git", "-C", str(path), "add", "README"], check=True)
        subprocess.run(["git", "-C", str(path), "commit", "-qm", "base"], check=True)

    def test_validation_is_strict_and_normalizes_scopes(self):
        value = observation(applies_to={
            "harnesses": ["grok", "codex"],
            "model_families": [],
            "model_ids": ["model-b", "model-a"],
        })
        normalized = validate_observation(value, now=NOW)
        self.assertEqual(normalized["applies_to"]["harnesses"], ["codex", "grok"])
        self.assertEqual(normalized["applies_to"]["model_ids"], ["model-a", "model-b"])
        value["applies_to"]["harnesses"].append("claude")
        self.assertEqual(normalized["applies_to"]["harnesses"], ["codex", "grok"])

        invalid = copy.deepcopy(normalized)
        invalid["extra"] = True
        with self.assertRaisesRegex(ContractError, "fields invalid"):
            validate_observation(invalid, now=NOW)
        invalid = observation(used=True)
        with self.assertRaisesRegex(ContractError, "finite non-negative"):
            validate_observation(invalid, now=NOW)
        invalid = observation(used=math.inf)
        with self.assertRaisesRegex(ContractError, "finite non-negative"):
            validate_observation(invalid, now=NOW)
        invalid = observation(used=101)
        with self.assertRaisesRegex(ContractError, "at most 100"):
            validate_observation(invalid, now=NOW)
        invalid = observation()
        invalid["source"] = []
        with self.assertRaisesRegex(ContractError, "source"):
            validate_observation(invalid, now=NOW)
        invalid = observation(observed_at=NOW + timedelta(minutes=6))
        invalid["expires_at"] = (NOW + timedelta(minutes=20)).isoformat()
        with self.assertRaisesRegex(ContractError, "clock skew"):
            validate_observation(invalid, now=NOW)
        invalid = observation()
        invalid["observed_at"] = "2026-09-27T15:00:00"
        with self.assertRaisesRegex(ContractError, "timezone"):
            validate_observation(invalid, now=NOW)

    def test_fresh_exhausted_weekly_window_beats_available_short_window(self):
        short = observation()
        weekly = observation(
            "obs-weekly", window_id="weekly", used=100, limit=100,
        )
        snapshot = derive_pool_capacity(
            "shared-pool", [short, weekly], in_flight=1, now=NOW,
        )
        self.assertEqual(snapshot["status"], "exhausted")
        self.assertEqual(snapshot["in_flight"], 1)
        self.assertEqual(
            [window["status"] for window in snapshot["windows"]],
            ["available", "exhausted"],
        )
        self.assertEqual(snapshot["reasons"], ["window_exhausted:weekly"])

    def test_stale_estimated_or_unknown_window_never_becomes_zero(self):
        stale = observation(
            "obs-weekly", window_id="weekly",
            expires_at=NOW - timedelta(seconds=1), used=100, limit=100,
        )
        snapshot = derive_pool_capacity(
            "shared-pool", [observation(), stale], now=NOW,
        )
        self.assertEqual(snapshot["status"], "unknown")
        self.assertIn("stale_observation:weekly", snapshot["reasons"])

        estimated = observation(
            "obs-estimated", source="estimated", confidence="estimated",
            used=100, limit=100,
        )
        snapshot = derive_pool_capacity("shared-pool", [estimated], now=NOW)
        self.assertEqual(snapshot["status"], "unknown")
        self.assertFalse(snapshot["windows"][0]["authoritative"])

        unknown = observation("obs-unknown", used=None, limit=None)
        snapshot = derive_pool_capacity("shared-pool", [unknown], now=NOW)
        self.assertEqual(snapshot["status"], "unknown")
        self.assertEqual(snapshot["windows"][0]["reason"], "unknown_measurement")

    def test_latest_observation_per_scoped_window_is_used(self):
        scope = {
            "harnesses": ["codex"],
            "model_families": ["gpt"],
            "model_ids": [],
        }
        older = observation(
            "older", used=100, limit=100, applies_to=scope,
            observed_at=NOW - timedelta(minutes=2),
        )
        newer = observation(
            "newer", used=10, limit=100, applies_to=scope,
            observed_at=NOW - timedelta(minutes=1),
        )
        target = {"harness": "codex", "model_family": "gpt", "model_id": "gpt-5"}
        snapshot = derive_pool_capacity(
            "shared-pool", [older, newer], target=target, now=NOW,
        )
        self.assertEqual(snapshot["status"], "available")
        self.assertEqual([item["observation_id"] for item in snapshot["windows"]], ["newer"])

        other = {"harness": "claude", "model_family": "claude", "model_id": "opus"}
        snapshot = derive_pool_capacity(
            "shared-pool", [older, newer], target=other, now=NOW,
        )
        self.assertEqual(snapshot["status"], "unknown")
        self.assertEqual(snapshot["reasons"], ["no_applicable_observations"])

    def test_migration_nine_creates_capacity_ledger(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            store = Store(path / "state.sqlite3", path / "artifacts")
            self.addCleanup(store.close)
            version = store.connection.execute(
                "SELECT MAX(version) FROM schema_migrations",
            ).fetchone()[0]
            self.assertEqual(version, 14)
            tables = {
                row[0] for row in store.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
            self.assertTrue({"pool_observations", "pool_reservations"} <= tables)

    def test_store_observation_is_replay_safe_and_stale_evidence_stays_visible(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            store = Store(path / "state.sqlite3", path / "artifacts")
            self.addCleanup(store.close)
            stale = observation(
                "persisted-stale",
                expires_at=NOW - timedelta(seconds=1),
                used=100,
                limit=100,
            )
            first = store.record_pool_observation(stale, now=NOW)
            replay = store.record_pool_observation(stale, now=NOW)
            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            snapshot = store.capacity_snapshot("shared-pool", now=NOW)
            self.assertEqual(snapshot["status"], "unknown")
            self.assertEqual(snapshot["windows"][0]["reason"], "stale_observation")

            changed = copy.deepcopy(stale)
            changed["used"] = 99
            with self.assertRaisesRegex(ConflictError, "different evidence"):
                store.record_pool_observation(changed, now=NOW)

    def test_non_attempt_reservation_is_bounded_and_explicitly_reconciled(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            repository = path / "repo"
            self.make_repository(repository)
            store = Store(path / "state.sqlite3", path / "artifacts")
            self.addCleanup(store.close)
            claim = store.claim_start(repository, "classifier-capacity", {}, "owner")
            reserved = store.reserve_pool_capacity(
                claim.run_id, "shared-pool", "classifier", now=NOW,
            )
            self.assertEqual(store.active_pool_counts(), {"shared-pool": 1})
            with self.assertRaisesRegex(ConflictError, "concurrency"):
                store.reserve_pool_capacity(
                    claim.run_id, "shared-pool", "classifier", now=NOW,
                )
            reconciled = store.reconcile_pool_reservation(
                reserved["reservation_id"], "classifier_finished", now=NOW,
            )
            self.assertFalse(reconciled["replayed"])
            self.assertEqual(store.active_pool_counts(), {})
            replay = store.reconcile_pool_reservation(
                reserved["reservation_id"], "classifier_finished", now=NOW,
            )
            self.assertTrue(replay["replayed"])

    def test_two_projects_racing_for_unknown_pool_create_one_reservation(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            repositories = [path / "repo-a", path / "repo-b"]
            for repository in repositories:
                self.make_repository(repository)
            database, artifacts = path / "state.sqlite3", path / "artifacts"
            store = Store(database, artifacts)
            run_ids = [
                store.claim_start(repository, f"run-{index}", {}, "owner").run_id
                for index, repository in enumerate(repositories)
            ]
            store.close()
            barrier = threading.Barrier(2)

            def reserve(run_id):
                connection = Store(database, artifacts)
                try:
                    barrier.wait()
                    return connection.reserve_pool_capacity(
                        run_id, "shared-pool", "qualification", now=NOW,
                    )["reservation_id"]
                except ConflictError:
                    return "conflict"
                finally:
                    connection.close()

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(reserve, run_ids))
            self.assertEqual(results.count("conflict"), 1)
            winner = next(result for result in results if result != "conflict")
            store = Store(database, artifacts)
            self.addCleanup(store.close)
            self.assertEqual(store.active_pool_counts(), {"shared-pool": 1})
            store.reconcile_pool_reservation(winner, "qualification_finished", now=NOW)

    def test_schema_eight_active_attempt_is_backfilled_and_reconciled(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            database = path / "state.sqlite3"
            import sqlite3

            connection = sqlite3.connect(database)
            migrations = ROOT / "plugin/core/src/devsquad/migrations"
            for version in range(1, 9):
                name = next(migrations.glob(f"{version:03d}_*.sql"))
                connection.executescript(name.read_text())
                connection.execute(
                    "INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)",
                    (version, NOW.isoformat()),
                )
            connection.execute(
                "INSERT INTO projects(id,git_common_dir,created_at) VALUES('p','/tmp/p',?)",
                (NOW.isoformat(),),
            )
            connection.execute(
                "INSERT INTO runs(id,project_id,idempotency_key,request_hash,"
                "submitted_request,state,version,created_at,updated_at,worktree_path) "
                "VALUES('r','p','key','hash','{}','running',1,?,?, '/tmp/w')",
                (NOW.isoformat(), NOW.isoformat()),
            )
            connection.execute(
                "INSERT INTO attempts(id,run_id,project_id,worktree_path,attempt_token,"
                "status,heartbeat_at,package_digest,created_at,role,account_pool_id,profile_id) "
                "VALUES('a','r','p','/tmp/w','token','running',?,'package',?,"
                "'reviewer','shared-pool','profile-a')",
                (NOW.isoformat(), NOW.isoformat()),
            )
            connection.commit()
            connection.close()

            store = Store(database, path / "artifacts")
            self.addCleanup(store.close)
            self.assertEqual(store.active_pool_counts(), {"shared-pool": 1})
            row = store.connection.execute(
                "SELECT purpose,profile_id FROM pool_reservations WHERE attempt_id='a'",
            ).fetchone()
            self.assertEqual(tuple(row), ("attempt", "profile-a"))
            store.connection.execute(
                "UPDATE attempts SET status='finished',finished_at=? WHERE id='a'",
                (NOW.isoformat(),),
            )
            self.assertEqual(store.active_pool_counts(), {})


if __name__ == "__main__":
    unittest.main()
