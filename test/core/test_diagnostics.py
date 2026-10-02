import contextlib
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad import cli, diagnostics, mcp_server, probe_process
from devsquad.contracts import ContractError
from devsquad.supervisor import _live_group_exists, process_start_identity


class DoctorReadinessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devsquad-doctor-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.project = self.root / "project"
        self.project.mkdir()
        self.core = self.root / "core"
        self.log = self.root / "probes.jsonl"
        self.manager = mock.Mock(
            mcp_sdk_available=True, mcp_sdk_supported=True,
            mcp_sdk_version="2.2.0", squad_executable=ROOT / "plugin/core/bin/squad",
            launcher_error=None,
        )
        self.manager.inspect.side_effect = lambda template: {
            "id": template.id, "installed": True, "ready": True, "status": "matching",
        }
        self.core_patch = mock.patch.object(diagnostics, "CORE_ROOT", self.core)
        self.core_patch.start()
        self.addCleanup(self.core_patch.stop)

    def install(self, name, *, version=None, response=None, returncode=0, raw=None, delay=0):
        versions = {
            "codex": "codex-cli 0.159.2", "claude": "2.1.220 (Claude Code)",
            "grok": "1.0.46", "antigravity": "1.2.14",
        }
        version = version or versions[name]
        binary = self.root / (name + "-fixture")
        configuration = {
            "name": name, "version": version, "response": response,
            "returncode": returncode, "raw": raw, "log": str(self.log), "delay": delay,
        }
        binary.write_text("#!" + sys.executable + "\n" + """
import json, os, sys, time
configuration = CONFIGURATION
def record(value):
    with open(configuration['log'], 'a') as output:
        output.write(json.dumps(value) + '\\n')
record({'argv': sys.argv[1:], 'environment': dict(os.environ)})
if sys.argv[1:] == ['--version']:
    print(configuration['version'])
elif sys.argv[1:] == ['auth', 'status', '--json']:
    time.sleep(configuration['delay'])
    print(configuration['raw'] if configuration['raw'] is not None else json.dumps(configuration['response']))
    print('private-stderr-token', file=sys.stderr)
    sys.exit(configuration['returncode'])
elif sys.argv[1:] == ['app-server', '--listen', 'stdio://']:
    for line in sys.stdin:
        message = json.loads(line)
        record({'request': message})
        if message['method'] == 'initialize':
            print(json.dumps({'id': message['id'], 'result': {}}), flush=True)
        elif message['method'] == 'account/read':
            time.sleep(configuration['delay'])
            if configuration['raw'] is not None:
                print(configuration['raw'], flush=True)
            else:
                print(json.dumps({'id': message['id'], 'result': configuration['response']}), flush=True)
else:
    sys.exit(99)
""".replace("CONFIGURATION", repr(configuration)))
        binary.chmod(0o755)
        directory = self.core / "adapters" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "adapter.json").write_text(json.dumps({
            "schema_version": 1, "name": name,
            "transport": "native_protocol" if name == "codex" else "cli_exec",
            "binary_candidates": [str(binary)], "capabilities": {},
            "verified_harness_versions": (
                [versions[name]] if name in {"codex", "claude"} else []
            ),
        }))
        return binary

    def report(self):
        return diagnostics.build_doctor_report(
            project=self.project, home=self.home, manager=self.manager,
        )

    def row(self, report, name):
        return next(row for row in report["adapters"] if row["adapter"] == name)

    def probes(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def codex(self, response=None):
        return self.install("codex", response=response or {
            "account": {"type": "chatgpt", "email": "private@example.test",
                        "accountId": "private-account", "accessToken": "private-token"},
            "requiresOpenaiAuth": True,
        })

    def test_logged_out_claude_is_visible_and_delivery_is_unavailable(self):
        self.codex()
        self.install("claude", response={"loggedIn": False, "authMethod": "none"}, returncode=1)
        report = self.report()
        claude = self.row(report, "claude")
        self.assertTrue(claude["installed"])
        self.assertTrue(claude["supported"])
        self.assertIs(claude["authenticated"], False)
        self.assertFalse(claude["ready"])
        self.assertEqual(claude["authentication"]["next_action"], "claude auth login")
        self.assertTrue(report["supported_workflows"]["branch-review"]["ready"])
        self.assertFalse(report["supported_workflows"]["issue-delivery"]["ready"])
        self.assertTrue(report["ready"])

    def test_unverified_cli_never_passes_adapter_readiness_or_auth_probe(self):
        self.install("codex", version="codex-cli 99.0.0")
        report = self.report()
        row = self.row(report, "codex")
        self.assertEqual(row["status"], "unverified")
        self.assertTrue(row["installed"])
        self.assertFalse(row["supported"])
        self.assertIsNone(row["authenticated"])
        self.assertFalse(report["adapter_ready"])
        self.assertFalse(report["ready"])
        self.assertEqual([row["argv"] for row in self.probes()], [["--version"]])

    def test_unsupported_authentication_is_unknown_even_with_four_registrations(self):
        self.install("grok")
        self.install("antigravity")
        report = self.report()
        self.assertEqual(len(report["local_apps"]), 4)
        self.assertTrue(report["local_app_access"]["ready"])
        self.assertTrue(all(row["registered"] for row in report["local_apps"]))
        self.assertTrue(all(row["operation_verified"] is None for row in report["local_apps"]))
        self.assertFalse(report["ready"])
        for row in report["adapters"]:
            self.assertIsNone(row["authenticated"])
            self.assertIsNone(row["operation_verified"])
            self.assertFalse(row["supported"])
        self.assertTrue(all(probe["argv"] == ["--version"] for probe in self.probes()))

    def test_authentication_and_registration_do_not_invent_operation_proof(self):
        self.codex()
        self.install("claude", response={
            "loggedIn": True, "authMethod": "claude.ai", "email": "private@example.test",
            "organizationId": "private-org", "apiKey": "private-key",
        })
        report = self.report()
        self.assertTrue(report["ready"])
        self.assertTrue(report["supported_workflows"]["issue-delivery"]["ready"])
        self.assertFalse(report["supported_workflows"]["council"]["ready"])
        for row in report["adapters"]:
            self.assertIs(row["authenticated"], True)
            self.assertIsNone(row["operation_verified"])
        output = json.dumps(report)
        for secret in ("private@example.test", "private-account", "private-token",
                       "private-org", "private-key", "private-stderr-token"):
            self.assertNotIn(secret, output)

    def test_claude_malformed_banner_missing_or_non_boolean_auth_stays_unknown(self):
        cases = [
            "Welcome private-token\n" + json.dumps({"loggedIn": True, "authMethod": "claude.ai"}),
            "not-json private-token", "[]", "{}",
            json.dumps({"loggedIn": "true", "authMethod": "claude.ai"}),
            json.dumps({"loggedIn": 1, "authMethod": "claude.ai"}),
            json.dumps({"loggedIn": True, "authMethod": "private-token"}),
            json.dumps({"loggedIn": True, "authMethod": None}),
            json.dumps({"loggedIn": True, "authMethod": []}),
            json.dumps({"loggedIn": True, "authMethod": "none"}),
            '{"loggedIn":false,"loggedIn":true,"authMethod":"claude.ai"}',
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                self.install("claude", raw=raw)
                report = self.report()
                self.assertIsNone(self.row(report, "claude")["authenticated"])
                self.assertFalse(report["ready"])
                self.assertNotIn("private-token", json.dumps(report))

    def test_api_key_auth_does_not_make_subscription_workflows_available(self):
        self.install("claude", response={"loggedIn": True, "authMethod": "api_key"})
        self.codex({"account": {"type": "apiKey", "apiKey": "private-key"},
                    "requiresOpenaiAuth": True})
        report = self.report()
        self.assertFalse(report["ready"])
        self.assertFalse(report["adapter_ready"])
        for row in report["adapters"]:
            self.assertTrue(row["authenticated"])
            self.assertFalse(row["authentication"]["subscription_supported"])
            self.assertFalse(row["ready"])
        self.assertNotIn("private-key", json.dumps(report))

    def test_codex_null_account_and_unknown_provider_state_are_not_ready(self):
        for response, authenticated in (
            ({"account": None, "requiresOpenaiAuth": True}, False),
            ({"account": None, "requiresOpenaiAuth": False}, None),
            ({"account": {"type": "unknown", "token": "private-token"}, "requiresOpenaiAuth": True}, None),
            ({"account": {"type": []}, "requiresOpenaiAuth": True}, None),
            ({"account": {"type": "chatgpt"}, "requiresOpenaiAuth": "true"}, None),
            ({"requiresOpenaiAuth": True}, None),
        ):
            with self.subTest(response=response):
                self.install("codex", response=response)
                report = self.report()
                self.assertIs(self.row(report, "codex")["authenticated"], authenticated)
                self.assertFalse(report["ready"])
                self.assertNotIn("private-token", json.dumps(report))

    def test_codex_protocol_is_nongenerating_and_environment_drops_credentials(self):
        self.codex()
        with mock.patch.dict(os.environ, {
            "USER": "fixture-user", "OPENAI_API_KEY": "private-key",
            "ANTHROPIC_API_KEY": "private-key", "CODEX_HOME": "/private/override",
            "CLAUDE_CODE_OAUTH_TOKEN": "private-token", "PRIVATE_PROVIDER_OVERRIDE": "secret",
        }):
            report = self.report()
        self.assertTrue(report["ready"])
        probes = self.probes()
        requests = [probe["request"] for probe in probes if "request" in probe]
        self.assertEqual([request["method"] for request in requests],
                         ["initialize", "initialized", "account/read"])
        self.assertEqual(requests[-1]["params"], {"refreshToken": False})
        for probe in (probe for probe in probes if "environment" in probe):
            environment = probe["environment"]
            self.assertEqual(environment["HOME"], str(self.home))
            self.assertEqual(environment["USER"], "fixture-user")
            self.assertIn("PATH", environment)
            # Python and macOS may add these locale values at process startup.
            self.assertLessEqual(set(environment), {
                "HOME", "USER", "PATH", "LC_CTYPE", "__CF_USER_TEXT_ENCODING",
            })

    def test_oversized_output_and_version_banners_are_redacted_before_parse(self):
        for name in ("claude", "codex"):
            with self.subTest(name=name):
                self.install(name, raw="private-token" + ("x" * diagnostics.MAX_PROBE_BYTES))
                report = self.report()
                self.assertIsNone(self.row(report, name)["authenticated"])
                self.assertNotIn("private-token", json.dumps(report))
        for name in ("claude", "codex"):
            with self.subTest(name=name, depth="excessive"):
                self.install(name, raw="[" * 1500 + "0" + "]" * 1500)
                self.assertIsNone(self.row(self.report(), name)["authenticated"])
        self.install("claude", version="login failed private-token")
        report = self.report()
        self.assertIsNone(self.row(report, "claude")["version"])
        self.assertNotIn("private-token", json.dumps(report))

    def test_codex_malformed_banner_error_or_uncorrelated_reply_stays_unknown(self):
        for raw in (
            "Welcome private-token", "[]", "{}",
            json.dumps({"id": 2, "result": None}),
            json.dumps({"id": 99, "result": {"account": {"type": "chatgpt"}, "requiresOpenaiAuth": True}}),
            json.dumps({"id": 2, "error": {"message": "private-token"}}),
        ):
            with self.subTest(raw=raw):
                self.install("codex", raw=raw)
                report = self.report()
                self.assertIsNone(self.row(report, "codex")["authenticated"])
                self.assertFalse(report["ready"])
                self.assertNotIn("private-token", json.dumps(report))

    def test_auth_checks_time_out_and_reap_the_owned_process(self):
        start_process = diagnostics.subprocess.Popen
        for name in ("claude", "codex"):
            with self.subTest(name=name):
                self.install(name, delay=60)
                processes = []
                def capture_process(*args, **kwargs):
                    process = start_process(*args, **kwargs)
                    processes.append(process)
                    return process
                with (
                    mock.patch.object(diagnostics, "PROBE_TIMEOUT_SECONDS", 0.5),
                    mock.patch.object(diagnostics, "AUTH_TIMEOUT_SECONDS", 0.5),
                    mock.patch.object(diagnostics.subprocess, "Popen", side_effect=capture_process) as spawned,
                ):
                    report = self.report()
                self.assertIsNone(self.row(report, name)["authenticated"])
                self.assertFalse(self.row(report, name)["ready"])
                for call in spawned.call_args_list:
                    self.assertEqual(set(call.kwargs["env"]), {"HOME", "USER", "PATH"})
                for process in processes:
                    self.assertIsNotNone(process.poll())
                    self.assertTrue(process.stdout.closed)

    def test_exited_probe_parent_cannot_leave_term_ignoring_descendant_alive(self):
        child_record = self.root / "probe-child.json"
        code = """
import json, os, signal, sys, time
from pathlib import Path
sys.path.insert(0, SOURCE)
from devsquad.supervisor import process_start_identity
read_ready, write_ready = os.pipe()
if os.fork() == 0:
    os.close(read_ready)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path(RECORD).write_text(json.dumps({'pid': os.getpid(), 'start': process_start_identity(os.getpid())}))
    os.write(write_ready, b'r')
    os.close(write_ready)
    time.sleep(60)
    os._exit(0)
os.close(write_ready)
os.read(read_ready, 1)
os.close(read_ready)
print('codex-cli 0.159.2', flush=True)
os._exit(0)
""".replace("SOURCE", repr(str(ROOT / "plugin/core/src"))).replace("RECORD", repr(str(child_record)))
        processes = []
        start_process = diagnostics.subprocess.Popen
        def capture_process(*args, **kwargs):
            process = start_process(*args, **kwargs)
            processes.append(process)
            return process
        started = time.monotonic()
        try:
            with (
                mock.patch.object(diagnostics, "PROBE_TIMEOUT_SECONDS", 0.5),
                mock.patch.object(diagnostics.subprocess, "Popen", side_effect=capture_process),
                self.assertRaises(TimeoutError),
            ):
                diagnostics._probe_output(
                    [sys.executable, "-c", code], project=self.project,
                    environment=diagnostics._environment(self.home),
                )
            self.assertLess(time.monotonic() - started, 3)
            self.assertTrue(child_record.exists(), "descendant readiness barrier was not reached")
            process = processes[0]
            self.assertEqual(process.returncode, 0)
            self.assertFalse(_live_group_exists(process.pid), "owned descendant survived probe cleanup")
            self.assertTrue(process.stdout.closed)
        finally:
            if processes:
                process = processes[0]
                child = json.loads(child_record.read_text()) if child_record.exists() else None
                if child and process_start_identity(child["pid"]) == child["start"]:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait(timeout=2)
                deadline = time.monotonic() + 2
                while _live_group_exists(process.pid) and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertFalse(_live_group_exists(process.pid), "controlled fixture cleanup failed")

    def test_reused_probe_identity_is_not_safe_to_signal(self):
        process = mock.Mock(pid=987654, returncode=0, stderr=None)
        with (
            mock.patch.object(probe_process, "_probe_group_exists", return_value=True),
            mock.patch.object(probe_process, "process_start_identity", return_value="new-start"),
            mock.patch.object(probe_process.os, "killpg") as signal_group,
            self.assertRaisesRegex(ContractError, "identity changed"),
        ):
            probe_process.close_probe(process, start_identity="original-start")
        signal_group.assert_not_called()
        process.stdout.close.assert_called_once()

    def test_missing_capture_and_observation_never_authorize_group_signals(self):
        process = mock.Mock(pid=987654, returncode=None, stderr=None)
        with (
            mock.patch.object(probe_process, "PROBE_CLEANUP_SECONDS", 0.05),
            mock.patch.object(probe_process, "PROBE_TERM_GRACE_SECONDS", 0.01),
            mock.patch.object(probe_process, "_probe_group_exists", return_value=True),
            mock.patch.object(probe_process, "process_start_identity", return_value=None),
            mock.patch.object(probe_process.os, "killpg") as signal_group,
            self.assertRaises(ContractError),
        ):
            probe_process.close_probe(process, start_identity=None)
        signal_group.assert_not_called()
        process.wait.assert_not_called()
        process.stdout.close.assert_called_once()

    def test_known_capture_missing_observation_has_no_unproven_group_authority(self):
        for returncode in (None, 0):
            with self.subTest(returncode=returncode):
                process = mock.Mock(pid=987654, returncode=returncode, stderr=None)
                with (
                    mock.patch.object(probe_process, "_probe_group_exists", return_value=True),
                    mock.patch.object(probe_process, "process_start_identity", return_value=None),
                    mock.patch.object(probe_process.os, "killpg") as signal_group,
                    mock.patch.object(probe_process.os, "kill") as signal_child,
                    self.assertRaises(ContractError),
                ):
                    probe_process.close_probe(process, start_identity="captured-start")
                signal_group.assert_not_called()
                signal_child.assert_not_called()
                process.wait.assert_not_called()
                process.stdout.close.assert_called_once()

    def test_probe_does_not_read_output_without_captured_identity(self):
        process = mock.Mock(pid=987654, returncode=None, stderr=None)
        process.wait.return_value = 0
        with (
            mock.patch.object(diagnostics.subprocess, "Popen", return_value=process),
            mock.patch.object(diagnostics, "capture_probe_identity", return_value=None),
            mock.patch.object(diagnostics, "_close_probe") as close,
            mock.patch.object(diagnostics.selectors, "DefaultSelector") as selector,
            mock.patch.object(diagnostics.os, "read", return_value=b"") as read,
            self.assertRaisesRegex(ContractError, "ownership is unavailable"),
        ):
            diagnostics._probe_output(["/fixture/claude", "auth", "status", "--json"],
                                      project=self.project, environment=diagnostics._environment(self.home))
        selector.assert_not_called()
        read.assert_not_called()
        process.wait.assert_not_called()
        close.assert_called_once_with(process, start_identity=None)

    def test_codex_auth_does_not_construct_or_send_protocol_without_identity(self):
        process = mock.Mock(pid=987654, returncode=None, stderr=None)
        with (
            mock.patch.object(diagnostics.subprocess, "Popen", return_value=process),
            mock.patch.object(diagnostics, "capture_probe_identity", return_value=None),
            mock.patch.object(diagnostics, "_close_probe") as close,
            mock.patch.object(diagnostics, "JsonLinePeer") as peer,
            mock.patch.object(diagnostics, "receive_response", side_effect=[
                {"result": {}}, {"result": {"account": {"type": "chatgpt"}, "requiresOpenaiAuth": True}},
            ]) as receive,
        ):
            authentication = diagnostics._codex_auth("/fixture/codex", project=self.project,
                                                    environment=diagnostics._environment(self.home))
        self.assertIsNone(authentication["authenticated"])
        self.assertFalse(authentication["subscription_supported"])
        peer.assert_not_called()
        receive.assert_not_called()
        close.assert_called_once_with(process, start_identity=None)

    def test_unidentified_live_child_is_reaped_without_group_signals(self):
        ready = self.root / "unidentified-ready"
        code = (
            "import signal,time\nfrom pathlib import Path\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            f"Path({str(ready)!r}).touch()\n"
            "time.sleep(60)\n"
        )
        process = diagnostics.subprocess.Popen(
            [sys.executable, "-c", code], stdin=diagnostics.subprocess.DEVNULL,
            stdout=diagnostics.subprocess.PIPE, stderr=diagnostics.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 2
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists())
            with (
                mock.patch.object(probe_process, "process_start_identity", return_value=None),
                mock.patch.object(probe_process.os, "killpg", wraps=os.killpg) as signal_group,
            ):
                probe_process.close_probe(process, start_identity=None)
            signal_group.assert_not_called()
            self.assertIsNotNone(process.returncode)
            self.assertFalse(_live_group_exists(process.pid))
            self.assertTrue(process.stdout.closed)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
            process.stdout.close()

    def test_missing_kernel_child_authority_denies_all_signals(self):
        process = diagnostics.subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=diagnostics.subprocess.DEVNULL, stdout=diagnostics.subprocess.PIPE,
            stderr=diagnostics.subprocess.DEVNULL, start_new_session=True,
        )
        try:
            with (
                mock.patch.object(probe_process.os, "waitpid", side_effect=ChildProcessError),
                mock.patch.object(probe_process.os, "killpg") as signal_group,
                mock.patch.object(probe_process.os, "kill") as signal_child,
                self.assertRaisesRegex(ContractError, "ownership is unavailable"),
            ):
                probe_process.close_probe(process, start_identity=None)
            signal_group.assert_not_called()
            signal_child.assert_not_called()
            self.assertIsNone(process.returncode)
            self.assertTrue(process.stdout.closed)
        finally:
            process.kill()
            process.wait(timeout=2)
            process.stdout.close()
        self.assertFalse(_live_group_exists(process.pid))

    def test_known_capture_without_waitid_falls_back_to_verified_child_only(self):
        process = diagnostics.subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=diagnostics.subprocess.DEVNULL, stdout=diagnostics.subprocess.PIPE,
            stderr=diagnostics.subprocess.DEVNULL, start_new_session=True,
        )
        captured = process_start_identity(process.pid)
        self.assertIsNotNone(captured)
        try:
            with (
                mock.patch.object(probe_process.os, "waitid", None, create=True),
                mock.patch.object(probe_process, "process_start_identity", return_value=None),
                mock.patch.object(probe_process.os, "killpg", wraps=os.killpg) as signal_group,
            ):
                probe_process.close_probe(process, start_identity=captured)
            signal_group.assert_not_called()
            self.assertIsNotNone(process.returncode)
            self.assertFalse(_live_group_exists(process.pid))
            self.assertTrue(process.stdout.closed)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
            process.stdout.close()

    def test_ps_zombie_anchor_rejects_reparented_wrong_group_and_malformed_rows(self):
        process = diagnostics.subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=diagnostics.subprocess.DEVNULL, stdout=diagnostics.subprocess.PIPE,
            stderr=diagnostics.subprocess.DEVNULL, start_new_session=True,
        )
        pid, parent = process.pid, os.getpid()
        cases = [
            (0, f"{pid} {parent + 1} {pid} Z\n"),
            (0, f"{pid} {parent} {pid + 1} Z\n"),
            (0, f"{pid + 1} {parent} {pid} Z\n"),
            (0, f"{pid} {parent} {pid} S\n"),
            (0, f"{pid} {parent} {pid}\n"),
            (0, f"{pid} {parent} {pid} Z\n{pid} {parent} {pid} Z\n"),
            (0, "private-banner-token\n"),
            (0, "x" * 1025),
            (1, f"{pid} {parent} {pid} Z\n"),
        ]
        try:
            for code, output in cases:
                with self.subTest(code=code, output=output[:100]):
                    with (
                        mock.patch.object(probe_process.os, "waitid", None, create=True),
                        mock.patch.object(probe_process.subprocess, "run", return_value=mock.Mock(returncode=code, stdout=output)) as inventory,
                        mock.patch.object(probe_process.os, "killpg") as signal_group,
                    ):
                        self.assertFalse(probe_process._retained_child_anchor(process, deadline=time.monotonic() + 1))
                    signal_group.assert_not_called()
                    self.assertLessEqual(inventory.call_args.kwargs["timeout"], 0.5)
                    self.assertEqual(set(inventory.call_args.kwargs["env"]), {"HOME", "USER", "PATH"})
            for error in (OSError, diagnostics.subprocess.TimeoutExpired("/bin/ps", 0.5)):
                with self.subTest(error=type(error).__name__):
                    with (
                        mock.patch.object(probe_process.os, "waitid", None, create=True),
                        mock.patch.object(probe_process.subprocess, "run", side_effect=error),
                    ):
                        self.assertFalse(probe_process._retained_child_anchor(process, deadline=time.monotonic() + 1))
        finally:
            process.kill()
            process.wait(timeout=2)
            process.stdout.close()

    def test_cleanup_reap_lock_contention_cannot_exceed_deadline_or_signal(self):
        process = diagnostics.subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=diagnostics.subprocess.DEVNULL, stdout=diagnostics.subprocess.PIPE,
            stderr=diagnostics.subprocess.DEVNULL, start_new_session=True,
        )
        process._waitpid_lock.acquire()
        started = time.monotonic()
        try:
            with (
                mock.patch.object(probe_process, "PROBE_CLEANUP_SECONDS", 0.05),
                mock.patch.object(probe_process.os, "killpg") as signal_group,
                mock.patch.object(probe_process.os, "kill") as signal_child,
                self.assertRaisesRegex(ContractError, "deadline expired"),
            ):
                probe_process.close_probe(process, start_identity=None)
            signal_group.assert_not_called()
            signal_child.assert_not_called()
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertTrue(process.stdout.closed)
        finally:
            process._waitpid_lock.release()
            process.kill()
            process.wait(timeout=2)
            process.stdout.close()

    def test_probe_eof_retains_exited_parent_until_owned_descendant_cleanup(self):
        record = self.root / "eof-child.json"
        code = (
            "import json,os,signal,time\nfrom pathlib import Path\n"
            f"import sys\nsys.path.insert(0, {str(ROOT / 'plugin/core/src')!r})\n"
            "from devsquad.supervisor import process_start_identity\n"
            "read_ready,write_ready=os.pipe()\n"
            "if os.fork()==0:\n"
            "    os.close(read_ready)\n"
            "    os.dup2(os.open(os.devnull,os.O_WRONLY),1)\n"
            "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            f"    Path({str(record)!r}).write_text(json.dumps({{'pid':os.getpid(),'start':process_start_identity(os.getpid())}}))\n"
            "    os.write(write_ready,b'r')\n    os.close(write_ready)\n    time.sleep(60)\n    os._exit(0)\n"
            "os.close(write_ready)\nos.read(read_ready,1)\nos.close(read_ready)\n"
            "print('codex-cli 0.159.2',flush=True)\nos._exit(0)\n"
        )
        captured_processes = []
        real_spawn, real_close = diagnostics.subprocess.Popen, diagnostics._close_probe
        def spawn(*args, **kwargs):
            process = real_spawn(*args, **kwargs)
            captured_processes.append(process)
            return process
        def close(process, **kwargs):
            self.assertIsNone(process.returncode, "probe reaped its ownership anchor before cleanup")
            real_close(process, **kwargs)
        try:
            with (
                mock.patch.object(diagnostics.subprocess, "Popen", side_effect=spawn),
                mock.patch.object(diagnostics, "_close_probe", side_effect=close),
            ):
                code, output = diagnostics._probe_output([sys.executable, "-c", code],
                    project=self.project, environment=diagnostics._environment(self.home))
            self.assertEqual((code, output.strip()), (0, "codex-cli 0.159.2"))
            self.assertFalse(_live_group_exists(captured_processes[0].pid))
        finally:
            child = json.loads(record.read_text()) if record.exists() else None
            if child and process_start_identity(child["pid"]) == child["start"]:
                try:
                    os.kill(child["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if captured_processes:
                process = captured_processes[0]
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=2)
                process.stdout.close()

    def test_unidentified_descendant_cleanup_is_unconfirmed_without_group_signals(self):
        record = self.root / "unidentified-child.json"
        code = (
            "import json,os,signal,time\nfrom pathlib import Path\n"
            f"import sys\nsys.path.insert(0, {str(ROOT / 'plugin/core/src')!r})\n"
            "from devsquad.supervisor import process_start_identity\n"
            "read_ready,write_ready=os.pipe()\n"
            "if os.fork()==0:\n"
            "    os.close(read_ready)\n"
            "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            f"    Path({str(record)!r}).write_text(json.dumps({{'pid':os.getpid(),'start':process_start_identity(os.getpid())}}))\n"
            "    os.write(write_ready,b'r')\n    os.close(write_ready)\n    time.sleep(60)\n    os._exit(0)\n"
            "os.close(write_ready)\nos.read(read_ready,1)\nos.close(read_ready)\ntime.sleep(60)\n"
        )
        process = diagnostics.subprocess.Popen(
            [sys.executable, "-c", code], stdin=diagnostics.subprocess.DEVNULL,
            stdout=diagnostics.subprocess.PIPE, stderr=diagnostics.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 2
            while not record.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(record.exists())
            with (
                mock.patch.object(probe_process, "process_start_identity", return_value=None),
                mock.patch.object(probe_process.os, "killpg", wraps=os.killpg) as signal_group,
                self.assertRaisesRegex(ContractError, "cleanup unconfirmed"),
            ):
                probe_process.close_probe(process, start_identity=None)
            signal_group.assert_not_called()
            self.assertIsNotNone(process.returncode)
            self.assertTrue(_live_group_exists(process.pid))
            self.assertTrue(process.stdout.closed)
        finally:
            child = json.loads(record.read_text()) if record.exists() else None
            if child and process_start_identity(child["pid"]) == child["start"]:
                try:
                    os.kill(child["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
            process.stdout.close()
            deadline = time.monotonic() + 2
            while _live_group_exists(process.pid) and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse(_live_group_exists(process.pid))

    def test_doctor_unidentified_auth_is_unknown_and_every_owned_parent_is_absent(self):
        self.codex()
        self.install("claude", response={"loggedIn": True, "authMethod": "claude.ai"})
        processes = []
        real_spawn, real_capture = diagnostics.subprocess.Popen, diagnostics.capture_probe_identity
        def spawn(*args, **kwargs):
            process = real_spawn(*args, **kwargs)
            processes.append(process)
            return process
        def capture(process):
            return real_capture(process) if process.args[-1] == "--version" else None
        with (
            mock.patch.object(diagnostics.subprocess, "Popen", side_effect=spawn),
            mock.patch.object(diagnostics, "capture_probe_identity", side_effect=capture),
            mock.patch.object(diagnostics, "JsonLinePeer") as peer,
        ):
            report = self.report()
        peer.assert_not_called()
        self.assertFalse(report["ready"])
        self.assertFalse(report["adapter_ready"])
        for name in ("codex", "claude"):
            row = self.row(report, name)
            self.assertTrue(row["supported"])
            self.assertIsNone(row["authenticated"])
            self.assertFalse(row["ready"])
        for process in processes:
            self.assertIsNotNone(process.returncode)
            self.assertFalse(_live_group_exists(process.pid))
            if process.stdout is not None:
                self.assertTrue(process.stdout.closed)

    def test_missing_group_is_confirmed_before_reap_without_signaling_stale_pid(self):
        process = mock.Mock(pid=987654, returncode=0, stderr=None)
        with (
            mock.patch.object(probe_process, "_probe_group_exists", return_value=False),
            mock.patch.object(probe_process, "process_start_identity", return_value="new-start"),
            mock.patch.object(probe_process.os, "killpg") as signal_group,
        ):
            probe_process.close_probe(process, start_identity="original-start")
        signal_group.assert_not_called()
        process.wait.assert_called_once()

    def test_identity_change_after_term_blocks_kill_escalation(self):
        process = mock.Mock(pid=987654, returncode=None, stderr=None)
        identity = {"value": "original-start"}
        def record_signal(pgid, value):
            identity["value"] = "reused-start"
        with (
            mock.patch.object(probe_process, "_probe_group_exists", return_value=True),
            mock.patch.object(probe_process, "process_start_identity", side_effect=lambda pid: identity["value"]),
            mock.patch.object(probe_process.os, "killpg", side_effect=record_signal) as signal_group,
            self.assertRaisesRegex(ContractError, "identity changed"),
        ):
            probe_process.close_probe(process, start_identity="original-start")
        signal_group.assert_called_once_with(process.pid, signal.SIGTERM)

    def test_missing_cli_never_claims_authentication_or_support(self):
        self.codex().unlink()
        report = self.report()
        row = self.row(report, "codex")
        self.assertEqual(row["status"], "unavailable")
        self.assertFalse(row["installed"])
        self.assertFalse(row["supported"])
        self.assertIsNone(row["authenticated"])
        self.assertIsNone(row["operation_verified"])
        self.assertFalse(report["ready"])
        self.assertFalse(self.log.exists())

    def test_cli_and_mcp_doctor_keep_the_same_machine_envelope(self):
        self.codex()
        report = self.report()
        stdout = io.StringIO()
        with mock.patch.object(cli, "build_doctor_report", return_value=report):
            with contextlib.redirect_stdout(stdout):
                code = cli.main(["doctor", "--json"])
        self.assertEqual(code, 0)
        envelope = json.loads(stdout.getvalue())
        self.assertEqual(envelope["schema_version"], 1)
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["data"], report)
        bridge = mcp_server.MCPBridge(self.root / "runtime", mock.Mock(), environment={})
        with mock.patch.object(mcp_server, "build_doctor_report", return_value=report):
            self.assertEqual(bridge.doctor(), envelope)

    def test_read_only_report_keeps_config_and_ledger_unchanged(self):
        self.codex()
        configuration = self.home / ".claude.json"
        configuration.write_text('{"secret":"private-token","keep":true}')
        database = self.project / "state.sqlite"
        database.write_bytes(b"unchanged-ledger")
        before = {str(path): path.read_bytes() for directory in (self.home, self.project)
                  for path in directory.rglob("*") if path.is_file()}
        self.report()
        after = {str(path): path.read_bytes() for directory in (self.home, self.project)
                 for path in directory.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.manager.setup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
