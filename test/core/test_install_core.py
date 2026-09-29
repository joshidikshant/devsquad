import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from devsquad_test_fixtures import branch_review_routing_documents


ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "plugin/core"
INSTALLER = ROOT / "scripts/install-core.sh"
COMPOSITE_INSTALLER = ROOT / "install.sh"


class StandaloneInstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devsquad-install-")
        self.root = Path(self.temp.name)
        self.install_root = self.root / "installed"
        self.bin_dir = self.root / "bin"
        self.environment = os.environ.copy()
        self.environment.update({
            "DEVSQUAD_INSTALL_ROOT": str(self.install_root),
            "DEVSQUAD_BIN_DIR": str(self.bin_dir),
            "DEVSQUAD_PYTHON": sys.executable,
        })
        self.environment.pop("PYTHONPATH", None)

    def tearDown(self):
        self.temp.cleanup()

    def install(self, source=CORE, *arguments):
        result = subprocess.run(
            [
                "/bin/bash", str(INSTALLER),
                "--source-core", str(source),
                "--json", *arguments,
            ],
            check=True,
            text=True,
            capture_output=True,
            env=self.environment,
            cwd=ROOT,
        )
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1, result.stdout)
        return json.loads(lines[0])

    def changed_source(self):
        destination = self.root / "core-v2"
        shutil.copytree(
            CORE,
            destination,
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".pytest_cache", "*.egg-info",
            ),
        )
        pyproject = destination / "pyproject.toml"
        pyproject.write_text(
            pyproject.read_text().replace('version = "0.1.0"', 'version = "0.1.1"')
        )
        package = destination / "src/devsquad/__init__.py"
        package.write_text(
            package.read_text().replace('__version__ = "0.1.0"', '__version__ = "0.1.1"')
        )
        return destination

    def test_fresh_install_requires_no_claude_and_reinstall_is_idempotent(self):
        self.environment["PATH"] = "/usr/bin:/bin"

        first = self.install()
        self.assertTrue(first["changed"])
        self.assertIsNone(first["error"])
        self.assertEqual(first["drift"], {
            "plugin_installed": False,
            "source_installed": False,
            "source_plugin": False,
        })
        self.assertFalse(first["installed"]["mcp"])
        self.assertTrue(first["installed_manifest_matches"])
        launcher = self.bin_dir / "squad"
        self.assertTrue(os.access(launcher, os.X_OK))
        version = subprocess.run(
            [str(launcher), "--version"],
            check=True,
            text=True,
            capture_output=True,
            env=self.environment,
        )
        self.assertEqual(version.stdout.strip(), "squad 0.1.0")
        self.assertEqual(version.stderr, "")

        release = Path(first["current_target"])
        self.assertTrue((release / "core/adapters/codex/adapter.json").is_file())
        self.assertTrue((release / "core/integrations/grok/registration.json").is_file())
        self.assertTrue((release / "venv/lib").is_dir())
        before_state = (self.install_root / "install-state.json").read_bytes()
        before_launcher = launcher.read_bytes()

        second = self.install()
        self.assertFalse(second["changed"])
        self.assertEqual(second["current_target"], first["current_target"])
        self.assertEqual((self.install_root / "install-state.json").read_bytes(), before_state)
        self.assertEqual(launcher.read_bytes(), before_launcher)
        self.assertEqual(len(list((self.install_root / "releases").iterdir())), 1)

    def test_composite_installer_succeeds_without_claude(self):
        self.environment["PATH"] = "/usr/bin:/bin"
        result = subprocess.run(
            ["/bin/bash", str(COMPOSITE_INSTALLER), "--core-only", "--json"],
            check=True,
            text=True,
            capture_output=True,
            env=self.environment,
            cwd=ROOT,
        )
        report = json.loads(result.stdout)
        self.assertTrue(report["changed"])
        self.assertIn("Claude plugin: skipped", result.stderr)
        self.assertEqual(
            subprocess.run(
                [str(self.bin_dir / "squad"), "--version"],
                check=True,
                text=True,
                capture_output=True,
                env=self.environment,
            ).stdout.strip(),
            "squad 0.1.0",
        )

    def test_known_legacy_release_symlink_is_migrated_but_arbitrary_launcher_is_not(self):
        legacy = self.install_root / "releases/0.1.0+legacy/venv/bin"
        legacy.mkdir(parents=True)
        legacy_squad = legacy / "squad"
        legacy_squad.write_text("#!/bin/sh\nexit 0\n")
        legacy_squad.chmod(0o755)
        self.bin_dir.mkdir()
        launcher = self.bin_dir / "squad"
        launcher.symlink_to(legacy_squad)

        migrated = self.install()
        self.assertTrue(migrated["changed"])
        self.assertFalse(launcher.is_symlink())
        self.assertIn("managed-by: devsquad-install-core-v1", launcher.read_text())
        self.assertTrue(legacy_squad.is_file())

        other_root = self.root / "other-install"
        other_bin = self.root / "other-bin"
        other_bin.mkdir()
        arbitrary = self.root / "arbitrary-squad"
        arbitrary.write_text("#!/bin/sh\nexit 0\n")
        arbitrary.chmod(0o755)
        (other_bin / "squad").symlink_to(arbitrary)
        environment = {
            **self.environment,
            "DEVSQUAD_INSTALL_ROOT": str(other_root),
            "DEVSQUAD_BIN_DIR": str(other_bin),
        }
        refused = subprocess.run(
            ["/bin/bash", str(INSTALLER), "--json"],
            text=True,
            capture_output=True,
            env=environment,
            cwd=ROOT,
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("refusing to replace unmanaged launcher", refused.stderr)
        self.assertTrue((other_bin / "squad").is_symlink())

    def test_composite_claude_reinstall_uses_plugin_hooks_once(self):
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir()
        fake_claude = fake_bin / "claude"
        fake_claude.write_text("""#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$DEVSQUAD_FAKE_CLAUDE_LOG"
case "$*" in
  "plugin marketplace list")
    if [ -f "$DEVSQUAD_FAKE_CLAUDE_STATE/marketplace" ]; then echo devsquad-marketplace; fi ;;
  "plugin marketplace add "*) touch "$DEVSQUAD_FAKE_CLAUDE_STATE/marketplace" ;;
  "plugin marketplace update "*) : ;;
  "plugin list")
    if [ -f "$DEVSQUAD_FAKE_CLAUDE_STATE/plugin" ]; then echo devsquad@devsquad-marketplace; fi ;;
  "plugin install "*) touch "$DEVSQUAD_FAKE_CLAUDE_STATE/plugin" ;;
  "plugin update "*) : ;;
  "plugin enable "*) : ;;
  *) exit 64 ;;
esac
""")
        fake_claude.chmod(0o755)
        state = self.root / "fake-claude-state"
        state.mkdir()
        log = self.root / "fake-claude.log"
        home = self.root / "home"
        settings = home / ".claude/settings.json"
        settings.parent.mkdir(parents=True)
        original_settings = '{"hooks":{"sentinel":[]}}\n'
        settings.write_text(original_settings)
        environment = self.environment.copy()
        environment.update({
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "HOME": str(home),
            "DEVSQUAD_FAKE_CLAUDE_LOG": str(log),
            "DEVSQUAD_FAKE_CLAUDE_STATE": str(state),
        })
        command = ["/bin/bash", str(COMPOSITE_INSTALLER), "--with-claude"]
        first = subprocess.run(
            command, text=True, capture_output=True, env=environment, cwd=ROOT,
        )
        self.assertEqual(first.returncode, 0, (first.stdout, first.stderr))
        second = subprocess.run(
            command, text=True, capture_output=True, env=environment, cwd=ROOT,
        )
        self.assertEqual(second.returncode, 0, (second.stdout, second.stderr))
        self.assertIn("Claude plugin ready", first.stdout)
        self.assertIn("Claude plugin ready", second.stdout)
        self.assertEqual(settings.read_text(), original_settings)
        calls = log.read_text().splitlines()
        self.assertEqual(calls, [
            "plugin marketplace list",
            "plugin marketplace add https://github.com/joshidikshant/devsquad.git",
            "plugin list",
            "plugin install devsquad@devsquad-marketplace",
            "plugin enable devsquad@devsquad-marketplace",
            "plugin marketplace list",
            "plugin marketplace update devsquad-marketplace",
            "plugin list",
            "plugin update devsquad@devsquad-marketplace",
            "plugin enable devsquad@devsquad-marketplace",
        ])

    def test_marketplace_package_source_contains_the_complete_core(self):
        marketplace = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
        entries = [item for item in marketplace["plugins"] if item["name"] == "devsquad"]
        self.assertEqual(len(entries), 1)
        plugin_root = (ROOT / entries[0]["source"]).resolve()
        self.assertEqual(plugin_root, (ROOT / "plugin").resolve())
        required = {
            "core/pyproject.toml",
            "core/bin/squad",
            "core/src/devsquad/cli.py",
            "core/src/devsquad/migrations/013_decision_observations.sql",
            "core/integrations/codex/registration.json",
            "core/schemas/task.schema.json",
        }
        self.assertEqual(
            {path for path in required if not (plugin_root / path).is_file()},
            set(),
        )

    def test_status_reports_drift_and_update_selects_a_new_immutable_release(self):
        first = self.install()
        old_release = Path(first["current_target"])
        old_manifest = (old_release / "release.json").read_bytes()
        source = self.changed_source()

        status = self.install(source, "--status")
        self.assertFalse(status["changed"])
        self.assertEqual(status["drift"], {
            "plugin_installed": False,
            "source_installed": True,
            "source_plugin": True,
        })

        updated = self.install(source)
        self.assertTrue(updated["changed"])
        self.assertNotEqual(updated["current_target"], first["current_target"])
        self.assertEqual(updated["installed"]["version"], "0.1.1")
        self.assertEqual(updated["drift"], {
            "plugin_installed": True,
            "source_installed": False,
            "source_plugin": True,
        })
        self.assertTrue(old_release.is_dir())
        self.assertEqual((old_release / "release.json").read_bytes(), old_manifest)
        self.assertEqual(len(list((self.install_root / "releases").iterdir())), 2)
        version = subprocess.run(
            [str(self.bin_dir / "squad"), "--version"],
            check=True,
            text=True,
            capture_output=True,
            env=self.environment,
        )
        self.assertEqual(version.stdout.strip(), "squad 0.1.1")

    def test_update_does_not_break_an_active_release_pinned_run(self):
        first = self.install()
        old_release = Path(first["current_target"])
        old_python = old_release / "venv/bin/python"
        repo = self.root / "repo"
        runtime = self.root / "runtime"
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
        (repo / "src/app.py").write_text("VALUE = 'base'\n")
        (repo / "tests/test_app.py").write_text("# fixture test\n")
        profiles, policy = branch_review_routing_documents()
        (repo / "profiles.json").write_text(profiles)
        (repo / "policy.json").write_text(policy)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
        task = json.loads(
            (ROOT / "docs/plans/engineering-team/examples/branch-review.json").read_text()
        )
        task["project"] = {
            "repo_path": str(repo), "base_ref": "HEAD", "target_ref": "HEAD",
        }
        task["routing"] = {
            "profiles_file": "profiles.json", "policy_file": "policy.json",
        }
        task_path = self.root / "active-task.json"
        task_path.write_text(json.dumps(task))
        start_script = """
import json
from pathlib import Path
import sys
from devsquad.service import Service
task = json.loads(Path(sys.argv[1]).read_text())
print(json.dumps(Service(Path(sys.argv[2])).start(
    task, "installer-update", _internal_fake_delay=4,
)))
"""
        started_process = subprocess.run(
            [str(old_python), "-P", "-c", start_script, str(task_path), str(runtime)],
            check=True,
            text=True,
            capture_output=True,
            env=self.environment,
            cwd=self.root,
        )
        started = json.loads(started_process.stdout)
        self.assertEqual(started["state"], "queued", started)

        launcher = self.bin_dir / "squad"
        deadline = time.monotonic() + 8
        before_update = None
        while time.monotonic() < deadline:
            before_update = self.cli_json(launcher, "status", started["run_id"], "--runtime-dir", str(runtime))
            if before_update["data"]["state"] == "running":
                break
            time.sleep(0.05)
        self.assertEqual(before_update["data"]["state"], "running", before_update)

        updated = self.install(self.changed_source())
        self.assertNotEqual(updated["current_target"], str(old_release))
        self.assertTrue(old_release.is_dir())

        deadline = time.monotonic() + 10
        after_update = None
        while time.monotonic() < deadline:
            after_update = self.cli_json(launcher, "status", started["run_id"], "--runtime-dir", str(runtime))
            if after_update["data"]["state"] == "succeeded":
                break
            time.sleep(0.05)
        self.assertEqual(after_update["data"]["state"], "succeeded", after_update)
        result = self.cli_json(
            launcher, "result", started["run_id"], "--runtime-dir", str(runtime),
        )
        self.assertTrue(result["data"]["ready"])
        with sqlite3.connect(runtime / "state.sqlite3") as connection:
            package_path, package_digest = connection.execute(
                "SELECT package_path, package_digest FROM runs WHERE id=?",
                (started["run_id"],),
            ).fetchone()
        self.assertTrue(Path(package_path).is_dir())
        self.assertEqual(len(package_digest), 64)

    def cli_json(self, launcher, *arguments):
        result = subprocess.run(
            [str(launcher), *arguments, "--json"],
            check=True,
            text=True,
            capture_output=True,
            env=self.environment,
            cwd=self.root,
        )
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)


if __name__ == "__main__":
    unittest.main()
