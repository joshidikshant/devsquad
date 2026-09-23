from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "plugin" / "core"
sys.path.insert(0, str(CORE / "src"))

from devsquad.contracts import ContractError
from devsquad.workspaces import (
    freeze_delivery_candidate,
    prepare_delivery_workspace,
    resolve_commit,
)


class DeliveryWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.runtime = self.root / "runtime"
        self.remote = self.root / "remote.git"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q")
        self.git(self.repo, "config", "user.name", "Fixture")
        self.git(self.repo, "config", "user.email", "fixture@example.test")
        (self.repo / "src").mkdir()
        (self.repo / "tests").mkdir()
        (self.repo / "src/app.py").write_text("VALUE = 'base'\n")
        (self.repo / "tests/test_app.py").write_text("# base test\n")
        (self.repo / "README.md").write_text("fixture\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "base")
        self.baseline = resolve_commit(self.repo, "HEAD")
        subprocess.run(
            ["git", "init", "--bare", "-q", str(self.remote)], check=True,
        )
        self.git(self.repo, "remote", "add", "origin", str(self.remote))
        self.git(self.repo, "push", "-q", "origin", "HEAD:refs/heads/main")
        self.source_status = self.git(self.repo, "status", "--porcelain")
        self.source_refs = self.git(self.repo, "show-ref")
        self.remote_refs = self.git(self.remote, "show-ref")

    @staticmethod
    def git(repo: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout

    def prepare(self) -> dict[str, object]:
        return prepare_delivery_workspace(
            self.repo,
            self.runtime,
            "project-1",
            "run-1",
            self.baseline,
            ("src", "tests"),
            ("src/app.py", "tests"),
        )

    def assert_source_unchanged(self) -> None:
        self.assertEqual(resolve_commit(self.repo, "HEAD"), self.baseline)
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), self.source_status)
        self.assertEqual(self.git(self.repo, "show-ref"), self.source_refs)
        self.assertEqual(self.git(self.remote, "show-ref"), self.remote_refs)
        self.assertEqual((self.repo / "src/app.py").read_text(), "VALUE = 'base'\n")

    def test_scoped_candidate_commit_patch_and_replay_preserve_source_and_remote(self):
        prepared = self.prepare()
        workspace = Path(prepared["path"])
        self.assertEqual(self.git(workspace, "rev-parse", "--abbrev-ref", "HEAD").strip(), "HEAD")
        (workspace / "src/app.py").write_text("VALUE = 'fixed'\n")
        (workspace / "tests/test regression.py").write_text(
            "def test_regression():\n    assert True\n"
        )

        candidate, patch = freeze_delivery_candidate(
            self.repo,
            workspace,
            self.baseline,
            ("src/app.py", "tests"),
            "run-1",
        )
        self.assertEqual(candidate["baseline_oid"], self.baseline)
        self.assertEqual(candidate["patch_sha256"], hashlib.sha256(patch).hexdigest())
        self.assertEqual(
            candidate["captured_untracked_paths"], ["tests/test regression.py"],
        )
        self.assertEqual(
            candidate["changed_paths"], ["src/app.py", "tests/test regression.py"],
        )
        self.assertIn(b"VALUE = 'fixed'", patch)
        self.assertEqual(
            self.git(workspace, "rev-parse", "HEAD^").strip(), self.baseline,
        )
        self.assertEqual(self.git(workspace, "status", "--porcelain"), "")
        replayed, replay_patch = freeze_delivery_candidate(
            self.repo,
            workspace,
            self.baseline,
            ("src/app.py", "tests"),
            "run-1",
        )
        self.assertEqual(replayed, candidate)
        self.assertEqual(replay_patch, patch)
        self.assert_source_unchanged()

    def test_later_mutation_cannot_replay_a_frozen_candidate(self):
        workspace = Path(self.prepare()["path"])
        (workspace / "src/app.py").write_text("VALUE = 'candidate'\n")
        freeze_delivery_candidate(
            self.repo,
            workspace,
            self.baseline,
            ("src/app.py", "tests"),
            "run-1",
        )
        (workspace / "src/app.py").write_text("VALUE = 'stale'\n")
        with self.assertRaisesRegex(ContractError, "later workspace changes"):
            freeze_delivery_candidate(
                self.repo,
                workspace,
                self.baseline,
                ("src/app.py", "tests"),
                "run-1",
            )
        self.assert_source_unchanged()

    def test_out_of_scope_change_is_rejected_before_commit(self):
        workspace = Path(self.prepare()["path"])
        (workspace / "README.md").write_text("unauthorized\n")
        with self.assertRaisesRegex(ContractError, "outside write scope"):
            freeze_delivery_candidate(
                self.repo,
                workspace,
                self.baseline,
                ("src/app.py", "tests"),
                "run-1",
            )
        self.assertEqual(resolve_commit(workspace, "HEAD"), self.baseline)
        self.assert_source_unchanged()

    def test_new_symlink_cannot_escape_the_delivery_workspace(self):
        workspace = Path(self.prepare()["path"])
        outside = self.root / "outside-secret"
        outside.write_text("private\n")
        (workspace / "tests/leak").symlink_to(outside)
        with self.assertRaisesRegex(ContractError, "escapes its workspace"):
            freeze_delivery_candidate(
                self.repo,
                workspace,
                self.baseline,
                ("src/app.py", "tests"),
                "run-1",
            )
        self.assertEqual(resolve_commit(workspace, "HEAD"), self.baseline)
        self.assert_source_unchanged()

    def test_candidate_requires_a_change(self):
        workspace = Path(self.prepare()["path"])
        with self.assertRaisesRegex(ContractError, "contains no changes"):
            freeze_delivery_candidate(
                self.repo,
                workspace,
                self.baseline,
                ("src/app.py", "tests"),
                "run-1",
            )
        self.assert_source_unchanged()


if __name__ == "__main__":
    unittest.main()
