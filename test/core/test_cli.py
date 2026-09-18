import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad import cli
from devsquad.contracts import ContractError
from devsquad.store import ConflictError, SchemaVersionError


class CliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devsquad-cli-")
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.task_file = self.root / "task.json"
        self.task_file.write_text('{"schema_version": 1}\n')

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, argv, service=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        patcher = mock.patch.object(cli, "Service", return_value=service) if service is not None else contextlib.nullcontext()
        with patcher, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(argv)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 1, stdout.getvalue())
        return code, json.loads(lines[0]), stderr.getvalue()

    def assert_success_envelope(self, payload, data):
        self.assertEqual(payload, {
            "schema_version": 1,
            "ok": True,
            "data": data,
            "error": None,
        })

    def assert_error_envelope(self, payload, code, message):
        self.assertEqual(payload, {
            "schema_version": 1,
            "ok": False,
            "data": None,
            "error": {
                "code": code,
                "message": message,
                "retryable": False,
                "details": {},
            },
        })

    def test_start_returns_exact_envelope_and_forwards_inputs(self):
        service = mock.Mock()
        response = {"run_id": "run-1", "state": "queued", "created": True}
        service.start.return_value = response
        code, payload, stderr = self.invoke([
            "start", "--task-file", str(self.task_file),
            "--idempotency-key", "key-1", "--supersedes-run", "old-run",
            "--runtime-dir", str(self.runtime), "--json",
        ], service)
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assert_success_envelope(payload, response)
        service.start.assert_called_once_with({"schema_version": 1}, "key-1", "old-run")
        service.status.assert_not_called()

    def test_status_events_result_cancel_and_resume_operations(self):
        cases = [
            (["status", "run-1"], "status", ("run-1",), {"run_id": "run-1", "state": "failed"}),
            (["events", "run-1", "--after", "7", "--limit", "9"], "events", ("run-1", 7, 9), {"events": [], "next_cursor": 7}),
            (["result", "run-1"], "result", ("run-1",), {"run_id": "run-1", "ready": False}),
            (["cancel", "run-1"], "cancel", ("run-1",), {"run_id": "run-1", "state": "cancelling"}),
        ]
        for arguments, method_name, expected_args, response in cases:
            with self.subTest(command=arguments[0]):
                service = mock.Mock()
                getattr(service, method_name).return_value = response
                code, payload, stderr = self.invoke(arguments + ["--runtime-dir", str(self.runtime), "--json"], service)
                self.assertEqual((code, stderr), (0, ""))
                self.assert_success_envelope(payload, response)
                getattr(service, method_name).assert_called_once_with(*expected_args)

        recovery_file = self.root / "recovery.json"
        recovery_file.write_text('{"attempt_id":"a-1","disposition":"confirm_dead"}\n')
        service = mock.Mock()
        response = {"run_id": "run-1", "disposition": "continued", "launched": True}
        service.resume.return_value = response
        code, payload, stderr = self.invoke([
            "resume", "run-1", "--recovery-file", str(recovery_file),
            "--runtime-dir", str(self.runtime), "--json",
        ], service)
        self.assertEqual((code, stderr), (0, ""))
        self.assert_success_envelope(payload, response)
        service.resume.assert_called_once_with("run-1", {"attempt_id": "a-1", "disposition": "confirm_dead"})

    def test_handoff_claim_renew_and_complete_dispatch_parsed_objects(self):
        claim_payload = {
            "schema_version": 1,
            "run_id": "run-1",
            "handoff_id": "handoff-1",
            "owner": "terminal-a",
            "fencing_token": 7,
            "expires_at": "2026-09-15T06:00:00+00:00",
            "run_version": 11,
        }
        claim_file = self.root / "claim.json"
        claim_file.write_text(json.dumps(claim_payload))
        decision_payload = {
            "schema_version": 1,
            "submission_id": "submission-1",
            "submission_hash": "a" * 64,
            "disposition": "accept",
            "reason": "accepted",
            "evidence_refs": [],
        }
        decision_file = self.root / "decision.json"
        decision_file.write_text(json.dumps(decision_payload))

        service = mock.Mock()
        response = {
            "run_id": "run-1", "state": "awaiting_host", "version": 12,
            "claim": claim_payload,
        }
        service.handoff_claim.return_value = response
        code, payload, stderr = self.invoke([
            "handoff", "claim", "run-1", "--expected-version", "11",
            "--owner", "terminal-a", "--claim-file", str(claim_file),
            "--runtime-dir", str(self.runtime), "--json",
        ], service)
        self.assertEqual((code, stderr), (0, ""))
        self.assert_success_envelope(payload, response)
        service.handoff_claim.assert_called_once_with(
            "run-1", 11, "terminal-a", claim_payload,
        )

        service = mock.Mock()
        response = {
            "run_id": "run-1", "state": "awaiting_host",
            "phase": "handoff_submitted", "replayed": False,
        }
        service.handoff_complete.return_value = response
        code, payload, stderr = self.invoke([
            "handoff", "complete", "run-1", "--claim-file", str(claim_file),
            "--decision-file", str(decision_file),
            "--runtime-dir", str(self.runtime), "--json",
        ], service)
        self.assertEqual((code, stderr), (0, ""))
        self.assert_success_envelope(payload, response)
        service.handoff_complete.assert_called_once_with(
            "run-1", claim_payload, decision_payload,
        )

        code, payload, _ = self.invoke(["handoff"])
        self.assertEqual(code, 64)
        self.assertEqual(payload["error"]["code"], "INPUT_INVALID")

    def test_parser_and_json_file_failures_are_input_errors(self):
        code, payload, _ = self.invoke(["status"])
        self.assertEqual(code, 64)
        self.assertEqual(payload["error"]["code"], "INPUT_INVALID")

        malformed = self.root / "malformed.json"
        malformed.write_text("{")
        code, payload, _ = self.invoke([
            "start", "--task-file", str(malformed), "--idempotency-key", "key",
            "--runtime-dir", str(self.runtime), "--json",
        ])
        self.assertEqual(code, 64)
        self.assertEqual(payload["error"]["code"], "INPUT_INVALID")
        self.assertIn("cannot read task file", payload["error"]["message"])

    def test_contract_conflict_schema_and_runtime_errors_have_exact_exits(self):
        cases = [
            (ContractError("bad input"), 64, "INPUT_INVALID"),
            (ConflictError("run version changed"), 75, "CONFLICT"),
            (SchemaVersionError("database is newer"), 75, "SCHEMA_UNSUPPORTED"),
            (OSError("disk failed"), 1, "INTERNAL_ERROR"),
            (RuntimeError("unexpected"), 1, "INTERNAL_ERROR"),
        ]
        for exception, expected_exit, expected_code in cases:
            with self.subTest(exception=type(exception).__name__):
                service = mock.Mock()
                service.status.side_effect = exception
                code, payload, stderr = self.invoke([
                    "status", "run-1", "--runtime-dir", str(self.runtime), "--json",
                ], service)
                self.assertEqual((code, stderr), (expected_exit, ""))
                self.assert_error_envelope(payload, expected_code, str(exception))

    def test_wait_maps_every_terminal_and_paused_state_to_contract_exit(self):
        expected = {
            "succeeded": 0,
            "blocked": 2,
            "awaiting_host": 2,
            "failed": 3,
            "cancelled": 4,
        }
        for state, expected_exit in expected.items():
            with self.subTest(state=state):
                service = mock.Mock()
                service.start.return_value = {"run_id": "run-1", "state": "queued", "created": True}
                status = {"run_id": "run-1", "state": state, "version": 3}
                service.status.return_value = status
                code, payload, stderr = self.invoke([
                    "start", "--task-file", str(self.task_file), "--idempotency-key", "key",
                    "--wait", "--runtime-dir", str(self.runtime), "--json",
                ], service)
                self.assertEqual((code, stderr), (expected_exit, ""))
                self.assert_success_envelope(payload, status)
                service.cancel.assert_not_called()

    def test_wait_observes_active_states_until_terminal(self):
        service = mock.Mock()
        service.start.return_value = {"run_id": "run-1", "state": "queued", "created": True}
        service.status.side_effect = [
            {"run_id": "run-1", "state": "queued"},
            {"run_id": "run-1", "state": "running"},
            {"run_id": "run-1", "state": "succeeded"},
        ]
        with mock.patch.object(cli.time, "sleep") as sleep:
            code, payload, _ = self.invoke([
                "start", "--task-file", str(self.task_file), "--idempotency-key", "key",
                "--wait", "--runtime-dir", str(self.runtime), "--json",
            ], service)
        self.assertEqual(code, 0)
        self.assertEqual(payload["data"]["state"], "succeeded")
        self.assertEqual(service.status.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        service.cancel.assert_not_called()

    def test_wait_keyboard_interrupt_stops_observation_without_cancelling(self):
        service = mock.Mock()
        service.start.return_value = {"run_id": "run-42", "state": "running", "created": True}
        service.status.side_effect = KeyboardInterrupt()
        code, payload, stderr = self.invoke([
            "start", "--task-file", str(self.task_file), "--idempotency-key", "key",
            "--wait", "--runtime-dir", str(self.runtime), "--json",
        ], service)
        self.assertEqual(code, 130)
        self.assert_success_envelope(payload, {
            "run_id": "run-42",
            "state": "running",
            "observation_stopped": True,
            "cancelled": False,
            "next_action": "squad cancel run-42",
        })
        self.assertIn("run-42", stderr)
        self.assertIn("squad cancel run-42", stderr)
        self.assertIn("not cancelled", stderr)
        service.cancel.assert_not_called()

    def test_wait_rejects_unknown_service_state_as_internal_error(self):
        service = mock.Mock()
        service.start.return_value = {"run_id": "run-1", "state": "queued", "created": True}
        service.status.return_value = {"run_id": "run-1", "state": "mystery"}
        code, payload, _ = self.invoke([
            "start", "--task-file", str(self.task_file), "--idempotency-key", "key",
            "--wait", "--runtime-dir", str(self.runtime), "--json",
        ], service)
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "INTERNAL_ERROR")


