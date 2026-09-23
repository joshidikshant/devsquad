import contextlib
import asyncio
import io
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "plugin/core"
sys.path.insert(0, str(CORE / "src"))

from devsquad import cli, diagnostics, mcp_server
from devsquad.contracts import ContractError
from devsquad.integrations import (
    IntegrationTemplate,
    LocalIntegrationManager,
    load_integrations,
)
from devsquad.service import Service
from devsquad.store import ConflictError
from devsquad_test_fixtures import branch_review_routing_documents


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


class MCPIntegrationTemplateTest(unittest.TestCase):
    def test_four_host_templates_render_absolute_argv_without_a_shell(self):
        templates = {template.id: template for template in load_integrations()}
        self.assertEqual(set(templates), {
            "codex", "claude-code", "antigravity", "grok",
        })
        host = Path(sys.executable)
        squad = CORE / "bin/squad"
        prefixes = {
            "codex": ("mcp", "add", "devsquad", "--"),
            "claude-code": (
                "mcp", "add", "--scope", "user", "devsquad", "--",
            ),
            "antigravity": ("mcp", "add", "devsquad", "--"),
            "grok": (
                "mcp", "add", "--scope", "user", "devsquad", "--",
            ),
        }
        for integration_id, template in templates.items():
            with self.subTest(integration=integration_id):
                command = template.registration_command(host, squad)
                resolved_host = str(host.resolve(strict=True))
                resolved_squad = str(squad.resolve(strict=True))
                self.assertEqual(command[0], resolved_host)
                self.assertEqual(command[1:1 + len(prefixes[integration_id])], prefixes[integration_id])
                squad_index = command.index(resolved_squad)
                self.assertEqual(command[squad_index + 1:squad_index + 4], (
                    "mcp", "serve", "--surface",
                ))
                self.assertEqual(command[squad_index + 4], template.surface)
                self.assertFalse(any("{" in argument for argument in command))
                inspection = template.inspection_command(host, squad)
                self.assertEqual(inspection[0], resolved_host)
                self.assertIn("mcp", inspection)
                removal = template.removal_command(host)
                if integration_id == "claude-code":
                    self.assertIsNotNone(removal)
                    self.assertIn("remove", removal)
                else:
                    self.assertIsNone(removal)

    def test_template_schema_rejects_unknown_placeholders_and_fields(self):
        with tempfile.TemporaryDirectory(prefix="devsquad-template-") as directory:
            path = Path(directory) / "registration.json"
            template = {
                "schema_version": 1,
                "id": "bad",
                "display_name": "Bad",
                "executable_paths": [],
                "executable_names": ["bad"],
                "server_name": "devsquad",
                "surface": "bad",
                "register_argv": ["{unknown}"],
                "remove_argv": None,
                "inspect_argv": ["{host_executable}"],
                "inspect_format": "text",
            }
            path.write_text(json.dumps(template))
            with self.assertRaisesRegex(ContractError, "unknown integration placeholders"):
                IntegrationTemplate.load(path)
            template["unexpected"] = True
            path.write_text(json.dumps(template))
            with self.assertRaisesRegex(ContractError, "fields differ"):
                IntegrationTemplate.load(path)


