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
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.store import (
    ConflictError,
    HandoffClaim,
    SUPPORTED_SCHEMA_VERSION,
    Store,
    canonical_json,
    request_hash,
)
from devsquad.contracts import ContractError


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

    def claim_with_deadline(self, key, deadline):
        run_id, _, snapshot = self.waiting_run(key)
        claim = self.store.claim_handoff(
            run_id,
            snapshot.run_version,
            "host",
            now=deadline - timedelta(minutes=1),
        )
        expires_at = deadline.isoformat()
        self.store.connection.execute(
            "UPDATE claims SET lease_expires_at=? WHERE run_id=?",
            (expires_at, run_id),
        )
        return run_id, HandoffClaim(
            run_id=claim.run_id,
            handoff_id=claim.handoff_id,
            owner_id=claim.owner_id,
            fencing_token=claim.fencing_token,
            expires_at=expires_at,
            run_version=claim.run_version,
            action=claim.action,
        )

    def run_after_independent_writer_wait(self, deadline, operation):
        """Run an operation whose BEGIN IMMEDIATE is blocked across a lease expiry."""
        ready = threading.Event()
        begin_attempted = threading.Event()
        release_started = threading.Event()
        failures = []
        connection = self.store.connection

        class BeginNotifyingConnection:
            def execute(self, statement, *args, **kwargs):
                if statement == "BEGIN IMMEDIATE":
                    begin_attempted.set()
                return connection.execute(statement, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(connection, name)

        def hold_lock():
            writer = sqlite3.connect(self.database, timeout=5, isolation_level=None)
            try:
                writer.execute("PRAGMA busy_timeout=5000")
                writer.execute("BEGIN IMMEDIATE")
                ready.set()
                if not begin_attempted.wait(5):
                    raise AssertionError("handoff operation did not attempt its write transaction")
                # Give the caller time to enter SQLite's busy wait before releasing the lock.
                time.sleep(0.05)
                release_started.set()
                writer.execute("COMMIT")
            except BaseException as exc:  # Preserve thread failures for the assertion owner.
                failures.append(exc)
                ready.set()
            finally:
                if writer.in_transaction:
                    writer.execute("ROLLBACK")
                writer.close()

        thread = threading.Thread(target=hold_lock)
        thread.start()
        self.store.connection = BeginNotifyingConnection()
        try:
            self.assertTrue(ready.wait(5), "independent SQLite writer did not acquire its lock")
            self.assertEqual(failures, [])
            before_expiry = deadline - timedelta(seconds=1)
            after_expiry = deadline + timedelta(seconds=1)
            with mock.patch(
                "devsquad.store._authoritative_now",
                side_effect=lambda value=None: (
                    after_expiry if release_started.is_set() else before_expiry
                ),
            ):
                return operation()
        finally:
            begin_attempted.set()
            thread.join(5)
            self.store.connection = connection
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])

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
            SUPPORTED_SCHEMA_VERSION,
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

    def test_durable_evidence_and_handoff_cross_one_atomic_fence(self):
        run_id, reservation, _ = self.running_run("durable-handoff")
        artifacts = []
        for name, content in (
            (f"{reservation.attempt_id}.stdout", b'{"workflow":"review"}\n'),
            (f"{reservation.attempt_id}.stderr", b""),
            ("review.json", b'{"verdict":"clean"}\n'),
        ):
            path, digest, size = self.store.finalize_artifact(run_id, name, content)
            artifacts.append({
                "name": name,
                "path": path,
                "sha256": digest,
                "byte_size": size,
            })
        review = next(item for item in artifacts if item["name"] == "review.json")
        packet = {
            "schema_version": 1,
            "candidate_sha256": "c" * 64,
            "artifacts": [{"name": review["name"], "sha256": review["sha256"]}],
        }
        outcome = self.store.commit_durable_handoff(
            run_id,
            reservation.attempt_token,
            artifacts,
            {"stdout": {"captured_bytes": 22}, "stderr": {"captured_bytes": 0}},
            packet,
        )
        self.assertEqual(outcome, "awaiting_host")
        snapshot = self.store.handoff_snapshot(run_id)
        self.assertEqual(snapshot.packet["candidate_sha256"], "c" * 64)
        reference = snapshot.packet["artifacts"][0]
        self.assertEqual(set(reference), {"artifact_id", "name", "sha256"})
        artifact = self.store.connection.execute(
            "SELECT name,sha256 FROM artifacts WHERE id=?", (reference["artifact_id"],),
        ).fetchone()
        self.assertEqual((artifact["name"], artifact["sha256"]), (
            "review.json", review["sha256"],
        ))
        self.assertEqual(
            self.store.commit_durable_handoff(
                run_id,
                reservation.attempt_token,
                artifacts,
                {"stdout": {"captured_bytes": 22}, "stderr": {"captured_bytes": 0}},
                packet,
            ),
            "awaiting_host",
        )

        invalid_run, invalid_reservation, _ = self.running_run("invalid-evidence")
        invalid_artifacts = []
        for name, content in (
            (f"{invalid_reservation.attempt_id}.stdout", b"output"),
            (f"{invalid_reservation.attempt_id}.stderr", b""),
        ):
            path, digest, size = self.store.finalize_artifact(
                invalid_run, name, content,
            )
            invalid_artifacts.append({
                "name": name, "path": path, "sha256": digest, "byte_size": size,
            })
        with self.assertRaisesRegex(ContractError, "does not match durable evidence"):
            self.store.commit_durable_handoff(
                invalid_run,
                invalid_reservation.attempt_token,
                invalid_artifacts,
                {},
                {
                    "schema_version": 1,
                    "artifacts": [{"name": "missing.json", "sha256": "0" * 64}],
                },
            )
        self.assertEqual(self.store.run(invalid_run)["state"], "running")

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

    def test_renewal_checks_expiry_after_waiting_for_write_lock(self):
        deadline = datetime(2026, 9, 15, 5, 2, tzinfo=timezone.utc)
        run_id, claim = self.claim_with_deadline("renewal-lock", deadline)
        version_before = self.store.run(run_id)["version"]
        with self.assertRaisesRegex(ConflictError, "stale or expired"):
            self.run_after_independent_writer_wait(
                deadline,
                lambda: self.store.claim_handoff(
                    run_id,
                    version_before,
                    claim.owner_id,
                    claim,
                ),
            )
        self.assertEqual(self.store.run(run_id)["version"], version_before)
        persisted = self.store.connection.execute(
            "SELECT lease_expires_at,renewed_at FROM claims WHERE run_id=?", (run_id,),
        ).fetchone()
        self.assertEqual(tuple(persisted), (claim.expires_at, None))

    def test_completion_checks_expiry_after_waiting_for_write_lock(self):
        deadline = datetime(2026, 9, 15, 5, 2, tzinfo=timezone.utc)
        run_id, claim = self.claim_with_deadline("completion-lock", deadline)
        version_before = self.store.run(run_id)["version"]
        with self.assertRaisesRegex(ConflictError, "expired_claim"):
            self.run_after_independent_writer_wait(
                deadline,
                lambda: self.store.record_handoff_submission(
                    run_id, claim, self.decision(),
                ),
            )
        run = self.store.run(run_id)
        self.assertEqual(
            (run["state"], run["phase"], run["version"]),
            ("awaiting_host", None, version_before + 1),
        )
        rejection = self.store.connection.execute(
            "SELECT outcome,rejection_code,recorded_run_version "
            "FROM handoff_submissions WHERE handoff_id=?",
            (claim.handoff_id,),
        ).fetchone()
        self.assertEqual(
            tuple(rejection), ("rejected", "expired_claim", version_before + 1),
        )

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

    def guided_expiry_recovery(self, key, *, guided=True):
        run_id, _, snapshot = self.waiting_run(key)
        started = datetime(2026, 9, 15, 5, 1, tzinfo=timezone.utc)
        decision = self.decision(submission_id=f"terminal-{snapshot.handoff_id}")
        options = {"initial_only": True, "terminal_decision": decision} if guided else {}
        old = self.store.claim_handoff(run_id, snapshot.run_version, "terminal-operator", now=started, **options)
        later = datetime.fromisoformat(old.expires_at) + timedelta(seconds=1)
        with self.assertRaisesRegex(ConflictError, "expired_claim"):
            self.store.record_handoff_submission(run_id, old, decision, now=later)
        rejected = dict(self.store.connection.execute("SELECT * FROM handoff_submissions WHERE handoff_id=?", (old.handoff_id,)).fetchone())
        current = self.store.claim_handoff(run_id, self.store.run(run_id)["version"], "terminal-operator", now=later, **options)
        return run_id, old, current, decision, later, rejected

    def handoff_rows(self, run_id):
        return {table: [dict(row) for row in self.store.connection.execute(query, (run_id,))] for table, query in {
            "runs": "SELECT * FROM runs WHERE id=?",
            "claims": "SELECT * FROM claims WHERE run_id=?",
            "handoffs": "SELECT * FROM handoffs WHERE run_id=?",
            "submissions": "SELECT s.* FROM handoff_submissions s JOIN handoffs h ON h.id=s.handoff_id WHERE h.run_id=?",
            "events": "SELECT * FROM events WHERE run_id=? ORDER BY id",
        }.items()}

    def test_guided_expiry_audit_and_projection_commit_or_roll_back_together(self):
        for interruption in ("before-audit", "after-audit", "after-projection"):
            with self.subTest(interruption=interruption):
                run_id, old, current, decision, later, rejected = self.guided_expiry_recovery(interruption)
                before = self.handoff_rows(run_id)
                connection = self.store.connection
                class InterruptedConnection:
                    def execute(self, statement, *args, **kwargs):
                        audit = "'handoff.completion_recovered'" in statement
                        projection = statement.startswith("UPDATE handoff_submissions SET")
                        if audit and interruption == "before-audit":
                            raise KeyboardInterrupt
                        result = connection.execute(statement, *args, **kwargs)
                        if (audit and interruption == "after-audit") or (projection and interruption == "after-projection"):
                            raise KeyboardInterrupt
                        return result
                    def __getattr__(self, name):
                        return getattr(connection, name)
                self.store.connection = InterruptedConnection()
                try:
                    with self.assertRaises(KeyboardInterrupt):
                        self.store.record_handoff_submission(run_id, current, decision, now=later)
                finally:
                    self.store.close()
                    self.store = Store(self.database, self.artifacts)
                self.assertEqual(self.handoff_rows(run_id), before)
                recorded = self.store.record_handoff_submission(run_id, current, decision, now=later)
                self.assertFalse(recorded.replayed)
                after = self.handoff_rows(run_id)
                self.assertEqual(after["events"][:-2], before["events"])
                audit_event, submitted = after["events"][-2:]
                self.assertEqual((audit_event["type"], submitted["type"]), ("handoff.completion_recovered", "handoff.submitted"))
                payload = json.loads(audit_event["payload"])
                self.assertEqual(audit_event["payload"], canonical_json(payload))
                self.assertEqual(payload["rejected_submission"], rejected)
                self.assertEqual(payload["rejected_submission_sha256"], request_hash(rejected))
                self.assertEqual(submitted["run_version"], audit_event["run_version"] + 1)
                self.assertEqual(recorded.recorded_run_version, submitted["run_version"])
                self.assertEqual(after["runs"][0]["version"], submitted["run_version"])
                self.assertEqual(after["handoffs"][0]["submitted_run_version"], submitted["run_version"])
                self.assertEqual(after["submissions"][0]["recorded_run_version"], submitted["run_version"])
                replay = self.store.record_handoff_submission(run_id, old, decision, now=later + timedelta(hours=1))
                self.assertTrue(replay.replayed)
                self.assertEqual(replay.recorded_run_version, recorded.recorded_run_version)
                self.assertEqual(self.handoff_rows(run_id), after)

    def test_guided_expiry_recovery_refuses_malformed_prior_row_or_marker(self):
        corruptions = {
            "decision": ("decision_json", "{}"),
            "evidence": ("evidence_refs_json", "[{}]"),
            "owner": ("owner_id", "other-app"),
            "disposition": ("disposition", "reject"),
            "rejection": ("rejection_code", "stale_claim"),
            "created": ("created_at", "not-a-time"),
            "future-created": ("created_at", "2099-01-01T00:00:00+00:00"),
            "same-fence": ("fencing_token", None),
            "same-version": ("recorded_run_version", None),
            "marker-structure": None,
            "marker-packet": None,
        }
        for name, corruption in corruptions.items():
            with self.subTest(corruption=name):
                run_id, _, current, decision, later, rejected = self.guided_expiry_recovery(name)
                if corruption is None:
                    event = self.store.connection.execute("SELECT id,payload FROM events WHERE run_id=? AND type='handoff.taken_over' ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
                    payload = json.loads(event["payload"])
                    if name == "marker-structure":
                        payload["terminal_finish"]["unexpected"] = True
                    else:
                        payload["terminal_finish"]["packet_sha256"] = "0" * 64
                    self.store.connection.execute("UPDATE events SET payload=? WHERE id=?", (canonical_json(payload), event["id"]))
                else:
                    field, value = corruption
                    if name == "same-fence":
                        value = current.fencing_token
                    elif name == "same-version":
                        value = current.run_version
                    self.store.connection.execute(f"UPDATE handoff_submissions SET {field}=? WHERE id=?", (value, rejected["id"]))
                before = self.handoff_rows(run_id)
                with self.assertRaises(ConflictError):
                    self.store.record_handoff_submission(run_id, current, decision, now=later)
                self.assertEqual(self.handoff_rows(run_id), before)

    def test_guided_expiry_recovery_requires_current_marker_not_same_name_app(self):
        for operation in ("ordinary-app", "renew", "takeover", "changed-intent"):
            with self.subTest(operation=operation):
                run_id, _, current, decision, later, _ = self.guided_expiry_recovery(operation, guided=operation != "ordinary-app")
                if operation == "renew":
                    current = self.store.claim_handoff(run_id, current.run_version, "terminal-operator", current, now=later + timedelta(seconds=1))
                    later += timedelta(seconds=1)
                elif operation == "takeover":
                    later = datetime.fromisoformat(current.expires_at) + timedelta(seconds=1)
                    current = self.store.claim_handoff(run_id, current.run_version, "terminal-operator", now=later)
                elif operation == "changed-intent":
                    decision = self.decision(submission_id=decision["submission_id"], reason="different exact intent")
                before = self.handoff_rows(run_id)
                with self.assertRaises(ConflictError):
                    self.store.record_handoff_submission(run_id, current, decision, now=later)
                after = self.handoff_rows(run_id)
                self.assertFalse(any(row["type"] == "handoff.completion_recovered" for row in after["events"]))
                self.assertEqual(after["submissions"][0], before["submissions"][0])
                if operation != "changed-intent":
                    self.assertEqual(after, before)
                    self.assertIsNone(self.store.terminal_finish_decision(run_id, current.handoff_id))

    def test_guided_expiry_recovery_requires_exact_original_rejection_event(self):
        for corruption in ("missing", "type", "created", "row-created", "row-version", "run", "version", "noncanonical", "extra", "handoff", "submission-id", "hash", "reason"):
            with self.subTest(corruption=corruption):
                run_id, _, current, decision, later, rejected = self.guided_expiry_recovery(f"audit-{corruption}")
                event = self.store.connection.execute("SELECT * FROM events WHERE run_id=? AND run_version=?", (run_id, rejected["recorded_run_version"])).fetchone()
                if corruption == "missing":
                    self.store.connection.execute("DELETE FROM events WHERE id=?", (event["id"],))
                elif corruption == "type":
                    self.store.connection.execute("UPDATE events SET type='handoff.submitted' WHERE id=?", (event["id"],))
                elif corruption == "created":
                    self.store.connection.execute("UPDATE events SET created_at=? WHERE id=?", ((later + timedelta(seconds=1)).isoformat(), event["id"]))
                elif corruption == "row-created":
                    self.store.connection.execute("UPDATE handoff_submissions SET created_at=? WHERE id=?", ((later - timedelta(seconds=1)).isoformat(), rejected["id"]))
                elif corruption == "row-version":
                    self.store.connection.execute("UPDATE handoff_submissions SET recorded_run_version=? WHERE id=?", (rejected["recorded_run_version"] - 1, rejected["id"]))
                elif corruption == "run":
                    other, _, _ = self.waiting_run("audit-other-run")
                    self.store.connection.execute("UPDATE events SET run_id=? WHERE id=?", (other, event["id"]))
                elif corruption == "version":
                    self.store.connection.execute("UPDATE events SET run_version=? WHERE id=?", (current.run_version + 100, event["id"]))
                elif corruption == "noncanonical":
                    self.store.connection.execute("UPDATE events SET payload=? WHERE id=?", (json.dumps(json.loads(event["payload"])), event["id"]))
                else:
                    payload = json.loads(event["payload"])
                    if corruption == "extra":
                        payload["extra"] = True
                    else:
                        field = {"handoff": "handoff_id", "submission-id": "submission_id", "hash": "submission_hash", "reason": "reason"}[corruption]
                        payload[field] = "different"
                    self.store.connection.execute("UPDATE events SET payload=? WHERE id=?", (canonical_json(payload), event["id"]))
                before = self.handoff_rows(run_id)
                with self.assertRaisesRegex(ConflictError, "rejection audit"):
                    self.store.record_handoff_submission(run_id, current, decision, now=later)
                self.assertEqual(self.handoff_rows(run_id), before)

    def test_repeated_guided_expiry_retains_one_rejection_and_one_recovery(self):
        run_id, _, current, decision, later, rejected = self.guided_expiry_recovery("repeated-expiry")
        again = datetime.fromisoformat(current.expires_at) + timedelta(seconds=1)
        with self.assertRaisesRegex(ConflictError, "expired_claim"):
            self.store.record_handoff_submission(run_id, current, decision, now=again)
        self.assertEqual(dict(self.store.connection.execute("SELECT * FROM handoff_submissions WHERE id=?", (rejected["id"],)).fetchone()), rejected)
        latest = self.store.claim_handoff(run_id, self.store.run(run_id)["version"], "terminal-operator", now=again, initial_only=True, terminal_decision=decision)
        self.assertGreater(latest.fencing_token, current.fencing_token)
        self.store.record_handoff_submission(run_id, latest, decision, now=again)
        rows = self.handoff_rows(run_id)
        self.assertEqual(sum(row["type"] == "handoff.completion_rejected" for row in rows["events"]), 1)
        self.assertEqual(sum(row["type"] == "handoff.completion_recovered" for row in rows["events"]), 1)
        self.assertEqual(len(rows["submissions"]), 1)

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
            try:
                result = subprocess.run(
                    [
                        candidate,
                        "-c",
                        "import setuptools, wheel; assert int(setuptools.__version__.split('.')[0]) >= 68",
                    ],
                    text=True,
                    capture_output=True,
                )
            except OSError:
                continue
            if result.returncode == 0:
                return candidate
        return None

    def test_build_python_skips_missing_first_candidate_for_supported_interpreter(self):
        missing = str(Path(tempfile.mkdtemp(prefix="devsquad-missing-")) / "no-such-python")
        probed = []

        def fake_run(argv, **kwargs):
            probed.append(argv[0])
            if argv[0] == missing:
                raise FileNotFoundError(argv[0])
            return subprocess.CompletedProcess(argv, 0)

        with (
            mock.patch.dict(os.environ, {"DEVSQUAD_BUILD_PYTHON": missing}),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            result = self.build_python()
        self.assertEqual(probed[0], missing)
        self.assertEqual(result, sys.executable)

    def test_installed_wheel_applies_schema_four_to_twelve(self):
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
    assert store.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 16
    assert store.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='handoff_submissions'"
    ).fetchone()
    claim_columns = {row[1] for row in store.connection.execute("PRAGMA table_info(claims)")}
    assert {"handoff_id", "lease_expires_at", "renewed_at"} <= claim_columns
    attempt_columns = {row[1] for row in store.connection.execute("PRAGMA table_info(attempts)")}
    assert {"profile_id", "profile_index"} <= attempt_columns
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
