from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.store import (
    ConflictError,
    HandoffClaim,
    Store,
    request_hash,
)


class HandoffStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devsquad-handoff-")
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.name", "Test"], check=True,
        )
        (self.repo / "README").write_text("base\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "README"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "base"], check=True)
        self.database = self.root / "runtime/state.sqlite3"
        self.artifacts = self.root / "runtime/artifacts"
        self.store = Store(self.database, self.artifacts)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def running_run(self, key="handoff"):
        claim = self.store.claim_start(self.repo, key, {"task": key}, "preflight")
        version = self.store.complete_preparation(
            claim.run_id,
            claim.fencing_token,
            {"base_oid": "a" * 40, "target_oid": "b" * 40},
            package_path="/frozen/package",
            package_digest="package-digest",
        )
        reservation = self.store.reserve_attempt(
            claim.run_id, version, "supervisor", "package-digest",
        )
        version = self.store.mark_attempt_running(
            reservation, 101, 101, "process-start-id",
        )
        return claim.run_id, reservation, version

    def waiting_run(self, key="handoff"):
        run_id, reservation, version = self.running_run(key)
        packet = {
            "schema_version": 1,
            "candidate_sha256": "c" * 64,
            "evidence_refs": [],
        }
        snapshot = self.store.publish_handoff(
            run_id,
            version,
            reservation.attempt_token,
            reservation.supervisor_token,
            packet,
            now=datetime(2026, 9, 15, 5, 0, tzinfo=timezone.utc),
        )
        return run_id, reservation, snapshot

    @staticmethod
    def decision(submission_id="submission-1", disposition="accept", reason="accepted"):
        body = {
            "schema_version": 1,
            "submission_id": submission_id,
            "disposition": disposition,
            "reason": reason,
            "evidence_refs": [],
        }
        return {**body, "submission_hash": request_hash(body)}

    def test_schema_four_fixture_migrates_to_host_handoffs(self):
        old_database = self.root / "schema-four.sqlite3"
        connection = sqlite3.connect(old_database)
        migration_dir = ROOT / "plugin/core/src/devsquad/migrations"
        for version, name in (
            (1, "001_initial.sql"),
            (2, "002_supervisor.sql"),
            (3, "003_durable_io.sql"),
            (4, "004_run_snapshot.sql"),
        ):
            connection.executescript((migration_dir / name).read_text())
            connection.execute(
                "INSERT INTO schema_migrations(version,applied_at) VALUES(?, 'fixture')",
                (version,),
            )
        connection.commit()
        connection.close()

        upgraded = Store(old_database, self.root / "schema-four-artifacts")
        self.addCleanup(upgraded.close)
        self.assertEqual(
            upgraded.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
            5,
        )
        tables = {
            row[0]
            for row in upgraded.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertTrue({"handoffs", "handoff_submissions"} <= tables)
        claim_columns = {
            row[1] for row in upgraded.connection.execute("PRAGMA table_info(claims)")
        }
        self.assertTrue({"handoff_id", "lease_expires_at", "renewed_at"} <= claim_columns)
        with self.assertRaises(sqlite3.IntegrityError):
            upgraded.connection.execute(
                "INSERT INTO handoffs(id,run_id,sequence,packet_json,packet_sha256,status,"
                "created_run_version,created_at) VALUES('bad','missing',1,'{}',?,'invalid',1,'now')",
                ("0" * 64,),
            )

    def test_publish_is_fenced_and_atomically_releases_writer(self):
        run_id, reservation, version = self.running_run()
        packet = {"schema_version": 1, "candidate_sha256": "c" * 64}
        snapshot = self.store.publish_handoff(
            run_id,
            version,
            reservation.attempt_token,
            reservation.supervisor_token,
            packet,
        )
        self.assertEqual(snapshot.packet, packet)
        self.assertEqual(snapshot.run_state, "awaiting_host")
        self.assertEqual(snapshot.run_version, version + 1)
        self.assertEqual(snapshot.created_run_version, version + 1)
        self.assertEqual(
            self.store.connection.execute(
                "SELECT status FROM attempts WHERE attempt_token=?",
                (reservation.attempt_token,),
            ).fetchone()[0],
            "finished",
        )
        self.assertEqual(
            self.store.connection.execute(
                "SELECT active FROM supervisor_claims WHERE run_id=?", (run_id,),
            ).fetchone()[0],
            0,
        )
        event = self.store.connection.execute(
            "SELECT run_version,type,payload FROM events WHERE run_id=? ORDER BY id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        self.assertEqual((event[0], event[1]), (version + 1, "run.awaiting_host"))
        self.assertEqual(json.loads(event[2])["packet_sha256"], snapshot.packet_sha256)
        with self.assertRaises(ConflictError):
            self.store.publish_handoff(
                run_id,
                version,
                reservation.attempt_token,
                reservation.supervisor_token,
                packet,
            )
        self.store.connection.execute(
            "UPDATE handoffs SET packet_json='{}' WHERE id=?", (snapshot.handoff_id,),
        )
        with self.assertRaises(ConflictError):
            self.store.handoff_snapshot(run_id)

    def test_claim_cas_renewal_and_expired_takeover(self):
        run_id, _, snapshot = self.waiting_run()
        first_time = datetime(2026, 9, 15, 5, 1, tzinfo=timezone.utc)
        first = self.store.claim_handoff(
            run_id, snapshot.run_version, "host-a", now=first_time,
        )
        self.assertEqual(first.action, "acquired")
        renewed = self.store.claim_handoff(
            run_id,
            first.run_version,
            "host-a",
            first,
            now=first_time + timedelta(minutes=5),
        )
        self.assertEqual(renewed.action, "renewed")
        self.assertEqual(renewed.fencing_token, first.fencing_token)
        self.assertGreater(renewed.expires_at, first.expires_at)
        with self.assertRaises(ConflictError):
            self.store.claim_handoff(
                run_id,
                renewed.run_version,
                "host-a",
                first,
                now=first_time + timedelta(minutes=6),
            )
        with self.assertRaises(ConflictError):
            self.store.claim_handoff(
                run_id,
                renewed.run_version,
                "host-b",
                now=first_time + timedelta(minutes=6),
            )
        with self.assertRaises(ConflictError):
            self.store.claim_handoff(
                run_id,
                renewed.run_version,
                "host-a",
                renewed,
                now=first_time + timedelta(minutes=15),
            )
        takeover = self.store.claim_handoff(
            run_id,
            renewed.run_version,
            "host-b",
            now=first_time + timedelta(minutes=15),
        )
        self.assertEqual(takeover.action, "taken_over")
        self.assertEqual(takeover.fencing_token, first.fencing_token + 1)

    def test_two_claimants_at_one_version_yield_one_owner(self):
        run_id, _, snapshot = self.waiting_run()
        barrier = threading.Barrier(2)
        claims = []
        conflicts = []

        def claim(owner):
            store = Store(self.database, self.artifacts)
            try:
                barrier.wait()
                claims.append(store.claim_handoff(run_id, snapshot.run_version, owner))
            except ConflictError as exc:
                conflicts.append(exc)
            finally:
                store.close()

        threads = [threading.Thread(target=claim, args=(owner,)) for owner in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(claims), 1)
        self.assertEqual(len(conflicts), 1)

    def test_expired_submission_is_audited_and_cannot_displace_takeover(self):
        run_id, _, snapshot = self.waiting_run()
        started = datetime(2026, 9, 15, 5, 1, tzinfo=timezone.utc)
        old_claim = self.store.claim_handoff(
            run_id, snapshot.run_version, "old-host", now=started,
        )
        takeover = self.store.claim_handoff(
            run_id,
            old_claim.run_version,
            "new-host",
            now=started + timedelta(minutes=11),
        )
        before = self.store.run(run_id)["version"]
        with self.assertRaises(ConflictError):
            self.store.record_handoff_submission(
                run_id,
                old_claim,
                self.decision(),
                now=started + timedelta(minutes=11),
            )
        self.assertEqual(self.store.run(run_id)["version"], before + 1)
        rejection = self.store.connection.execute(
            "SELECT outcome,rejection_code FROM handoff_submissions WHERE handoff_id=?",
            (snapshot.handoff_id,),
        ).fetchone()
        self.assertEqual(tuple(rejection), ("rejected", "stale_claim"))
        current_claim = self.store.handoff_snapshot(run_id).claim
        self.assertEqual(current_claim.owner_id, takeover.owner_id)
        self.assertEqual(current_claim.fencing_token, takeover.fencing_token)

    def test_submission_replays_and_terminal_late_rejection_preserves_run_and_events(self):
        run_id, _, snapshot = self.waiting_run()
        claimed_at = datetime(2026, 9, 15, 5, 1, tzinfo=timezone.utc)
        claim = self.store.claim_handoff(
            run_id, snapshot.run_version, "host", now=claimed_at,
        )
        decision = self.decision()
        recorded = self.store.record_handoff_submission(
            run_id, claim, decision, now=claimed_at + timedelta(minutes=1),
        )
        self.assertFalse(recorded.replayed)
        waiting = self.store.run(run_id)
        self.assertEqual((waiting["state"], waiting["phase"]), ("awaiting_host", "handoff_submitted"))
        replay = self.store.record_handoff_submission(
            run_id, claim, decision, now=claimed_at + timedelta(minutes=20),
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.recorded_run_version, recorded.recorded_run_version)
        terminal_version = self.store.cancel_host_wait(
            run_id, now=claimed_at + timedelta(minutes=21),
        )
        replay_after_cancel = self.store.record_handoff_submission(
            run_id, claim, decision, now=claimed_at + timedelta(minutes=22),
        )
        self.assertTrue(replay_after_cancel.replayed)
        self.assertEqual(self.store.run(run_id)["version"], terminal_version)

        terminal_before = self.store.connection.execute(
            "SELECT * FROM runs WHERE id=?", (run_id,),
        ).fetchone()
        terminal_before = tuple(terminal_before)
        events_before = [
            tuple(row)
            for row in self.store.connection.execute(
                "SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,),
            )
        ]

        conflicting = self.decision(reason="different body")
        with self.assertRaises(ConflictError):
            self.store.record_handoff_submission(
                run_id, claim, conflicting, now=claimed_at + timedelta(minutes=23),
            )
        terminal = self.store.run(run_id)
        self.assertEqual(terminal["state"], "cancelled")
        self.assertEqual(terminal["phase"], None)
        self.assertEqual(terminal["version"], terminal_version)
        terminal_after = tuple(self.store.connection.execute(
            "SELECT * FROM runs WHERE id=?", (run_id,),
        ).fetchone())
        events_after = [
            tuple(row)
            for row in self.store.connection.execute(
                "SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,),
            )
        ]
        self.assertEqual(terminal_after, terminal_before)
        self.assertEqual(events_after, events_before)
        rejection = self.store.connection.execute(
            "SELECT outcome,rejection_code,recorded_run_version FROM handoff_submissions "
            "WHERE handoff_id=? AND submission_hash=?",
            (snapshot.handoff_id, conflicting["submission_hash"]),
        ).fetchone()
        self.assertEqual(
            tuple(rejection), ("rejected", "submission_id_reused", terminal_version),
        )

        with self.assertRaises(ConflictError):
            self.store.record_handoff_submission(
                run_id, claim, conflicting, now=claimed_at + timedelta(minutes=24),
            )
        self.assertEqual(tuple(self.store.connection.execute(
            "SELECT * FROM runs WHERE id=?", (run_id,),
        ).fetchone()), terminal_before)
        self.assertEqual([
            tuple(row)
            for row in self.store.connection.execute(
                "SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,),
            )
        ], events_before)

    def test_cancel_wait_invalidates_claim_and_publishes_terminal_receipt(self):
        run_id, _, snapshot = self.waiting_run()
        claim = self.store.claim_handoff(
            run_id, snapshot.run_version, "host",
            now=datetime(2026, 9, 15, 5, 1, tzinfo=timezone.utc),
        )
        version = self.store.cancel_host_wait(
            run_id, now=datetime(2026, 9, 15, 5, 2, tzinfo=timezone.utc),
        )
        run = self.store.run(run_id)
        self.assertEqual((run["state"], run["phase"], run["version"]), ("cancelled", None, version))
        persisted_claim = self.store.connection.execute(
            "SELECT active,fencing_token FROM claims WHERE run_id=?", (run_id,),
        ).fetchone()
        self.assertEqual(tuple(persisted_claim), (0, claim.fencing_token + 1))
        receipt = self.store.artifact_named(run_id, "result-receipt.json")
        self.assertIsNotNone(receipt)
        receipt_payload = json.loads(Path(receipt["path"]).read_text())
        self.assertEqual(
            (receipt_payload["state"], receipt_payload["phase"]),
            ("cancelled", "awaiting_host"),
        )
        self.assertEqual(self.store.cancel_host_wait(run_id), version)


class InstalledWheelHandoffMigrationTest(unittest.TestCase):
    @staticmethod
    def build_python():
        candidates = [
            os.environ.get("DEVSQUAD_BUILD_PYTHON"),
            sys.executable,
            str(Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"),
            shutil.which("python3.13"),
            shutil.which("python3.12"),
            shutil.which("python3.11"),
        ]
        for candidate in dict.fromkeys(value for value in candidates if value):
            result = subprocess.run(
                [
                    candidate,
                    "-c",
                    "import setuptools, wheel; assert int(setuptools.__version__.split('.')[0]) >= 68",
                ],
                text=True,
                capture_output=True,
            )
            if result.returncode == 0:
                return candidate
        return None

    def test_installed_wheel_applies_schema_four_to_five(self):
        build_python = self.build_python()
        if build_python is None:
            self.skipTest("offline wheel gate requires setuptools>=68 and wheel")
        with tempfile.TemporaryDirectory(prefix="devsquad-handoff-wheel-") as directory:
            root = Path(directory)
            source = root / "core"
            shutil.copytree(ROOT / "plugin/core", source)
            wheels = root / "wheels"
            wheels.mkdir()
            subprocess.run(
                [
                    build_python,
                    "-m",
                    "pip",
                    "wheel",
                    str(source),
                    "--wheel-dir",
                    str(wheels),
                    "--no-index",
                    "--no-deps",
                    "--no-build-isolation",
                ],
                check=True,
                text=True,
                capture_output=True,
            )
            wheel = next(wheels.glob("devsquad_core-*.whl"))
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            venv = root / "venv"
            subprocess.run([build_python, "-m", "venv", str(venv)], check=True, env=environment)
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            subprocess.run(
                [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
                check=True,
                text=True,
                capture_output=True,
                env=environment,
            )
            probe = r'''
from importlib.resources import files
from pathlib import Path
import sqlite3
import sys
from devsquad.store import Store

root = Path(sys.argv[1])
root.mkdir(parents=True)
database = root / "state.sqlite3"
connection = sqlite3.connect(database)
migrations = files("devsquad.migrations")
for version, name in (
    (1, "001_initial.sql"),
    (2, "002_supervisor.sql"),
    (3, "003_durable_io.sql"),
    (4, "004_run_snapshot.sql"),
):
    connection.executescript(migrations.joinpath(name).read_text())
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES(?, 'fixture')", (version,)
    )
connection.commit()
connection.close()
store = Store(database, root / "artifacts")
try:
    assert store.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 5
    assert store.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='handoff_submissions'"
    ).fetchone()
    claim_columns = {row[1] for row in store.connection.execute("PRAGMA table_info(claims)")}
    assert {"handoff_id", "lease_expires_at", "renewed_at"} <= claim_columns
finally:
    store.close()
'''
            subprocess.run(
                [str(python), "-P", "-c", probe, str(root / "probe-runtime")],
                check=True,
                text=True,
                capture_output=True,
                cwd=root,
                env=environment,
            )


if __name__ == "__main__":
    unittest.main()