class FakeMCPHost:
    def __init__(self, template, home):
        self.template = template
        self.home = home
        self.loaded = None
        self.registration_calls = 0

    def _config_path(self):
        return {
            "codex": self.home / ".codex/config.toml",
            "claude-code": self.home / ".claude.json",
            "antigravity": self.home / ".gemini/config/mcp_config.json",
            "grok": self.home / ".grok/config.toml",
        }[self.template.id]

    def seed_unrelated_config(self):
        path = self._config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".json":
            path.write_text(json.dumps({"unrelated": {"credential": "preserve-me"}}))
        else:
            path.write_text('unrelated = "preserve-me"\n')

    def _save_registration(self, command, args):
        path = self._config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".json":
            value = json.loads(path.read_text()) if path.exists() else {}
            value.setdefault("mcpServers", {})["devsquad"] = {
                "command": command,
                "args": args,
            }
            if self.template.id == "antigravity":
                value["mcpServers"]["devsquad"]["disabled"] = False
            path.write_text(json.dumps(value))
        else:
            existing = path.read_text() if path.exists() else ""
            if "[mcp_servers.devsquad]" not in existing:
                enabled = "enabled = true\n" if self.template.id == "grok" else ""
                path.write_text(
                    existing
                    + "\n[mcp_servers.devsquad]\n"
                    + f"command = {json.dumps(command)}\n"
                    + f"args = {json.dumps(args)}\n"
                    + enabled
                )

    def _inspection_result(self, argv):
        if self.loaded is None:
            if self.template.id == "grok":
                return subprocess.CompletedProcess(argv, 0, "[]\n", "")
            if self.template.id == "antigravity":
                return subprocess.CompletedProcess(
                    argv, 0, "NAME  TYPE  STATUS  COMMAND/URL\n", "",
                )
            return subprocess.CompletedProcess(argv, 1, "", "not found")
        command, args = self.loaded
        if self.template.id == "codex":
            stdout = json.dumps({
                "name": "devsquad",
                "enabled": True,
                "transport": {"command": command, "args": args, "env": None},
            })
        elif self.template.id == "grok":
            stdout = json.dumps([{
                "name": "devsquad", "enabled": True, "scope": "user",
                "command": command, "args": args,
            }])
        elif self.template.id == "claude-code":
            stdout = (
                "devsquad:\n"
                "  Scope: User config (available in all your projects)\n"
                "  Status: ✓ Connected\n"
                "  Type: stdio\n"
                f"  Command: {command}\n"
                f"  Args: {shlex.join(args)}\n"
                "  Environment: PRIVATE_TOKEN=not-reported\n"
            )
        else:
            stdout = (
                "NAME      TYPE   STATUS   COMMAND/URL\n"
                f"devsquad  stdio  enabled  {shlex.join([command, *args])}\n"
            )
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    def __call__(self, argv, **_):
        argv = tuple(argv)
        if "remove" in argv:
            self.loaded = None
            path = self._config_path()
            value = json.loads(path.read_text())
            value.get("mcpServers", {}).pop("devsquad", None)
            path.write_text(json.dumps(value))
            return subprocess.CompletedProcess(argv, 0, "removed\n", "")
        if "add" not in argv:
            return self._inspection_result(argv)
        self.registration_calls += 1
        delimiter = argv.index("--")
        command = argv[delimiter + 1]
        args = list(argv[delimiter + 2:])
        self.loaded = (command, args)
        self._save_registration(command, args)
        return subprocess.CompletedProcess(argv, 0, "registered\n", "")


class LocalMCPRegistrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devsquad-local-mcp-")
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.project = self.root / "project"
        self.home.mkdir()
        self.project.mkdir()
        self.squad = CORE / "bin/squad"

    def tearDown(self):
        self.temp.cleanup()

    def manager(self, fake):
        return LocalIntegrationManager(
            project=self.project,
            home=self.home,
            squad_executable=self.squad,
            which=lambda _: sys.executable,
            runner=fake,
            mcp_sdk_available=True,
            mcp_sdk_version="2.2.0",
        )

    def test_setup_is_idempotent_for_every_host_and_preserves_unrelated_config(self):
        for template in load_integrations():
            with self.subTest(host=template.id):
                fake = FakeMCPHost(template, self.home)
                fake.seed_unrelated_config()
                manager = self.manager(fake)

                first = manager.setup(template)
                second = manager.setup(template)

                self.assertEqual(first["action"], "added")
                self.assertTrue(first["ready"])
                self.assertEqual(second["action"], "unchanged")
                self.assertTrue(second["ready"])
                self.assertEqual(fake.registration_calls, 1)
                self.assertEqual(len(second["sources"]), 1)
                self.assertEqual(second["loaded"]["args"], [
                    "mcp", "serve", "--surface", template.surface,
                ])
                self.assertNotIn("PRIVATE_TOKEN", json.dumps(second))
                self.assertIn("preserve-me", fake._config_path().read_text())

                fake._config_path().unlink()

    def test_duplicate_and_inherited_registrations_fail_closed_without_mutation(self):
        template = next(item for item in load_integrations() if item.id == "codex")
        expected = (
            str(self.squad.resolve()),
            ["mcp", "serve", "--surface", template.surface],
        )
        fake = FakeMCPHost(template, self.home)
        fake.loaded = expected
        fake._save_registration(*expected)
        project_config = self.project / ".codex/config.toml"
        project_config.parent.mkdir(parents=True)
        project_config.write_text(
            "[mcp_servers.devsquad]\n"
            f"command = {json.dumps(expected[0])}\n"
            f"args = {json.dumps(expected[1])}\n"
        )
        duplicate = self.manager(fake).setup(template)
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(duplicate["action"], "blocked_duplicate")
        self.assertEqual(fake.registration_calls, 0)
        fake._config_path().unlink()
        project_config.unlink()
        inherited = self.manager(fake).setup(template)
        self.assertEqual(inherited["status"], "inherited")
        self.assertEqual(inherited["action"], "blocked_inherited")
        self.assertEqual(fake.registration_calls, 0)

        fake._save_registration(*expected)
        fake.loaded = ("/inherited/override", ["mcp", "serve"])
        overlaid = self.manager(fake).setup(template)
        self.assertEqual(overlaid["status"], "duplicate")
        self.assertEqual(overlaid["action"], "blocked_duplicate")
        self.assertEqual(fake.registration_calls, 0)

    def test_doctor_data_redacts_drifted_arguments_and_malformed_config_content(self):
        template = next(
            item for item in load_integrations() if item.id == "claude-code"
        )
        fake = FakeMCPHost(template, self.home)
        fake.loaded = (str(self.squad.resolve()), ["--api-key", "super-secret"])
        fake._save_registration(*fake.loaded)
        drifted = self.manager(fake).inspect(template)
        encoded = json.dumps(drifted)
        self.assertEqual(drifted["status"], "drifted")
        self.assertIsNone(drifted["loaded"]["args"])
        self.assertIsNone(drifted["sources"][0]["args"])
        self.assertNotIn("super-secret", encoded)

        updated = self.manager(fake).setup(template)
        self.assertEqual(updated["action"], "updated")
        self.assertTrue(updated["ready"])
        self.assertEqual(updated["removal_exit_code"], 0)
        self.assertEqual(fake.registration_calls, 1)

        fake._config_path().write_text('{"private":"do-not-report"')
        fake.loaded = None
        malformed = self.manager(fake).inspect(template)
        self.assertEqual(malformed["status"], "invalid_config")
        self.assertNotIn("do-not-report", json.dumps(malformed))

    def test_setup_requires_the_exact_supported_optional_sdk(self):
        template = next(item for item in load_integrations() if item.id == "grok")
        fake = FakeMCPHost(template, self.home)
        missing = LocalIntegrationManager(
            project=self.project,
            home=self.home,
            squad_executable=self.squad,
            which=lambda _: sys.executable,
            runner=fake,
            mcp_sdk_available=False,
        ).setup(template)
        self.assertEqual(missing["action"], "blocked_missing_mcp_sdk")
        unsupported = LocalIntegrationManager(
            project=self.project,
            home=self.home,
            squad_executable=self.squad,
            which=lambda _: sys.executable,
            runner=fake,
            mcp_sdk_available=True,
            mcp_sdk_version="2.1.0",
        ).setup(template)
        self.assertEqual(unsupported["action"], "blocked_unsupported_mcp_sdk")
        self.assertEqual(fake.registration_calls, 0)

    def test_explicit_missing_launcher_does_not_silently_fall_back(self):
        template = next(item for item in load_integrations() if item.id == "codex")
        fake = FakeMCPHost(template, self.home)
        manager = LocalIntegrationManager(
            project=self.project,
            home=self.home,
            squad_executable=self.root / "missing-squad",
            which=lambda _: sys.executable,
            runner=fake,
            mcp_sdk_available=True,
            mcp_sdk_version="2.2.0",
        )
        result = manager.setup(template)
        self.assertEqual(result["status"], "unstable_launcher")
        self.assertEqual(result["action"], "blocked_unstable_launcher")
        self.assertIsNone(result["expected"]["command"])
        self.assertEqual(fake.registration_calls, 0)


