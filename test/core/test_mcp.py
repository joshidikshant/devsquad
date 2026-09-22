import contextlib
import asyncio
import io
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "plugin/core"
sys.path.insert(0, str(CORE / "src"))

from devsquad import cli, mcp_server
from devsquad.contracts import ContractError
from devsquad.store import ConflictError


class MCPDependencyBoundaryTest(unittest.TestCase):
    def test_supported_sdk_is_an_exact_optional_dependency(self):
        project = tomllib.loads((CORE / "pyproject.toml").read_text())["project"]
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertEqual(project["dependencies"], [])
        self.assertEqual(project["optional-dependencies"]["mcp"], ["mcp==2.2.0"])
        self.assertEqual(mcp_server.MCP_SDK_REQUIREMENT, "mcp==2.2.0")
        locked = {
            line.strip().lower()
            for line in (CORE / "requirements-mcp.lock").read_text().splitlines()
            if line.strip() and not line.startswith("#")
        }
        self.assertIn("mcp==2.2.0", locked)

    def test_core_cli_import_does_not_import_optional_sdk(self):
        probe = """
import sys
sys.path.insert(0, {source!r})
import devsquad.cli
assert not any(name == 'mcp' or name.startswith('mcp.') for name in sys.modules)
""".format(source=str(CORE / "src"))
        subprocess.run([sys.executable, "-P", "-c", probe], check=True)

    def test_mcp_serve_dispatches_without_writing_protocol_stdout(self):
        runtime = Path("/tmp/devsquad-mcp-boundary")
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(mcp_server, "serve_stdio") as serve:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = cli.main(["mcp", "serve", "--runtime-dir", str(runtime)])
        self.assertEqual((code, stdout.getvalue(), stderr.getvalue()), (0, "", ""))
        serve.assert_called_once_with(
            runtime, caller_surface=None, caller_session_ref=None,
        )

    def test_missing_sdk_is_actionable_and_keeps_stdout_clean(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        missing = mcp_server.MCPDependencyUnavailable("install devsquad-core[mcp]")
        with mock.patch.object(mcp_server, "serve_stdio", side_effect=missing):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = cli.main(["mcp", "serve"])
        self.assertEqual(code, 69)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("devsquad-core[mcp]", stderr.getvalue())


class MCPBridgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devsquad-mcp-bridge-")
        self.runtime = Path(self.temp.name) / "runtime"
        (self.runtime / "artifacts").mkdir(parents=True)
        self.service = mock.Mock()
        self.bridge = mcp_server.MCPBridge(
            self.runtime, self.service, environment={},
        )

    def tearDown(self):
        self.temp.cleanup()

    def assert_success(self, payload, data):
        self.assertEqual(payload, {
            "schema_version": 1,
            "ok": True,
            "data": data,
            "error": None,
        })

    def test_operations_map_directly_to_the_saved_run_service(self):
        task = {"schema_version": 1}
        recovery = {"attempt_id": "attempt-1", "disposition": "confirm_dead"}
        claim = {"run_id": "run-1", "fencing_token": 3}
        decision = {"disposition": "accept"}
        calls = [
            (
                lambda: self.bridge.start(task, "key-1", "run-0"),
                "start", (task, "key-1", "run-0"),
                {"run_id": "run-1", "state": "queued"},
            ),
            (
                lambda: self.bridge.status("run-1"),
                "status", ("run-1",), {"run_id": "run-1", "state": "running"},
            ),
            (
                lambda: self.bridge.events("run-1", 4, 25),
                "events", ("run-1", 4, 25), {"events": [], "next_cursor": 4},
            ),
            (
                lambda: self.bridge.cancel("run-1"),
                "cancel", ("run-1",), {"run_id": "run-1", "state": "cancelling"},
            ),
            (
                lambda: self.bridge.resume("run-1", recovery),
                "resume", ("run-1", recovery), {"run_id": "run-1", "state": "running"},
            ),
            (
                lambda: self.bridge.handoff_claim("run-1", 7, "claude", claim),
                "handoff_claim", ("run-1", 7, "claude", claim),
                {"run_id": "run-1", "action": "renewed"},
            ),
            (
                lambda: self.bridge.handoff_complete("run-1", claim, decision),
                "handoff_complete", ("run-1", claim, decision),
                {"run_id": "run-1", "state": "succeeded"},
            ),
        ]
        for invoke, method_name, expected_args, response in calls:
            with self.subTest(operation=method_name):
                method = getattr(self.service, method_name)
                method.return_value = response
                self.assert_success(invoke(), response)
                method.assert_called_once_with(*expected_args)
                method.reset_mock()

    def test_contract_conflict_and_internal_failures_keep_machine_envelopes(self):
        cases = [
            (ContractError("bad request"), "INPUT_INVALID"),
            (ConflictError("stale claim"), "CONFLICT"),
            (OSError("disk unavailable"), "INTERNAL_ERROR"),
        ]
        for failure, code in cases:
            with self.subTest(code=code):
                self.service.status.side_effect = failure
                payload = self.bridge.status("run-1")
                self.assertEqual(payload["schema_version"], 1)
                self.assertFalse(payload["ok"])
                self.assertIsNone(payload["data"])
                self.assertEqual(payload["error"]["code"], code)
                self.assertEqual(payload["error"]["message"], str(failure))

    def test_result_caps_preview_bytes_across_saved_artifacts(self):
        first = self.runtime / "artifacts" / "receipt.json"
        second = self.runtime / "artifacts" / "events.jsonl"
        first.write_text("abcdef")
        second.write_text("ghijkl")
        artifacts = [
            {"id": "a-1", "name": first.name, "path": str(first), "sha256": "1" * 64, "byte_size": 6},
            {"id": "a-2", "name": second.name, "path": str(second), "sha256": "2" * 64, "byte_size": 6},
        ]
        self.service.result.return_value = {
            "run_id": "run-1", "ready": True, "state": "succeeded", "artifacts": artifacts,
        }
        payload = self.bridge.result("run-1", preview_bytes=5)
        self.assertTrue(payload["ok"])
        result = payload["data"]
        self.assertEqual(result["preview_bytes"], 5)
        self.assertEqual(result["artifacts"][0]["preview_text"], "abcde")
        self.assertTrue(result["artifacts"][0]["preview_truncated"])
        self.assertIsNone(result["artifacts"][1]["preview_text"])
        self.assertTrue(result["artifacts"][1]["preview_truncated"])
        self.assertEqual(
            {key: result["artifacts"][0][key] for key in ("id", "path", "sha256")},
            {"id": "a-1", "path": str(first), "sha256": "1" * 64},
        )

        too_large = self.bridge.result(
            "run-1", preview_bytes=mcp_server.MAX_ARTIFACT_PREVIEW_BYTES + 1,
        )
        self.assertFalse(too_large["ok"])
        self.assertEqual(too_large["error"]["code"], "INPUT_INVALID")
        self.assertEqual(self.service.result.call_count, 1)

    def test_result_rejects_preview_path_outside_the_runtime(self):
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("not a saved runtime artifact")
        self.service.result.return_value = {
            "run_id": "run-1",
            "ready": True,
            "state": "succeeded",
            "artifacts": [{
                "id": "a-1", "name": outside.name, "path": str(outside),
                "sha256": "1" * 64, "byte_size": outside.stat().st_size,
            }],
        }
        payload = self.bridge.result("run-1")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "CONFLICT")
        self.assertIn("escapes", payload["error"]["message"])

    def test_configured_origin_is_saved_as_provenance_not_authorization(self):
        task = {"schema_version": 1, "origin": {"surface": "user-label"}}
        self.service.start.return_value = {
            "run_id": "run-1", "state": "queued", "created": True,
        }
        bridge = mcp_server.MCPBridge(
            self.runtime,
            self.service,
            caller_surface="codex-app",
            caller_session_ref="thread-7",
            environment={},
        )
        payload = bridge.start(task, "key-1")
        self.assertTrue(payload["ok"])
        submitted = self.service.start.call_args.args[0]
        self.assertEqual(submitted["origin"], {
            "surface": "codex-app", "session_ref": "thread-7",
        })
        self.assertEqual(task["origin"], {"surface": "user-label"})

        self.service.reset_mock()
        user_label_only = mcp_server.MCPBridge(
            self.runtime,
            self.service,
            caller_surface="worker",
            environment={},
        )
        self.service.start.return_value = {
            "run_id": "run-2", "state": "queued", "created": True,
        }
        self.assertTrue(user_label_only.start(task, "key-2")["ok"])
        self.service.start.assert_called_once()

    def test_worker_environment_rejects_every_mutation_but_allows_inspection(self):
        worker_bridge = mcp_server.MCPBridge(
            self.runtime,
            self.service,
            environment={
                "DEVSQUAD_WORKER": "1",
                "DEVSQUAD_RUN_ID": "run-1",
                "DEVSQUAD_DELEGATION_DEPTH": "1",
            },
        )
        mutations = [
            lambda: worker_bridge.start({"schema_version": 1}, "key-1"),
            lambda: worker_bridge.cancel("run-1"),
            lambda: worker_bridge.resume("run-1"),
            lambda: worker_bridge.handoff_claim("run-1", 2, "host"),
            lambda: worker_bridge.handoff_complete("run-1", {}, {}),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                payload = mutate()
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], "POLICY_DENIED")
        for method in (
            self.service.start,
            self.service.cancel,
            self.service.resume,
            self.service.handoff_claim,
            self.service.handoff_complete,
        ):
            method.assert_not_called()

        self.service.status.return_value = {
            "run_id": "run-1", "state": "running", "version": 2,
        }
        self.assertTrue(worker_bridge.status("run-1")["ok"])
        self.service.status.assert_called_once_with("run-1")


