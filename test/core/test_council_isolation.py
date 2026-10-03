from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))
from devsquad.council_isolation import command, freeze_boundary, verify_read_boundary, verify_native_bootstrap, verify_native_network
from devsquad.contracts import CapabilityUnavailable
from devsquad.mcp_server import MCPBridge


class CouncilIsolationTest(unittest.TestCase):
    def test_bootstrap_uses_shared_cleanup_and_refuses_missing_identity_before_rpc(self):
        # No provider execution: test only the exact caller authority contract.
        from unittest.mock import Mock
        process = Mock()
        with tempfile.TemporaryDirectory(prefix="council-bootstrap-contract-") as temporary:
            with patch("devsquad.diagnostics._probe_output", return_value=(0, "codex-cli test")), \
                    patch("devsquad.council_isolation.command", return_value=["frozen-command"]), \
                    patch("devsquad.council_isolation.subprocess.Popen", return_value=process), \
                    patch("devsquad.probe_process.capture_probe_identity", return_value=None), \
                    patch("devsquad.probe_process.close_probe") as close, \
                    patch("devsquad.codex_protocol.JsonLinePeer") as peer:
                with self.assertRaisesRegex(CapabilityUnavailable, "ownership identity"):
                    verify_native_bootstrap({}, executable=Path("/bin/cat"), expected_version="codex-cli test", evidence=Path(temporary))
                peer.assert_not_called()
                close.assert_called_once_with(process, start_identity=None)

    def test_native_bootstrap_or_cached_catalog_is_not_network_attestation(self):
        with self.assertRaisesRegex(CapabilityUnavailable, "genuine non-generating HTTPS backend response"):
            verify_native_network({"probe_status": "passed", "model_list_count": 8})

    def test_unsupported_platform_has_no_unsandboxed_fallback(self):
        with patch("devsquad.council_isolation.platform.system", return_value="Linux"):
            with self.assertRaises(CapabilityUnavailable):
                freeze_boundary(executable=Path("/bin/cat"), evidence=ROOT)

    @unittest.skipUnless(platform.system() == "Darwin" and Path("/usr/bin/sandbox-exec").is_file(), "macOS Seatbelt required")
    def test_actual_same_user_process_denies_peer_ledger_logs_artifacts_and_symlink_children(self):
        with tempfile.TemporaryDirectory(prefix="council-isolation-public-") as temporary:
            root = Path(temporary).resolve()
            own = root / "proposer_a"
            own.mkdir()
            brief = own / "brief.txt"
            brief.write_text("Only own frozen evidence\n")
            forbidden = []
            for directory, name in (("proposer_b", "proposal.txt"), ("critic", "critique.txt"),
                                    ("runtime", "state.sqlite3"), ("private-logs", "supervisor.log"), ("artifacts", "raw-provenance.json")):
                parent = root / directory
                parent.mkdir()
                path = parent / name
                path.write_text("Private peer/coordinator data\n")
                forbidden.append(path)
            boundary = freeze_boundary(executable=Path("/bin/cat"), evidence=own)
            verify_read_boundary(boundary, own_file=brief, forbidden=tuple(forbidden))
            self.assertEqual(subprocess.run(command(boundary, ["/bin/cat", str(brief)]), capture_output=True).returncode, 0)
            for path in forbidden:
                result = subprocess.run(command(boundary, ["/bin/cat", str(path)]), capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(b"Private peer", result.stdout)
            link = own / "peer-link"
            link.symlink_to(forbidden[0])
            self.assertNotEqual(subprocess.run(command(boundary, ["/bin/cat", str(link)]), capture_output=True).returncode, 0)
            # The native jail is stricter (no shell exec). Permit shell only in
            # this diagnostic variant to prove the file rules inherit to children.
            policy = boundary["profile"] + '(allow process-exec (literal "/bin/sh"))\n(allow file-read* (literal "/bin/sh"))\n'
            child = subprocess.run(["/usr/bin/sandbox-exec", "-p", policy, "/bin/sh", "-c", 'exec /bin/cat "$1"', "probe", str(forbidden[0])], capture_output=True)
            self.assertNotEqual(child.returncode, 0)
            corrupt = {**boundary, "profile": boundary["profile"] + "(allow default)"}
            with self.assertRaises(CapabilityUnavailable):
                command(corrupt, ["/bin/cat", str(brief)])
            # The native dependency additions must preserve these exact denials.
            native = freeze_boundary(executable=Path("/bin/cat"), evidence=own, native_codex=True)
            verify_read_boundary(native, own_file=brief, forbidden=tuple(forbidden))
            self.assertNotEqual(subprocess.run(command(native, ["/bin/cat", str(link)]), capture_output=True).returncode, 0)
            environment = {"PATH": "/usr/bin:/bin"}  # no Council/worker marker
            self.assertNotEqual(subprocess.run(command(native, ["/bin/cat", str(forbidden[2])]),
                capture_output=True, env=environment).returncode, 0)
            # Alternate MCP servers cannot escape by spawning a new interpreter:
            # it is not a frozen execution dependency, even under the same uid.
            alternate = subprocess.run(["/usr/bin/sandbox-exec", "-p", native["profile"], sys.executable,
                "-c", "import pathlib; pathlib.Path(__import__('sys').argv[1]).read_bytes()", str(forbidden[2])],
                capture_output=True, env=environment)
            self.assertNotEqual(alternate.returncode, 0)

    def test_worker_mcp_saved_reads_cannot_bypass_role_directory(self):
        with tempfile.TemporaryDirectory(prefix="council-mcp-denial-") as directory:
            bridge = MCPBridge(Path(directory))
            with patch.dict(os.environ, {"DEVSQUAD_WORKER": "1", "DEVSQUAD_DELEGATION_DEPTH": "1", "DEVSQUAD_COUNCIL_ROLE": "proposer_a"}):
                for operation in (lambda: bridge.status("peer-run"), lambda: bridge.events("peer-run"), lambda: bridge.result("peer-run")):
                    result = operation()
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["error"]["code"], "POLICY_DENIED")


if __name__ == "__main__":
    unittest.main()
