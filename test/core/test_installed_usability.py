"""Fresh installed normal commands, with only provider binaries replaced."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))
import test_cli as cli_fixtures


class InstalledNormalUsabilityTest(unittest.TestCase):
    def setUp(self):
        python = cli_fixtures.InstalledWheelMigrationTest.build_python()
        if python is None:
            self.skipTest("offline supported build interpreter is unavailable")
        self.temp = tempfile.TemporaryDirectory(prefix="devsquad-installed-ux-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.provider_bin = self.root / "providers"
        self.provider_bin.mkdir()
        for name, target in (
            ("codex", ROOT / "test/core/fakes/codex_review_cli.py"),
            ("claude", ROOT / "test/core/fakes/claude_delivery_cli.py"),
            ("python3", Path(python)),
            ("git", Path(shutil.which("git"))),
        ):
            (self.provider_bin / name).symlink_to(target)
        codex_home = self.home / ".codex"
        codex_home.mkdir()
        (codex_home / "auth.json").write_text("{}\n")
        (codex_home / "auth.json").chmod(0o600)
        claude_home = self.home / ".claude"
        claude_home.mkdir()
        (claude_home / ".credentials.json").write_text("{}\n")
        (claude_home / ".credentials.json").chmod(0o600)
        self.runtime = self.root / "runtime"
        self.environment = {
            "HOME": str(self.home), "USER": "devsquad-offline-test",
            "PATH": f"{self.provider_bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "CODEX_HOME": str(codex_home), "PYTHONWARNINGS": "error::ResourceWarning",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "DEVSQUAD_PYTHON": python, "DEVSQUAD_RUNTIME_DIR": str(self.runtime),
            "DEVSQUAD_INSTALL_ROOT": str(self.root / "installed"),
            "DEVSQUAD_BIN_DIR": str(self.root / "bin"),
        }
        installed = subprocess.run(
            ["/bin/bash", str(ROOT / "scripts/install-core.sh"), "--source-core", str(ROOT / "plugin/core"), "--json"],
            cwd=self.root, env=self.environment, text=True, capture_output=True, timeout=60,
        )
        self.assertEqual((installed.returncode, installed.stderr), (0, ""), installed.stdout)
        self.installation = json.loads(installed.stdout)
        self.assertTrue(self.installation["changed"])
        self.assertFalse(self.installation["installed"]["mcp"])
        self.launcher = self.root / "bin/squad"
        self.run_ids = set()
        self.addCleanup(self.cancel_unfinished_runs)

    def git(self, repo, *arguments):
        return subprocess.run([str(self.provider_bin / "git"), "-C", str(repo), *arguments],
                              env=self.environment, text=True, capture_output=True, check=True).stdout

    def project(self, name, *, defective):
        repo = self.root / name
        repo.mkdir()
        self.git(repo, "init", "-qb", "main")
        self.git(repo, "config", "user.name", "Offline Test")
        self.git(repo, "config", "user.email", "test@example.invalid")
        (repo / "src").mkdir()
        (repo / "tests").mkdir()
        (repo / "src/app.py").write_text(f"def add(a, b):\n    return a {'-' if defective else '+'} b\n")
        (repo / "tests/test_app.py").write_text(
            "import unittest\nfrom src.app import add\n"
            "class AdditionTest(unittest.TestCase):\n"
            "    def test_addition(self):\n        self.assertEqual(add(2, 3), 5)\n"
        )
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-qm", "seeded fixture")
        return repo

    def command(self, repo, *arguments, expected=0):
        result = subprocess.run([str(self.launcher), *arguments], cwd=repo, env=self.environment,
                                text=True, capture_output=True, timeout=60)
        self.run_ids.update(re.findall(r"^Run: ([a-f0-9-]{36})$", result.stdout, re.MULTILINE))
        evidence = result.stdout
        if result.returncode != expected:
            for run_id in re.findall(r"^Run: ([a-f0-9-]{36})$", result.stdout, re.MULTILINE):
                report = subprocess.run([str(self.launcher), "result", run_id, "--json"], cwd=repo,
                                        env=self.environment, text=True, capture_output=True, timeout=15)
                if report.returncode == 0:
                    for artifact in json.loads(report.stdout)["data"]["artifacts"]:
                        if artifact["name"] == "receipt.json":
                            evidence += "\n" + Path(artifact["path"]).read_text()
        self.assertEqual((result.returncode, result.stderr), (expected, ""), evidence)
        return result.stdout

    def receipt(self, repo, run_id):
        result = json.loads(self.command(repo, "result", run_id, "--json"))["data"]
        self.assertTrue(result["ready"])
        self.assertEqual(result["state"], "succeeded")
        for artifact in result["artifacts"]:
            self.assertEqual(hashlib.sha256(Path(artifact["path"]).read_bytes()).hexdigest(), artifact["sha256"])
        reference = next(item for item in result["artifacts"] if item["name"] == "receipt.json")
        return json.loads(Path(reference["path"]).read_text())

    def cancel_unfinished_runs(self):
        for run_id in self.run_ids:
            subprocess.run([str(self.launcher), "cancel", run_id, "--json"], cwd=self.root,
                           env=self.environment, text=True, capture_output=True, timeout=15)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                result = subprocess.run([str(self.launcher), "status", run_id, "--json"], cwd=self.root,
                                        env=self.environment, text=True, capture_output=True, timeout=5)
                if result.returncode == 0 and json.loads(result.stdout)["data"]["state"] in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(0.05)
            else:
                self.fail(f"installed fixture cleanup did not terminalize {run_id}")

    def test_fresh_installed_review_and_fix_finish_using_normal_commands(self):
        review_repo = self.project("review-project", defective=False)
        output = self.command(review_repo, "status", expected=64)
        self.assertIn("No saved runs", output)
        output = self.command(self.root, "review", "--project-dir", str(review_repo), "--base", "main", "--wait", expected=2)
        self.assertIn("Review: clean", output)
        self.assertIn("squad finish", output)
        review_id = json.loads(self.command(review_repo, "status", "--json"))["data"]["run_id"]
        self.command(review_repo, "finish", "--accept", "--reason", "Inspected the exact saved review.")
        self.assertIn("receipt.md", self.command(review_repo, "result"))
        review_receipt = self.receipt(review_repo, review_id)
        self.assertEqual(review_receipt["accounting"]["worker_invocations"], 1)

        fix_repo = self.project("fix-project", defective=True)
        self.assertIn("No saved runs", self.command(fix_repo, "status", expected=64))
        original_oid = self.git(fix_repo, "rev-parse", "HEAD")
        original_source = (fix_repo / "src/app.py").read_bytes()
        seeded_failure = subprocess.run([str(self.provider_bin / "python3"), "-m", "unittest", "discover", "-s", "tests"],
                                        cwd=fix_repo, env=self.environment, capture_output=True, text=True)
        self.assertEqual(seeded_failure.returncode, 1)
        output = self.command(self.root, "fix", "Correct add so it returns the sum.", "--project-dir", str(fix_repo), "--write-path", "src/app.py", "--wait", expected=2)
        self.assertIn("Check detected-tests: passed", output)
        fix_id = json.loads(self.command(fix_repo, "status", "--json"))["data"]["run_id"]
        self.assertNotEqual(fix_id, review_id)
        self.command(fix_repo, "finish", "--accept", "--reason", "Seeded addition test and independent review pass.")
        receipt = self.receipt(fix_repo, fix_id)
        self.assertEqual(receipt["accounting"]["worker_invocations"], 2)
        identities = [item["observed_identity"] for item in receipt["attempts"]]
        self.assertEqual([item["harness"] for item in identities], ["claude", "codex"])
        self.assertNotEqual(identities[0]["model_id"], identities[1]["model_id"])
        self.assertEqual(identities[0]["verification_scope"], "reported_model")
        self.assertEqual(identities[0]["native_evidence"]["writer_messages"]["message_count"], 1)
        self.assertTrue(all(item["required_to_pass"] and item["status"] == "passed" for item in receipt["checks"]))
        self.assertTrue(all(item["integrity"]["status"] == "verified" for item in receipt["checks"]))
        self.assertEqual(self.git(fix_repo, "rev-parse", "HEAD"), original_oid)
        self.assertEqual((fix_repo / "src/app.py").read_bytes(), original_source)
        self.assertEqual(self.git(fix_repo, "status", "--porcelain"), "")

        # Omitted IDs stay project-scoped even when newer unrelated runs exist.
        self.assertEqual(json.loads(self.command(review_repo, "status", "--json"))["data"]["run_id"], review_id)
        self.command(review_repo, "review", "--base", "main", "--idempotency-key", "installed-second-review", "--wait", expected=2)
        output = self.command(review_repo, "status", expected=75)
        self.assertIn("Multiple saved runs", output)
        self.assertIn(review_id, output)
        self.assertNotIn(fix_id, output)
        output = self.command(review_repo, "result", expected=75)
        self.assertIn("specify RUN", output)
        self.assertTrue(json.loads(self.command(review_repo, "result", review_id, "--json"))["data"]["ready"])


if __name__ == "__main__":
    unittest.main()
