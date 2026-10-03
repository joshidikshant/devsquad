from __future__ import annotations

from pathlib import Path
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))
from devsquad.council_task_entry import build_council_task
from devsquad.contracts import CapabilityUnavailable, ContractError
from devsquad import cli


class CouncilEntryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="council-entry-public-")
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        for args in (("init", "-q"), ("config", "user.email", "fixture@example.invalid"),
                     ("config", "user.name", "Public fixture")):
            self.git(*args)
        (self.repo / "README").write_text("Frozen public input\n")
        self.git("add", "README")
        self.git("commit", "-qm", "Frozen public input")
        self.identities = [{"harness": "codex", "model_id": "model-" + name, "model_family": name,
            "effort": "low", "harness_version": "codex-cli test", "account_pool_id": "subscription-shared"}
            for name in ("a", "b", "c")]

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    def test_manual_scope_budget_and_selection_are_explicit_without_launch(self):
        task, summary = build_council_task(project_dir=self.repo, goal="Compare alternatives", identities=self.identities,
            read_paths=["README"], max_invocations=4)
        self.assertEqual(task["scope"], {"read_paths": ["README"], "write_paths": []})
        self.assertEqual(task["budget"]["max_worker_invocations"], 4)
        self.assertEqual(task["budget"]["max_revisions"], 0)
        self.assertFalse(summary["automatic_enabled"])
        self.assertEqual(set(summary["profiles"]), {"proposer_a", "proposer_b", "critic"})
        self.assertTrue(all(profile["quality_status"] == "trial" for profile in summary["profiles"].values()))
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_catalog_duplicates_missing_identity_and_unsafe_scope_fail_closed(self):
        with self.assertRaises(CapabilityUnavailable):
            build_council_task(project_dir=self.repo, goal="Question", identities=self.identities[:2] + [self.identities[0]])
        incomplete = [{**identity} for identity in self.identities]
        incomplete[1].pop("harness_version")
        with self.assertRaises(ContractError):
            build_council_task(project_dir=self.repo, goal="Question", identities=incomplete)
        for path in ("../peer", "/private/peer"):
            with self.assertRaises(ContractError):
                build_council_task(project_dir=self.repo, goal="Question", identities=self.identities, read_paths=[path])

    def test_normal_dry_run_human_and_json_preserve_explicit_scope_and_cap(self):
        for json_mode in (False, True):
            output = io.StringIO()
            with patch.object(cli, "discover_codex_identity", return_value=self.identities), contextlib.redirect_stdout(output):
                args = ["council", "Compare retry alternatives", "--project-dir", str(self.repo),
                        "--read-path", "README", "--lead", "host", "--max-invocations", "3", "--dry-run"]
                if json_mode:
                    args.append("--json")
                self.assertEqual(cli.main(args), 0)
            if json_mode:
                value = json.loads(output.getvalue())
                self.assertEqual(set(value), {"schema_version", "ok", "data", "error"})
                self.assertTrue(value["data"]["dry_run"])
                self.assertIsNone(value["data"]["run_id"])
                self.assertFalse(value["data"]["native_ready"])
                self.assertEqual(value["data"]["scope"]["read_paths"], ["README"])
            else:
                self.assertIn("proposer_a: codex model-a", output.getvalue())
                self.assertIn("Read scope: README; write scope: none", output.getvalue())
                self.assertIn("Worker cap: 3; rounds: 1; lead: host; automatic off", output.getvalue())
                self.assertIn("Native readiness: unavailable", output.getvalue())


if __name__ == "__main__":
    unittest.main()