@unittest.skipUnless(importlib.util.find_spec("mcp"), "optional MCP SDK is not installed")
class OfficialSDKConformanceTest(unittest.TestCase):
    def test_tool_schemas_and_calls_use_the_official_in_memory_transport(self):
        from mcp import Client

        service = mock.Mock()
        service.status.return_value = {"run_id": "run-1", "state": "running", "version": 2}
        service.start.return_value = {"run_id": "run-1", "state": "queued", "created": True}
        service.cancel.return_value = {"run_id": "run-1", "state": "cancelling", "version": 3}
        with tempfile.TemporaryDirectory(prefix="devsquad-sdk-server-") as directory:
            server = mcp_server.build_server(
                Path(directory), service, environment={},
            )

            async def probe():
                async with Client(server) as client:
                    listing = await client.list_tools()
                    tools = {tool.name: tool for tool in listing.tools}
                    self.assertEqual(set(tools), {
                        "squad_start", "squad_status", "squad_events", "squad_result",
                        "squad_cancel", "squad_resume", "squad_handoff_claim",
                        "squad_handoff_complete",
                    })
                    self.assertEqual(
                        tools["squad_status"].input_schema["required"], ["run_id"],
                    )
                    status = await client.call_tool("squad_status", {"run_id": "run-1"})
                    self.assertFalse(status.is_error)
                    self.assertEqual(status.structured_content["data"]["version"], 2)
                    started = await client.call_tool("squad_start", {
                        "task": {"schema_version": 1}, "idempotency_key": "key-1",
                    })
                    self.assertFalse(started.is_error)
                    self.assertEqual(started.structured_content["data"]["run_id"], "run-1")
                    cancelled = await client.call_tool("squad_cancel", {"run_id": "run-1"})
                    self.assertFalse(cancelled.is_error)
                    self.assertEqual(cancelled.structured_content["data"]["state"], "cancelling")
                    malformed = await client.call_tool("squad_events", {
                        "run_id": "run-1", "after": 0, "limit": "not-an-integer",
                    })
                    self.assertTrue(malformed.is_error)

            asyncio.run(probe())


