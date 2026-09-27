import copy
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.capacity import derive_pool_capacity, validate_observation
from devsquad.contracts import ContractError
from devsquad.store import Store


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
            self.assertEqual(version, 9)
            tables = {
                row[0] for row in store.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
            self.assertTrue({"pool_observations", "pool_reservations"} <= tables)


if __name__ == "__main__":
    unittest.main()
