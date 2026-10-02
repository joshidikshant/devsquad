import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))
sys.path.insert(0, str(ROOT / "test/core/fakes"))

from decision_adapter import FakeDecisionAdapter
from devsquad.contracts import ContractError
from devsquad.decision import build_decision_request
from devsquad.store import ConflictError, Store


NOW = datetime(2026, 9, 29, 3, 0, tzinfo=timezone.utc)


def config(mode="shadow"):
    return {
        "schema_version": 1,
        "mode": mode,
        "purpose": {
            "id": "profile-ranking",
            "version": 1,
            "question_sha256": "a" * 64,
            "rubric_sha256": "b" * 64,
        },
        "adapter": {
            "id": "fixture",
            "model": "fixture-v1",
            "runtime_revision": "fixture-runtime-1",
            "calibration_version": None,
        },
        "language": "en",
        "min_confidence": 0.7,
        "gate_evidence_sha256": None,
        "budget": {
            "max_calls": 1,
            "max_input_bytes": 4096,
            "wall_seconds": 2,
            "max_cost_usd": 0.01,
        },
    }


class DecisionStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devsquad-decision-store-")
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.name", "Test"],
            check=True,
        )
        (self.repo / "README").write_text("fixture\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "README"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "base"], check=True)
        self.store = Store(self.root / "state.sqlite3", self.root / "artifacts")
        self.task = {"scope": {"read_paths": ["README"], "write_paths": []}}
        self.routing = {
            "profile_registry": {"sha256": "d" * 64},
            "roles": {"reviewer": {
                "source": "automatic",
                "selected": {"profile_id": "profile-a"},
                "fallbacks": [{"profile_id": "profile-b"}],
            }},
        }
        self.policy = {"decision_helper": config()}
        self.request = build_decision_request(
            self.task, self.routing, self.policy, "redacted fixture evidence",
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def run_id(self, key):
        return self.store.claim_start(
            self.repo, key, {"fixture": key}, f"owner-{key}",
        ).run_id

    def test_completed_call_is_cached_across_resume_and_runs(self):
        run_one = self.run_id("run-one")
        claimed = self.store.claim_decision_observation(
            run_one, "shadow", self.request, "decision-owner-1", now=NOW,
        )
        self.assertEqual((claimed["action"], claimed["billable_calls"]), ("claimed", 0))
        launched = self.store.launch_decision_call(
            claimed["cache_key"], "decision-owner-1", now=NOW,
        )
        self.assertEqual(launched["billable_calls"], 1)
        adapter = FakeDecisionAdapter({"reviewer": ["profile-b", "profile-a"]})
        completed = self.store.complete_decision_call(
            claimed["cache_key"], "decision-owner-1",
            adapter.decide(self.request), config(), now=NOW,
        )
        self.assertEqual((completed["status"], adapter.calls), ("succeeded", 1))

        resumed = self.store.claim_decision_observation(
            run_one, "shadow", self.request, "decision-owner-2", now=NOW,
        )
        self.assertEqual((resumed["action"], resumed["billable_calls"]), ("cached", 1))
        run_two = self.run_id("run-two")
        shared = self.store.claim_decision_observation(
            run_two, "shadow", self.request, "decision-owner-3", now=NOW,
        )
        self.assertEqual((shared["action"], shared["billable_calls"]), ("cached", 1))
        self.assertEqual(shared["response"], completed["response"])

        effect = {
            "mode": "shadow", "applied_roles": [],
            "role_status": {"reviewer": "shadow_mode"},
        }
        recorded = self.store.record_decision_effect(
            run_two, "profile-ranking", shared["cache_key"], effect, now=NOW,
        )
        self.assertFalse(recorded["applied"])
        self.assertTrue(self.store.record_decision_effect(
            run_two, "profile-ranking", shared["cache_key"], effect, now=NOW,
        )["replayed"])

    def test_launched_unknown_outcome_abstains_without_duplicate_call(self):
        run_id = self.run_id("run-crash")
        claimed = self.store.claim_decision_observation(
            run_id, "shadow", self.request, "decision-owner-old", now=NOW,
        )
        self.store.launch_decision_call(
            claimed["cache_key"], "decision-owner-old", now=NOW,
        )
        recovered = self.store.claim_decision_observation(
            run_id, "shadow", self.request, "decision-owner-new", now=NOW,
        )
        self.assertEqual(recovered["status"], "indeterminate")
        self.assertEqual(recovered["action"], "abstain")
        self.assertEqual(recovered["billable_calls"], 1)
        with self.assertRaisesRegex(ConflictError, "not launchable"):
            self.store.launch_decision_call(
                claimed["cache_key"], "decision-owner-new", now=NOW,
            )

    def test_cancellation_before_launch_records_zero_calls(self):
        run_id = self.run_id("run-cancel")
        claimed = self.store.claim_decision_observation(
            run_id, "shadow", self.request, "decision-owner", now=NOW,
        )
        cancelled = self.store.cancel_run_decision_observations(
            run_id, now=NOW,
        )[0]
        self.assertEqual((cancelled["status"], cancelled["billable_calls"]), ("cancelled", 0))
        replay = self.store.claim_decision_observation(
            run_id, "shadow", self.request, "decision-owner-new", now=NOW,
        )
        self.assertEqual((replay["action"], replay["billable_calls"]), ("abstain", 0))

    def test_invalid_response_is_redacted_and_cannot_be_retried(self):
        run_id = self.run_id("run-invalid")
        claimed = self.store.claim_decision_observation(
            run_id, "shadow", self.request, "decision-owner", now=NOW,
        )
        self.store.launch_decision_call(
            claimed["cache_key"], "decision-owner", now=NOW,
        )
        invalid = FakeDecisionAdapter().decide(self.request)
        invalid["recommendations"]["reviewer"]["probabilities"]["profile-a"] = float("nan")
        completed = self.store.complete_decision_call(
            claimed["cache_key"], "decision-owner", invalid, config(), now=NOW,
        )
        self.assertEqual(completed["status"], "invalid")
        self.assertIsNone(completed["response"])
        self.assertEqual(completed["billable_calls"], 1)
        saved = self.store.decision_observation(run_id, "profile-ranking")
        self.assertEqual(saved["status"], "invalid")
        self.assertIsNone(saved["response"])
        with self.assertRaisesRegex(ConflictError, "not launchable"):
            self.store.launch_decision_call(
                claimed["cache_key"], "decision-owner", now=NOW,
            )

    def test_schema_thirteen_contains_decision_cache_and_run_links(self):
        version = self.store.connection.execute(
            "SELECT MAX(version) FROM schema_migrations",
        ).fetchone()[0]
        self.assertEqual(version, 16)
        tables = {
            row[0] for row in self.store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            )
        }
        self.assertTrue({"decision_cache", "run_decision_observations"} <= tables)


if __name__ == "__main__":
    unittest.main()
