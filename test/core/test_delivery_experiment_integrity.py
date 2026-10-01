"""Delivery exposure requires semantically valid, durably imported evidence."""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from experiment_runtime_fixture import ExperimentRuntimeFixture
from devsquad.contracts import ContractError
from devsquad.store import canonical_json


class DeliveryExperimentIntegrityTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="devsquad-delivery-evidence-")
        self.addCleanup(temporary.cleanup)
        self.fixture = ExperimentRuntimeFixture(Path(temporary.name), workflow="issue-delivery")
        self.addCleanup(self.fixture.close)
        self.run_id = self.fixture.run_arm("eval-1", "candidate")
        self.store = self.fixture.store()
        self.addCleanup(self.store.close)

    @contextmanager
    def changed_artifact(self, row, content, *, attempt=None):
        path = Path(row["path"])
        original = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        path.write_bytes(content)
        self.store.connection.execute("UPDATE artifacts SET sha256=?,byte_size=? WHERE id=?",
                                      (digest, len(content), row["id"]))
        if attempt is not None:
            metadata = json.loads(attempt["output_metadata"])
            metadata["stdout"].update(captured_sha256=digest, captured_bytes=len(content),
                                      full_sha256=digest, total_bytes=len(content))
            self.store.connection.execute("UPDATE attempts SET output_metadata=? WHERE id=?",
                                          (canonical_json(metadata), attempt["id"]))
        try:
            yield
        finally:
            path.write_bytes(original)
            self.store.connection.execute("UPDATE artifacts SET sha256=?,byte_size=? WHERE id=?",
                                          (row["sha256"], row["byte_size"], row["id"]))
            if attempt is not None:
                self.store.connection.execute("UPDATE attempts SET output_metadata=? WHERE id=?",
                                              (attempt["output_metadata"], attempt["id"]))

    def rejected(self):
        with self.assertRaises(ContractError):
            self.fixture.service.policy_evaluate(self.fixture.spec)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM experiments").fetchone()[0], 0)

    def test_hash_consistent_malformed_delivery_streams_cannot_count_as_exposure(self):
        for role in ("implementer", "reviewer"):
            attempt = dict(self.store.connection.execute(
                "SELECT * FROM attempts WHERE run_id=? AND role=?", (self.run_id, role)).fetchone())
            row = dict(self.store.connection.execute(
                "SELECT * FROM artifacts WHERE id=?", (attempt["stdout_artifact_id"],)).fetchone())
            with self.subTest(role=role), self.changed_artifact(row, b'{"not":"worker evidence"}', attempt=attempt):
                self.rejected()

    def test_successful_delivery_requires_both_imports(self):
        for prefix in ("implementation", "review"):
            row = self.store.connection.execute(
                "SELECT id,name FROM artifacts WHERE run_id=? AND name LIKE ?",
                (self.run_id, f"{prefix}-attempt-%.json")).fetchone()
            with self.subTest(prefix=prefix):
                self.store.connection.execute("UPDATE artifacts SET name='hidden-import.json' WHERE id=?", (row["id"],))
                try:
                    self.rejected()
                finally:
                    self.store.connection.execute("UPDATE artifacts SET name=? WHERE id=?", (row["name"], row["id"]))

    def test_saved_candidate_and_imported_usage_are_bound_to_the_stream(self):
        for name in ("candidate-1.json", "implementation-attempt-%", "review-attempt-%", "checks-%"):
            row = dict(self.store.connection.execute(
                "SELECT * FROM artifacts WHERE run_id=? AND name LIKE ?", (self.run_id, name)).fetchone())
            changed = json.loads(Path(row["path"]).read_bytes())
            if name.startswith("candidate"):
                changed["commit_oid"] = "0" * 40
            elif name.startswith("checks"):
                changed["results"][0]["target_oid"] = "0" * 40
            else:
                attempt = changed.get("attempt", changed)
                attempt["usage"] = {"input_tokens": 1, "output_tokens": 1,
                                     "total_tokens": 2, "source": "native_reported"}
            with self.subTest(name=name), self.changed_artifact(row, canonical_json(changed).encode()):
                self.rejected()

    def test_real_failed_writer_requires_an_exact_terminal_receipt(self):
        fixture = ExperimentRuntimeFixture(self.fixture.root / "failed-writer", workflow="issue-delivery", fail_candidate=True)
        self.addCleanup(fixture.close)
        fixture.run_all()
        run_id = fixture.runs[("eval-1", "candidate")]
        store = fixture.store()
        self.addCleanup(store.close)
        self.assertEqual(fixture.service.status(run_id)["state"], "failed")
        evaluated = fixture.service.policy_evaluate(fixture.spec)
        self.assertEqual(evaluated["evaluation"]["cases"][0]["candidate_verdict"], "failed")
        row = dict(store.connection.execute("SELECT * FROM artifacts WHERE run_id=? AND name='result-receipt.json'", (run_id,)).fetchone())
        content = json.loads(Path(row["path"]).read_bytes())
        content["attempts"][0]["status"] = "succeeded"
        original_store = self.store
        self.store = store
        try:
            with self.changed_artifact(row, canonical_json(content).encode()):
                with self.assertRaisesRegex(ContractError, "failed implementer receipt|stale"):
                    fixture.service.policy_evaluate(fixture.spec)
        finally:
            self.store = original_store
