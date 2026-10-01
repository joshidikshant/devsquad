from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

CORE = Path(__file__).resolve().parents[2] / "plugin" / "core"
sys.path.insert(0, str(CORE / "src"))

from devsquad.adapters import AdapterManifest, classify_cli, prepare_cli
from devsquad.contracts import ProfileUnsupported


class ClaudeAdapterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.binary = Path(self.temp.name) / "claude"
        self.binary.write_text("#!/bin/sh\nexit 0\n")
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        self.manifest = AdapterManifest.load(
            CORE / "adapters" / "claude" / "adapter.json"
        )

    def prepare(
        self,
        *,
        permission: str = "read_only",
        model: str | None = None,
        effort: str | None = None,
        version: str | None = None,
    ):
        manifest = self.manifest
        if effort is not None:
            manifest = manifest.with_model_efforts({model: (effort,)})
        with patch.dict(os.environ, {"PATH": str(self.binary.parent)}):
            return prepare_cli(
                manifest,
                prompt="Fix src/My Parser.ts without delegating.",
                cwd=self.temp.name,
                model=model,
                effort=effort,
                permission=permission,
                timeout_seconds=17,
                harness_version_value=version,
            )

    def test_read_only_argv_is_structured_and_has_no_bypass_or_delegation(self):
        spec = self.prepare()
        self.assertEqual(spec.adapter, "claude")
        self.assertEqual(spec.transport, "cli_exec")
        self.assertEqual(spec.requested.permissions, "read_only")
        self.assertEqual(spec.requested.tools, ("Read", "Glob", "Grep"))
        self.assertIn("plan", spec.argv)
        self.assertIn("Read,Glob,Grep", spec.argv)
        self.assertIn('{"mcpServers":{}}', spec.argv)
        self.assertIn("Fix src/My Parser.ts without delegating.", spec.argv)
        self.assertEqual(spec.argv[-2:], ("--", "Fix src/My Parser.ts without delegating."))
        self.assertNotIn("--dangerously-skip-permissions", spec.argv)
        self.assertNotIn("Agent", ",".join(spec.argv))
        self.assertNotIn("Bash", ",".join(spec.argv))

    def test_workspace_write_argv_and_version_are_exactly_verified(self):
        spec = self.prepare(
            permission="workspace_write",
            model="claude-fixture-1",
            effort="high",
            version="2.1.220 (Claude Code)",
        )
        self.assertIn("acceptEdits", spec.argv)
        self.assertIn("Read,Glob,Grep,Edit,Write", spec.argv)
        self.assertEqual(spec.requested.model, "claude-fixture-1")
        self.assertEqual(spec.requested.effort, "high")
        self.assertEqual(spec.requested.harness_version, "2.1.220 (Claude Code)")
        self.assertEqual(spec.requested.verification, "verified")
        with self.assertRaisesRegex(ProfileUnsupported, "unverified claude"):
            self.prepare(version="2.2.0 (Claude Code)")

    def test_structured_result_and_faults_are_not_conflated_with_acceptance(self):
        spec = self.prepare()
        success = classify_cli(
            spec,
            returncode=0,
            stdout=json.dumps({
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "bounded implementation summary",
                "session_id": "fixture-session",
            }),
            stderr="",
        )
        self.assertEqual(success.execution_status, "succeeded")
        self.assertEqual(success.acceptance_status, "not_evaluated")

        auth = classify_cli(
            spec,
            returncode=0,
            stdout=json.dumps({
                "type": "result",
                "is_error": True,
                "result": "401 quota authorization required",
            }),
            stderr="",
        )
        self.assertEqual(auth.execution_status, "failed")
        self.assertEqual(auth.error_code, "AUTH_ERROR")

        denied = classify_cli(
            spec,
            returncode=0,
            stdout=json.dumps({
                "type": "result",
                "is_error": True,
                "result": "tool use denied",
            }),
            stderr="",
        )
        self.assertEqual(denied.execution_status, "denied")
        self.assertEqual(denied.error_code, "CLI_ERROR")

        malformed = classify_cli(
            spec,
            returncode=0,
            stdout=json.dumps({"type": "system", "subtype": "init"}),
            stderr="",
        )
        self.assertEqual(malformed.execution_status, "malformed")


if __name__ == "__main__":
    unittest.main()
