"""Contract-epoch fences; immutable installed-old-client proof is separate."""
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))
from devsquad.store import Store, SchemaVersionError, SUPPORTED_SCHEMA_VERSION


class CouncilEpochTest(unittest.TestCase):
    def test_schema16_active_and_recoverable_deferral_then_upgrade_and_old_connection_fence(self):
        # A separately loaded module keeps its connection-schema function at 16
        # after current Store commits 17. This tests existing hot-client triggers,
        # not the older Service's permission semantics or installed package proof.
        old = types.ModuleType("devsquad._controlled_schema16_store")
        old.__package__ = "devsquad"
        old.__file__ = str(ROOT / "plugin/core/src/devsquad/store.py")
        sys.modules[old.__name__] = old
        self.addCleanup(sys.modules.pop, old.__name__, None)
        source = Path(old.__file__).read_text().replace(f"SUPPORTED_SCHEMA_VERSION = {SUPPORTED_SCHEMA_VERSION}", "SUPPORTED_SCHEMA_VERSION = 16", 1)
        exec(compile(source, old.__file__, "exec"), old.__dict__)
        with tempfile.TemporaryDirectory(prefix="council-epoch-public-") as temporary:
            root = Path(temporary)
            repo = root / "repo"
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            database, artifacts = root / "state.sqlite3", root / "artifacts"
            previous = old.Store(database, artifacts)
            self.addCleanup(previous.close)
            before_tables = {r[0] for r in previous.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            active = previous.claim_start(repo, "active16", {"task": "legacy"}, "old16")
            with self.assertRaisesRegex(SchemaVersionError, "deferred.*active/recoverable"):
                Store(database, artifacts)
            previous.complete_preparation(active.run_id, active.fencing_token, {"legacy": True})
            with self.assertRaisesRegex(SchemaVersionError, "deferred.*active/recoverable"):
                Store(database, artifacts)
            self.assertEqual(previous.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 16)
            previous.cancel_queued(active.run_id)
            upgraded = Store(database, artifacts)
            try:
                self.assertEqual(upgraded.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], SUPPORTED_SCHEMA_VERSION)
                self.assertGreaterEqual(SUPPORTED_SCHEMA_VERSION, 17)
                self.assertEqual(before_tables, {r[0] for r in upgraded.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")})
                with self.assertRaisesRegex(sqlite3.DatabaseError, "newer than connection supports"):
                    previous.claim_start(repo, "old16-after17", {"task": "forbidden"}, "old16")
                with self.assertRaisesRegex(old.SchemaVersionError, "newer than supported 16"):
                    old.Store(database, artifacts)
                self.assertEqual(upgraded.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
            finally:
                upgraded.close()


if __name__ == "__main__":
    unittest.main()