class InstalledWheelMigrationTest(unittest.TestCase):
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
            result = subprocess.run([
                candidate, "-c",
                "import setuptools, wheel; assert int(setuptools.__version__.split('.')[0]) >= 68",
            ], text=True, capture_output=True)
            if result.returncode == 0:
                return candidate
        return None

    def test_installed_wheel_contains_and_applies_migrations_through_eight(self):
        build_python = self.build_python()
        if build_python is None:
            self.skipTest("offline wheel gate requires setuptools>=68 and wheel; set DEVSQUAD_BUILD_PYTHON")
        with tempfile.TemporaryDirectory(prefix="devsquad-wheel-") as directory:
            root = Path(directory)
            source = root / "core"
            shutil.copytree(ROOT / "plugin/core", source)
            wheels = root / "wheels"
            wheels.mkdir()
            subprocess.run([
                build_python, "-m", "pip", "wheel", str(source),
                "--wheel-dir", str(wheels), "--no-index", "--no-deps", "--no-build-isolation",
            ], check=True, text=True, capture_output=True)
            wheel = next(wheels.glob("devsquad_core-*.whl"))
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            venv = root / "venv"
            subprocess.run([build_python, "-m", "venv", str(venv)], check=True, env=environment)
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            subprocess.run([
                str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel),
            ], check=True, text=True, capture_output=True, env=environment)
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
for version, name in ((1, "001_initial.sql"), (2, "002_supervisor.sql"), (3, "003_durable_io.sql")):
    connection.executescript(migrations.joinpath(name).read_text())
    connection.execute("INSERT INTO schema_migrations(version, applied_at) VALUES(?, 'fixture')", (version,))
connection.commit()
connection.close()
store = Store(database, root / "artifacts")
try:
    assert store.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 8
    attempt_columns = {row[1] for row in store.connection.execute("PRAGMA table_info(attempts)")}
    assert {"role", "account_pool_id", "profile_id", "profile_index"} <= attempt_columns
    columns = {row[1] for row in store.connection.execute("PRAGMA table_info(runs)")}
    assert {"package_path", "package_digest", "supersedes_run_id"} <= columns
    assert store.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='handoffs'"
    ).fetchone()
finally:
    store.close()
'''
            subprocess.run([
                str(python), "-P", "-c", probe, str(root / "probe-runtime"),
            ], check=True, text=True, capture_output=True, cwd=root, env=environment)


if __name__ == "__main__":
    unittest.main()