class MCPDoctorReportTest(unittest.TestCase):
    def test_installed_app_drift_controls_readiness_but_unavailable_apps_do_not(self):
        manager = mock.Mock(
            mcp_sdk_available=True,
            mcp_sdk_supported=True,
            mcp_sdk_version="2.2.0",
            squad_executable=CORE / "bin/squad",
            launcher_error=None,
        )
        rows = [
            {"id": "codex", "installed": True, "ready": True},
            {"id": "claude-code", "installed": False, "ready": False},
        ]
        manager.inspect.side_effect = rows
        templates = (mock.Mock(id="codex"), mock.Mock(id="claude-code"))
        adapters = [{"adapter": "codex", "status": "supported"}]
        with (
            mock.patch.object(diagnostics, "_adapter_rows", return_value=adapters),
            mock.patch.object(diagnostics, "load_integrations", return_value=templates),
        ):
            report = diagnostics.build_doctor_report(project=ROOT, manager=manager)
        self.assertTrue(report["ready"])
        self.assertEqual(report["local_app_access"]["installed_count"], 1)
        self.assertEqual(report["local_app_access"]["configured_count"], 1)

        manager.inspect.side_effect = [
            {"id": "codex", "installed": True, "ready": False}, rows[1],
        ]
        with (
            mock.patch.object(diagnostics, "_adapter_rows", return_value=adapters),
            mock.patch.object(diagnostics, "load_integrations", return_value=templates),
        ):
            drifted = diagnostics.build_doctor_report(project=ROOT, manager=manager)
        self.assertFalse(drifted["ready"])
        self.assertFalse(drifted["local_app_access"]["ready"])


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

    def test_doctor_uses_the_shared_read_only_report(self):
        report = {"core_version": "0.1.0", "ready": True, "local_apps": []}
        with mock.patch.object(mcp_server, "build_doctor_report", return_value=report) as doctor:
            self.assert_success(self.bridge.doctor(), report)
        doctor.assert_called_once_with(project=Path.cwd().resolve())

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
                        "squad_doctor", "squad_start", "squad_status", "squad_events", "squad_result",
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

    def test_closing_a_real_stdio_client_does_not_cancel_the_detached_worker(self):
        from mcp import Client, StdioServerParameters

        with tempfile.TemporaryDirectory(prefix="devsquad-sdk-disconnect-") as directory:
            root = Path(directory)
            repo = root / "repo"
            runtime = root / "runtime"
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "Test"],
                check=True,
            )
            (repo / "src").mkdir()
            (repo / "tests").mkdir()
            (repo / "src/app.py").write_text("VALUE = 'fixture'\n")
            (repo / "tests/test_app.py").write_text("# fixture\n")
            profiles, policy = branch_review_routing_documents()
            (repo / "profiles.json").write_text(profiles)
            (repo / "policy.json").write_text(policy)
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-qm", "fixture"],
                check=True,
            )
            task = json.loads(
                (ROOT / "docs/plans/engineering-team/examples/branch-review.json").read_text()
            )
            task["project"] = {
                "repo_path": str(repo), "base_ref": "HEAD", "target_ref": "HEAD",
            }
            task["routing"] = {
                "profiles_file": "profiles.json", "policy_file": "policy.json",
            }
            service = Service(runtime)
            started = service.start(
                task, "stdio-client-disconnect", _internal_fake_delay=2,
            )

            async def observe_then_disconnect():
                parameters = StdioServerParameters(
                    command=sys.executable,
                    args=[
                        str(CORE / "bin/squad"), "mcp", "serve",
                        "--runtime-dir", str(runtime), "--surface", "codex-app",
                    ],
                    cwd=ROOT,
                )
                async with Client(parameters) as client:
                    status = await client.call_tool(
                        "squad_status", {"run_id": started["run_id"]},
                    )
                    self.assertFalse(status.is_error)
                    self.assertEqual(
                        status.structured_content["data"]["run_id"], started["run_id"],
                    )
                    self.assertIn(
                        status.structured_content["data"]["state"], {"queued", "running"},
                    )

            asyncio.run(observe_then_disconnect())
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                status = service.status(started["run_id"])
                if status["state"] == "succeeded":
                    break
                time.sleep(0.05)
            self.assertEqual(service.status(started["run_id"])["state"], "succeeded")


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
            integrations = subprocess.run(
                [
                    str(python), "-P", "-c",
                    "from devsquad.integrations import load_integrations; "
                    "print(','.join(sorted(item.id for item in load_integrations())))",
                ],
                check=True,
                text=True,
                capture_output=True,
                cwd=root,
                env=environment,
            )
            self.assertEqual(
                integrations.stdout.strip(),
                "antigravity,claude-code,codex,grok",
            )
            missing = subprocess.run(
                [str(squad), "mcp", "serve"], text=True, capture_output=True, env=environment,
            )
            self.assertEqual(missing.returncode, 69)
            self.assertEqual(missing.stdout, "")
            self.assertIn("devsquad-core[mcp]", missing.stderr)


if __name__ == "__main__":
    unittest.main()
