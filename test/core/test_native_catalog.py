import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.contracts import ContractError
from devsquad.native_catalog import NativeCatalogCache, native_scope, normalize_codex_limits
from devsquad.capacity import derive_pool_capacity


NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)
MODELS = [{"id": "gpt-test", "supportedReasoningEfforts": ["low"]}]
TARGET = {"harness": "codex", "model_family": "gpt", "model_id": "gpt-test"}


class NativeCatalogTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = NativeCatalogCache(Path(self.temp.name), "scope-a", "v-test")

    def test_last_good_ttl_failure_backoff_and_drift(self):
        first = self.cache.refresh(lambda: MODELS, now=NOW)
        self.assertTrue(first["complete"])
        self.assertEqual(self.cache.refresh(lambda: self.fail("fresh cache queried"), now=NOW), first)
        def failure():
            raise ContractError("AUTH_ERROR private provider diagnostics")
        later = NOW + timedelta(days=2)
        failed = self.cache.refresh(failure, now=later)
        self.assertEqual(failed["models"], first["models"])
        self.assertEqual(failed["last_refresh"]["status"], "error")
        self.assertNotIn("private provider", json.dumps(failed))
        self.cache.refresh(lambda: self.fail("backoff ignored"), now=later + timedelta(seconds=10))
        changed = self.cache.refresh(lambda: [], now=later + timedelta(minutes=3))
        self.assertEqual(changed["catalog_change"]["removed_model_ids"], ["gpt-test"])

    def test_concurrent_refresh_and_dead_owner_release(self):
        self.cache.refresh(lambda: MODELS, now=NOW)
        entered, release = threading.Event(), threading.Event()
        result = []
        def slow():
            entered.set()
            self.assertTrue(release.wait(5))
            return MODELS
        owner = threading.Thread(target=lambda: result.append(self.cache.refresh(slow, now=NOW + timedelta(days=2))))
        owner.start()
        self.assertTrue(entered.wait(5))
        try:
            cached = self.cache.refresh(lambda: self.fail("second refresh owner"), now=NOW + timedelta(days=2))
            self.assertEqual(cached["models"][0]["id"], "gpt-test")
        finally:
            release.set()
            owner.join(5)
        self.assertEqual(len(result), 1)
        def interrupted():
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.cache.refresh(interrupted, now=NOW + timedelta(days=4))
        self.assertTrue(self.cache.refresh(lambda: MODELS, now=NOW + timedelta(days=4))["complete"])

    def test_scope_is_private_and_changes_for_account_config_binary_version(self):
        account = {"account": {"type": "chatgpt", "email": "private@example.invalid", "planType": "plus"}}
        scope = native_scope(account, {"provider": "native"}, "/binary/a", "v1")
        for a, c, b, v in (
            ({"account": {"type": "chatgpt", "email": "other@example.invalid"}}, {}, "/binary/a", "v1"),
            (account, {"provider": "changed"}, "/binary/a", "v1"),
            (account, {"provider": "native"}, "/binary/b", "v1"),
            (account, {"provider": "native"}, "/binary/a", "v2"),
        ):
            self.assertNotEqual(scope, native_scope(a, c, b, v))
        self.assertNotIn("private", scope)
        for account in ({"account": None}, {"account": {"type": "apiKey"}}, {"account": {"type": "chatgpt"}}):
            with self.assertRaises(ContractError):
                native_scope(account, {}, "/binary/a", "v1")
        other = NativeCatalogCache(Path(self.temp.name), "scope-b", "v-test")
        with self.assertRaises(ContractError):
            other.refresh(lambda: (_ for _ in ()).throw(TimeoutError()), now=NOW)

    def test_weekly_limit_blocks_available_primary_and_null_is_unknown(self):
        payload = {"rateLimitsByLimitId": {"codex": {
            "primary": {"usedPercent": 10, "windowDurationMins": 300, "resetsAt": int((NOW + timedelta(hours=5)).timestamp())},
            "secondary": {"usedPercent": 100, "windowDurationMins": 10080, "resetsAt": int((NOW + timedelta(days=5)).timestamp())},
        }}}
        observations = normalize_codex_limits(payload, "pool", now=NOW)
        self.assertEqual(derive_pool_capacity("pool", observations, target=TARGET, now=NOW)["status"], "exhausted")
        self.assertEqual(derive_pool_capacity("pool", observations, target=TARGET, now=NOW + timedelta(minutes=2))["status"], "unknown")
        unknown = normalize_codex_limits({"rateLimitsByLimitId": {}, "rateLimits": payload["rateLimitsByLimitId"]["codex"]}, "pool", now=NOW)
        self.assertEqual(derive_pool_capacity("pool", unknown, target=TARGET, now=NOW)["status"], "unknown")
        self.assertTrue(all(o["used"] is None for o in unknown))
        malformed = normalize_codex_limits({"rateLimits": {"primary": {"usedPercent": True}}}, "pool", now=NOW)
        self.assertEqual(derive_pool_capacity("pool", malformed, target=TARGET, now=NOW)["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
