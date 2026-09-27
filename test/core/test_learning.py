import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.contracts import ContractError
from devsquad.learning import validate_outcome
from devsquad.store import ConflictError, Store


NOW = datetime(2026, 9, 27, 16, 0, tzinfo=timezone.utc)


def final_outcome():
    return {
        "schema_version": 1,
        "outcome_id": "outcome-final",
        "kind": "final",
        "verdict": "succeeded",
        "selection_mode": "automatic",
        "observed_at": NOW.isoformat(),
        "corrects_outcome_id": None,
        "summary": "A repair attempt produced the accepted result.",
        "criteria": [{
            "criterion_id": "checks",
            "status": "passed",
            "evidence_refs": ["receipt.json#criteria/checks"],
        }],
        "contributions": [
            {
                "attempt_id": "attempt-original",
                "role": "implementer",
                "result": "failed",
                "independent_success": False,
                "evidence_refs": ["attempt-original.json"],
            },
            {
                "attempt_id": "attempt-repair",
                "role": "implementer",
                "result": "repair",
                "independent_success": False,
                "evidence_refs": ["attempt-repair.json"],
            },
        ],
        "lead_repairs": [],
        "evidence_refs": ["receipt.json"],
    }


class LearningContractTest(unittest.TestCase):
    def test_outcome_contract_rejects_false_success_and_mutation(self):
        normalized = validate_outcome(final_outcome(), now=NOW)
        self.assertEqual(normalized["verdict"], "succeeded")
        changed = final_outcome()
        changed["criteria"][0]["status"] = "failed"
        with self.assertRaisesRegex(ContractError, "must all pass"):
            validate_outcome(changed, now=NOW)
        changed = final_outcome()
        changed["contributions"][0]["independent_success"] = True
        with self.assertRaisesRegex(ContractError, "only a successful"):
            validate_outcome(changed, now=NOW)
        changed = final_outcome()
        changed["extra"] = True
        with self.assertRaisesRegex(ContractError, "fields invalid"):
            validate_outcome(changed, now=NOW)
        changed = final_outcome()
        changed["observed_at"] = (NOW + timedelta(minutes=6)).isoformat()
        with self.assertRaisesRegex(ContractError, "clock skew"):
            validate_outcome(changed, now=NOW)

    def test_final_and_late_outcomes_are_append_only_and_attempt_bound(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            repository = path / "repo"
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.name", "Test"],
                check=True,
            )
            (repository / "README").write_text("fixture\n")
            subprocess.run(["git", "-C", str(repository), "add", "README"], check=True)
            subprocess.run(["git", "-C", str(repository), "commit", "-qm", "base"], check=True)
            store = Store(path / "state.sqlite3", path / "artifacts")
            self.addCleanup(store.close)
            claim = store.claim_start(repository, "outcome-run", {}, "owner")
            run = store.run(claim.run_id)
            store.connection.execute(
                "UPDATE runs SET state='succeeded',phase=NULL WHERE id=?",
                (claim.run_id,),
            )
            for attempt_id, metadata in (
                ("attempt-original", {"failure": {"error": "seeded"}}),
                ("attempt-repair", {}),
            ):
                store.connection.execute(
                    "INSERT INTO attempts(id,run_id,project_id,worktree_path,attempt_token,"
                    "status,heartbeat_at,package_digest,output_metadata,created_at,finished_at,role) "
                    "VALUES(?,?,?,?,?,'finished',?,?,?,?,?,'implementer')",
                    (
                        attempt_id, claim.run_id, run["project_id"], str(repository),
                        f"token-{attempt_id}", NOW.isoformat(), "package",
                        json.dumps(metadata), NOW.isoformat(), NOW.isoformat(),
                    ),
                )

            false_credit = final_outcome()
            false_credit["contributions"][0]["result"] = "successful"
            false_credit["contributions"][0]["independent_success"] = True
            with self.assertRaisesRegex(ConflictError, "failed attempt"):
                store.record_outcome(claim.run_id, false_credit, now=NOW)

            first = store.record_outcome(claim.run_id, final_outcome(), now=NOW)
            replay = store.record_outcome(claim.run_id, final_outcome(), now=NOW)
            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            changed = copy.deepcopy(final_outcome())
            changed["summary"] = "Changed after persistence."
            with self.assertRaisesRegex(ConflictError, "different evidence"):
                store.record_outcome(claim.run_id, changed, now=NOW)

            late = {
                "schema_version": 1,
                "outcome_id": "outcome-late-defect",
                "kind": "late_correction",
                "verdict": "escaped_defect",
                "selection_mode": "automatic",
                "observed_at": (NOW + timedelta(minutes=1)).isoformat(),
                "corrects_outcome_id": "outcome-final",
                "summary": "A defect escaped the original acceptance evidence.",
                "criteria": [{
                    "criterion_id": "checks", "status": "failed",
                    "evidence_refs": ["late-defect.json"],
                }],
                "contributions": [],
                "lead_repairs": [],
                "evidence_refs": ["late-defect.json"],
            }
            predating = copy.deepcopy(late)
            predating["observed_at"] = (NOW - timedelta(seconds=1)).isoformat()
            with self.assertRaisesRegex(ConflictError, "predates"):
                store.record_outcome(claim.run_id, predating, now=NOW)
            store.record_outcome(
                claim.run_id, late, now=NOW + timedelta(minutes=1),
            )
            history = store.outcomes_for_run(claim.run_id)
            self.assertEqual(
                [item["outcome"]["verdict"] for item in history],
                ["succeeded", "escaped_defect"],
            )
            self.assertFalse(
                history[0]["outcome"]["contributions"][0]["independent_success"],
            )

    def test_migration_ten_creates_outcome_ledger(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            store = Store(path / "state.sqlite3", path / "artifacts")
            self.addCleanup(store.close)
            self.assertEqual(
                store.connection.execute(
                    "SELECT MAX(version) FROM schema_migrations",
                ).fetchone()[0],
                10,
            )
            columns = {
                row[1] for row in store.connection.execute("PRAGMA table_info(outcomes)")
            }
            self.assertTrue({"outcome_id", "payload_sha256", "corrects_outcome_id"} <= columns)


if __name__ == "__main__":
    unittest.main()
