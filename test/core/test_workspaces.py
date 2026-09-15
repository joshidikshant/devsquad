import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.contracts import ContractError
from devsquad.workspaces import (
    assert_clean_inputs,
    committed_regular_file,
    prepare_check_workspace,
    prepare_review_workspace,
    repo_relative_config,
    resolve_commit,
)


class ReviewWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="devsquad-workspace-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.runtime = self.root / "runtime"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.name", "Test"], check=True,
        )
        for directory in ("src", "tests", "devsquad"):
            (self.repo / directory).mkdir()
        (self.repo / "src/app.py").write_text("VALUE = 'base'\n")
        (self.repo / "tests/test_app.py").write_text("# base test\n")
        (self.repo / "devsquad/profiles.json").write_text('{"profiles":"base"}\n')
        (self.repo / "devsquad/policy.json").write_text('{"policy":"base"}\n')
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "base"], check=True)
        self.base = resolve_commit(self.repo, "HEAD")
        (self.repo / "src/app.py").write_text("VALUE = 'candidate'\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "src/app.py"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-qm", "candidate"], check=True,
        )
        self.target = resolve_commit(self.repo, "HEAD")

    def prepare(self, run_id="run-1", target=None, scope=("src", "tests")):
        return prepare_review_workspace(
            self.repo,
            self.runtime,
            "project-1",
            run_id,
            self.base,
            target or self.target,
            scope,
            required_clean_paths=("devsquad/profiles.json", "devsquad/policy.json"),
        )

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout

    def test_workspace_is_detached_frozen_idempotent_and_checkout_preserving(self):
        (self.repo / "notes.txt").write_text("dirty but outside declared inputs\n")
        before_head = self.git("rev-parse", "HEAD")
        before_status = self.git("status", "--porcelain=v1", "-z")
        before_index = hashlib.sha256((self.repo / ".git/index").read_bytes()).hexdigest()

        first = self.prepare()
        after_head = self.git("rev-parse", "HEAD")
        after_status = self.git("status", "--porcelain=v1", "-z")
        after_index = hashlib.sha256((self.repo / ".git/index").read_bytes()).hexdigest()
        self.assertEqual((after_head, after_status, after_index), (
            before_head, before_status, before_index,
        ))
        workspace = Path(first["path"])
        self.assertEqual((workspace / "src/app.py").read_text(), "VALUE = 'candidate'\n")
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(workspace), "rev-parse", "--abbrev-ref", "HEAD"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip(),
            "HEAD",
        )
        self.assertEqual(first["base_oid"], self.base)
        self.assertEqual(first["target_oid"], self.target)
        self.assertEqual(first["changed_paths"], ["src/app.py"])
        self.assertEqual(first["scope"], ["src", "tests"])

        (self.repo / "src/app.py").write_text("VALUE = 'later'\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "src/app.py"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "later"], check=True)
        second = self.prepare()
        self.assertEqual(second, first)
        self.assertEqual((workspace / "src/app.py").read_text(), "VALUE = 'candidate'\n")

    def test_checks_get_a_separate_detached_candidate_workspace(self):
        review = self.prepare()
        check = prepare_check_workspace(
            self.repo,
            self.runtime,
            "project-1",
            "run-1",
            self.target,
            ("src", "tests"),
            required_clean_paths=("devsquad/profiles.json", "devsquad/policy.json"),
        )
        review_path, check_path = Path(review["path"]), Path(check["path"])
        self.assertNotEqual(review_path, check_path)
        self.assertEqual((check_path / "src/app.py").read_text(), "VALUE = 'candidate'\n")
        self.assertEqual(
            self.git("-C", str(check_path), "rev-parse", "--abbrev-ref", "HEAD").strip(),
            b"HEAD",
        )
        (check_path / "src/app.py").write_text("check side effect\n")
        self.assertEqual((review_path / "src/app.py").read_text(), "VALUE = 'candidate'\n")

    def test_dirty_scoped_tracked_path_is_rejected(self):
        (self.repo / "src/app.py").write_text("dirty\n")
        with self.assertRaisesRegex(ContractError, "src/app.py"):
            self.prepare()

    def test_dirty_required_config_is_rejected_even_outside_scope(self):
        (self.repo / "devsquad/policy.json").write_text("dirty\n")
        subprocess.run(
            ["git", "-C", str(self.repo), "add", "devsquad/policy.json"], check=True,
        )
        with self.assertRaisesRegex(ContractError, "devsquad/policy.json"):
            self.prepare(scope=("src",))

    def test_untracked_scoped_path_is_rejected(self):
        (self.repo / "tests/new_test.py").write_text("dirty\n")
        with self.assertRaisesRegex(ContractError, "tests/new_test.py"):
            self.prepare()

    def test_scoped_symlink_escape_is_rejected(self):
        outside = self.root / "outside.txt"
        outside.write_text("private\n")
        (self.repo / "src/leak").symlink_to(outside)
        subprocess.run(["git", "-C", str(self.repo), "add", "src/leak"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "symlink"], check=True)
        target = resolve_commit(self.repo, "HEAD")
        with self.assertRaisesRegex(ContractError, "symlink escapes"):
            self.prepare(run_id="symlink-run", target=target)

    def test_existing_workspace_for_another_target_is_rejected(self):
        self.prepare(run_id="reused-path")
        with self.assertRaisesRegex(ContractError, "different commit"):
            self.prepare(run_id="reused-path", target=self.base)

    def test_committed_config_reads_exact_regular_blob(self):
        expected = (self.repo / "devsquad/policy.json").read_bytes()
        self.assertEqual(
            committed_regular_file(self.repo, self.target, "devsquad/policy.json"),
            expected,
        )
        outside = self.root / "outside-config"
        outside.write_text("outside\n")
        (self.repo / "devsquad/link.json").symlink_to(outside)
        subprocess.run(["git", "-C", str(self.repo), "add", "devsquad/link.json"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-qm", "linked config"], check=True,
        )
        with self.assertRaisesRegex(ContractError, "regular file"):
            committed_regular_file(
                self.repo, resolve_commit(self.repo, "HEAD"), "devsquad/link.json",
            )

    def test_config_paths_and_identifiers_cannot_escape(self):
        self.assertEqual(
            repo_relative_config(
                self.repo, str(self.repo / "devsquad/policy.json"), "policy_file",
            ),
            "devsquad/policy.json",
        )
        with self.assertRaisesRegex(ContractError, "without traversal"):
            repo_relative_config(self.repo, "../policy.json", "policy_file")
        with self.assertRaisesRegex(ContractError, "escapes project"):
            repo_relative_config(self.repo, str(self.root / "outside.json"), "policy_file")
        with self.assertRaisesRegex(ContractError, "safe path segment"):
            prepare_review_workspace(
                self.repo,
                self.runtime,
                "../project",
                "run",
                self.base,
                self.target,
                ("src",),
            )

    def test_clean_input_helper_accepts_unrelated_dirty_files(self):
        (self.repo / "notes.txt").write_text("unrelated\n")
        assert_clean_inputs(
            self.repo,
            ("src", "tests"),
            ("devsquad/profiles.json", "devsquad/policy.json"),
        )


if __name__ == "__main__":
    unittest.main()