class InstalledWheelMCPBoundaryTest(unittest.TestCase):
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
                [candidate, "-c", "import setuptools, wheel; assert int(setuptools.__version__.split('.')[0]) >= 68"],
                text=True,
                capture_output=True,
            )
            if result.returncode == 0:
                return candidate
        return None

    def test_plain_installed_wheel_keeps_cli_usable_without_mcp(self):
        build_python = self.build_python()
        if build_python is None:
            self.skipTest("offline wheel gate requires setuptools>=68 and wheel")
        with tempfile.TemporaryDirectory(prefix="devsquad-mcp-wheel-") as directory:
            root = Path(directory)
            source = root / "core"
            shutil.copytree(CORE, source)
            wheels = root / "wheels"
            wheels.mkdir()
            subprocess.run(
                [
                    build_python, "-m", "pip", "wheel", str(source),
                    "--wheel-dir", str(wheels), "--no-index", "--no-deps", "--no-build-isolation",
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
            squad = venv / ("Scripts/squad.exe" if os.name == "nt" else "bin/squad")
            subprocess.run(
                [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
                check=True,
                text=True,
                capture_output=True,
                env=environment,
            )
            version = subprocess.run(
                [str(squad), "--version"], check=True, text=True, capture_output=True, env=environment,
            )
            self.assertEqual(version.stdout.strip(), "squad 0.1.0")
            missing = subprocess.run(
                [str(squad), "mcp", "serve"], text=True, capture_output=True, env=environment,
            )
            self.assertEqual(missing.returncode, 69)
            self.assertEqual(missing.stdout, "")
            self.assertIn("devsquad-core[mcp]", missing.stderr)


if __name__ == "__main__":
    unittest.main()
