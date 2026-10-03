"""Actual temporary installer gate using exact accepted R5 core provenance."""
from __future__ import annotations

from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import time
import unittest

import test_install_core as installer_tests
import test_council_runtime as council_fixture_tests
from devsquad.store import SUPPORTED_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[2]
OLD_COMMIT = "bf3de0867484552d354e6e8b6ba835f31a93aeb3"
OLD_SOURCE_SHA256 = "90f1e87fb9acf7756dad46780672de9b84fe75e3631322857a310f089280345e"
OLD_PACKAGE_SHA256 = "e03bf3a2e362b3aff6d0a5dd00bd837f15afb216d4c58ac1d9c776c4d8f09c67"


class CouncilInstallEpochTest(unittest.TestCase):
    setUp = installer_tests.StandaloneInstallerTest.setUp
    tearDown = installer_tests.StandaloneInstallerTest.tearDown
    install = installer_tests.StandaloneInstallerTest.install
    cli_json = installer_tests.StandaloneInstallerTest.cli_json

    def _schema(self, runtime):
        with closing(sqlite3.connect(runtime / "state.sqlite3")) as connection:
            return connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]

    def _wait(self, launcher, runtime, run_id, states):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            status = self.cli_json(launcher, "status", run_id, "--runtime-dir", str(runtime))["data"]
            if status["state"] in states:
                return status
            time.sleep(.05)
        self.fail(f"Controlled installed run did not reach {states}: {status}")

    def _python(self, python, script, *arguments):
        completed = subprocess.run([str(python), "-P", "-c", script, *map(str, arguments)],
            capture_output=True, text=True, check=True, env=self.environment, cwd=self.root, timeout=20)
        return json.loads(completed.stdout)

    def test_exact_old16_install_deferral_reconciliation_upgrade_and_old_handoff_mutation_fences(self):
        # This archive's entire core digest is exactly the accepted immutable
        # installed R5 payload, not a rewritten current Store with a lower label.
        archived = subprocess.run(["git", "archive", OLD_COMMIT + ":plugin/core"],
            cwd=ROOT, capture_output=True, check=True).stdout
        old_source = self.root / "accepted-schema16-core"
        old_source.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archived)) as archive:
            digest = hashlib.sha256()
            for member in sorted((member for member in archive.getmembers() if member.isfile()), key=lambda member: member.name):
                digest.update(member.name.encode() + b"\0" + archive.extractfile(member).read())
            self.assertEqual(digest.hexdigest(), OLD_SOURCE_SHA256)
            archive.extractall(old_source, filter="data")
        self.environment["PIP_NO_INDEX"] = "1"
        self.environment["DEVSQUAD_RUNTIME_DIR"] = str(self.install_root / "runtime")
        accepted_python = Path("/Users/Dikshant/.devsquad/releases/0.1.0-py31214-90f1e87fb9ac-mcp-a26bc88afbef/venv/bin/python")
        if accepted_python.is_file():
            self.environment["DEVSQUAD_PYTHON"] = str(accepted_python)
        first = self.install(old_source)
        old_release = Path(first["current_target"])
        old_python = old_release / "venv/bin/python"
        self.assertEqual(json.loads((old_release / "release.json").read_text())["source_digest"], OLD_SOURCE_SHA256)
        runtime, launcher = self.install_root / "runtime", self.bin_dir / "squad"

        # Build harmless public input/fixture documents; all execution below uses
        # the actual temporary installed packages through their own interpreters.
        fixture = council_fixture_tests.CouncilRuntimeTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        profiles = json.loads((fixture.repo / "profiles.json").read_text())
        policy = json.loads((fixture.repo / "policy.json").read_text())
        control = json.loads(json.dumps(fixture.task))
        control.pop("council")
        control["workflow"] = "branch-review"
        control["lead"]["mode"] = "host"
        control["routing"] = {"profiles": profiles, "policy": json.loads(json.dumps(policy))}
        control["routing"]["policy"]["roles"] = {"reviewer": [{"kind": "profile", "id": "critic"}]}
        task_path = self.root / "controlled-old16-task.json"
        task_path.write_text(json.dumps(control))
        script = """
import json,sys
from pathlib import Path
from unittest.mock import patch
from devsquad.service import Service
from devsquad.store import SUPPORTED_SCHEMA_VERSION
s=Service(Path(sys.argv[2]))
task=json.loads(Path(sys.argv[1]).read_text())
live=s.start(task,'accepted16-active',_internal_fake_delay=30)
with patch.object(s,'_spawn_daemon',return_value=0):
    queued=s.start(task,'accepted16-recoverable',_internal_fake_delay=.1)
print(json.dumps({'supported':SUPPORTED_SCHEMA_VERSION,'live':live,'queued':queued}))
"""
        admitted = self._python(old_python, script, task_path, runtime)
        self.assertEqual(admitted["supported"], 16)
        live, queued = admitted["live"]["run_id"], admitted["queued"]["run_id"]
        hot = None
        council_id = None
        try:
            self.assertEqual(self._wait(launcher, runtime, live, {"running"})["state"], "running")
            hot_script = """
import json,sys
from pathlib import Path
from devsquad.store import Store,SUPPORTED_SCHEMA_VERSION
from devsquad.service import Service
runtime=Path(sys.argv[1]); s=Store(runtime/'state.sqlite3',runtime/'artifacts')
package=Service(runtime)._freeze_package()[1]
print(json.dumps({'supported':SUPPORTED_SCHEMA_VERSION,'schema':s.connection.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0],'package_digest':package}),flush=True)
request=json.loads(sys.stdin.readline())
try:
    s.claim_handoff(request['run_id'],request['version'],'old16-long-lived-owner')
except Exception as exc:
    print(json.dumps({'rejected':True,'error':str(exc)}),flush=True)
else:
    print(json.dumps({'rejected':False}),flush=True)
finally:
    s.close()
"""
            hot = subprocess.Popen([str(old_python), "-P", "-c", hot_script, str(runtime)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=self.environment, cwd=self.root)
            ready = json.loads(hot.stdout.readline())
            self.assertEqual(ready, {"supported": 16, "schema": 16, "package_digest": OLD_PACKAGE_SHA256})
            before_selector = (self.install_root / "current").resolve()
            before_launcher = launcher.read_bytes()
            before_state = (self.install_root / "install-state.json").read_bytes()

            def assert_deferred():
                attempted = subprocess.run(["/bin/bash", str(installer_tests.INSTALLER), "--source-core", str(installer_tests.CORE), "--json"],
                    capture_output=True, text=True, env=self.environment, cwd=ROOT, timeout=90)
                self.assertNotEqual(attempted.returncode, 0)
                self.assertIn("active/recoverable", attempted.stderr)
                self.assertEqual((self.install_root / "current").resolve(), before_selector)
                self.assertEqual(launcher.read_bytes(), before_launcher)
                self.assertEqual((self.install_root / "install-state.json").read_bytes(), before_state)
                self.assertEqual(self._schema(runtime), 16)

            assert_deferred()
            self.cli_json(launcher, "cancel", live, "--runtime-dir", str(runtime))
            self.assertEqual(self._wait(launcher, runtime, live, {"cancelled"})["state"], "cancelled")
            # Active work is gone, but a recoverable old run still defers update.
            assert_deferred()
            self.cli_json(launcher, "resume", queued, "--runtime-dir", str(runtime))
            self.assertEqual(self._wait(launcher, runtime, queued, {"succeeded"})["state"], "succeeded")
            self.assertEqual(self._schema(runtime), 16)
            updated = self.install()
            new_release = Path(updated["current_target"])
            self.assertNotEqual(new_release, old_release)
            self.assertTrue(old_release.is_dir())
            self.assertEqual(self._schema(runtime), 16)  # selector activation does not migrate
            self.assertTrue(self.cli_json(launcher, "result", queued, "--runtime-dir", str(runtime))["data"]["ready"])
            self.assertEqual(self._schema(runtime), SUPPORTED_SCHEMA_VERSION)

            council = json.loads(json.dumps(fixture.task))
            council["lead"]["mode"] = "host"
            council["routing"] = {"profiles": profiles, "policy": policy}
            payload = self.root / "controlled-council.json"
            payload.write_text(json.dumps({"task": council, "fixture": fixture.fixture}))
            start_script = """
import json,sys
from pathlib import Path
from devsquad.service import Service
value=json.loads(Path(sys.argv[1]).read_text())
print(json.dumps(Service(Path(sys.argv[2])).start(value['task'],'new17-host-council',_internal_council_fixture=value['fixture'])))
"""
            council_id = self._python(new_release / "venv/bin/python", start_script, payload, runtime)["run_id"]
            waiting = self._wait(launcher, runtime, council_id, {"awaiting_host", "failed"})
            self.assertEqual(waiting["state"], "awaiting_host")
            output, stderr = hot.communicate(json.dumps({"run_id": council_id, "version": waiting["version"]}) + "\n", timeout=8)
            self.assertEqual(hot.returncode, 0, stderr)
            hot_result = json.loads(output)
            self.assertTrue(hot_result["rejected"])
            self.assertIn("newer than connection supports", hot_result["error"])
            fresh_script = """
import json,sys
from pathlib import Path
from devsquad.service import Service
try:
    Service(Path(sys.argv[1])).handoff_claim(sys.argv[2],int(sys.argv[3]),'old16-fresh-owner')
except Exception as exc:
    print(json.dumps({'rejected':True,'type':type(exc).__name__,'error':str(exc)}))
else:
    print(json.dumps({'rejected':False}))
"""
            fresh = self._python(old_python, fresh_script, runtime, council_id, waiting["version"])
            self.assertTrue(fresh["rejected"])
            self.assertEqual(fresh["type"], "SchemaVersionError")
            self.assertIn(f"{SUPPORTED_SCHEMA_VERSION} is newer than supported 16", fresh["error"])
            after = self.cli_json(launcher, "status", council_id, "--runtime-dir", str(runtime))["data"]
            self.assertEqual(after["version"], waiting["version"])
            self.assertIsNone(after["handoff"]["claimed_by"])
            with closing(sqlite3.connect(runtime / "state.sqlite3")) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM claims WHERE run_id=? AND handoff_id IS NOT NULL AND active=1", (council_id,)).fetchone()[0], 0)
            self.cli_json(launcher, "cancel", council_id, "--runtime-dir", str(runtime))
            self.assertEqual(self._wait(launcher, runtime, council_id, {"cancelled"})["state"], "cancelled")
            from devsquad.supervisor import _live_group_exists
            with closing(sqlite3.connect(runtime / "state.sqlite3")) as connection:
                groups = [row[0] for row in connection.execute("SELECT pgid FROM attempts WHERE pgid IS NOT NULL")]
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM runs WHERE state NOT IN ('succeeded','failed','cancelled')").fetchone()[0], 0)
            self.assertTrue(all(not _live_group_exists(group) for group in groups))
            self.epoch_probe_result = {"old_commit": OLD_COMMIT, "old_source_sha256": OLD_SOURCE_SHA256,
                "old_package_sha256": OLD_PACKAGE_SHA256, "old_schema": 16, "new_schema": self._schema(runtime),
                "active_deferral": True, "recoverable_deferral": True, "selector_and_launcher_preserved": True,
                "old_active_cancelled": True, "old_recoverable_reconciled": True,
                "long_lived_old_claim_rejected": True, "fresh_old_service_claim_rejected": True,
                "council_claim_unchanged": True, "native_generation": False, "mcp_installed_in_temporary_release": False,
                "owned_process_groups_gone": True,
                "candidate_source_sha256": json.loads((new_release / "release.json").read_text())["source_digest"]}
        finally:
            if hot is not None:
                if hot.poll() is None:
                    hot.terminate()
                hot.communicate(timeout=5)
            if (runtime / "state.sqlite3").is_file():
                current = (self.install_root / "current").resolve()
                cleanup = "from pathlib import Path;import sys;from devsquad.service import Service;s=Service(Path(sys.argv[1]));[s.cancel(r) for r in sys.argv[2:]]"
                subprocess.run([str(current / "venv/bin/python"), "-P", "-c", cleanup, str(runtime), live, queued, *([council_id] if council_id else [])],
                    capture_output=True, env=self.environment, cwd=self.root, timeout=15)


if __name__ == "__main__":
    unittest.main()
