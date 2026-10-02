import io
import copy
import contextlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.contracts import ContractError
from devsquad.service import Service
from devsquad.task_entry import (
    build_managed_task,
    discover_codex_identity,
    parse_checks,
)
from devsquad.router import load_routing
from devsquad.store import ConflictError, Store, canonical_json
from devsquad import cli


class ManagedTaskEntryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devsquad-task-entry-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "project"
        self.repo.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main"], cwd=self.repo,
            check=True, text=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "DevSquad Test"],
            cwd=self.repo, check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=self.repo, check=True,
        )
        (self.repo / "src").mkdir()
        (self.repo / "src/app.py").write_text("VALUE = 1\n")
        (self.repo / "test").mkdir()
        (self.repo / "test/run.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
        subprocess.run(
            ["git", "add", "."], cwd=self.repo, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "fixture"], cwd=self.repo,
            check=True, text=True, capture_output=True,
        )
        self.oid = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo,
            check=True, text=True, capture_output=True,
        ).stdout.strip()
        self.codex = {
            "harness": "codex",
            "harness_version": "codex-cli fixture",
            "model_id": "gpt-fixture",
            "model_family": "gpt",
            "effort": "low",
        }

    def _init_repo(self):
        temp = tempfile.TemporaryDirectory(prefix="devsquad-task-entry-discovery-")
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name) / "project"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main"], cwd=repo,
            check=True, text=True, capture_output=True,
        )
        subprocess.run(["git", "config", "user.name", "DevSquad Test"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True,
        )
        return repo

    def _commit(self, repo, message):
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", message], cwd=repo,
            check=True, text=True, capture_output=True,
        )
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo,
            check=True, text=True, capture_output=True,
        ).stdout.strip()

    def _stage_raw_path_blob(self, repo, path_bytes: bytes, content: bytes) -> None:
        """Stage a blob at a raw byte path, bypassing filesystem filename rules."""
        hashed = subprocess.run(
            ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
            input=content, check=True, capture_output=True,
        )
        sha = hashed.stdout.strip().decode()
        cacheinfo = f"100644,{sha},".encode() + path_bytes
        subprocess.run(
            ["git", "-C", str(repo), "update-index", "--add", "--cacheinfo", cacheinfo],
            check=True, capture_output=True,
        )

    def _commit_index(self, repo, message):
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", message],
            check=True, capture_output=True,
        )
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, text=True, capture_output=True,
        ).stdout.strip()

    def test_branch_review_freezes_exact_commits_and_embedded_routing(self):
        task, summary = build_managed_task(
            workflow="branch-review",
            project_dir=self.repo / "src",
            base_ref="main",
            target_ref="HEAD",
            goal="Review the exact branch delta.",
            codex_identity=self.codex,
            checks=parse_checks(["python3 -m unittest"]),
        )

        self.assertEqual(task["project"], {
            "repo_path": str(self.repo.resolve()),
            "base_ref": self.oid,
            "target_ref": self.oid,
        })
        self.assertEqual(task["scope"], {
            "read_paths": ["."], "write_paths": [],
        })
        self.assertNotIn("profiles_file", task["routing"])
        self.assertNotIn("policy_file", task["routing"])
        self.assertEqual(
            [profile["harness"] for profile in task["routing"]["profiles"]["profiles"]],
            ["codex"],
        )
        self.assertTrue(all(
            not check["required_to_pass"] for check in task["checks"]
        ))
        self.assertEqual(
            [check["id"] for check in task["checks"]],
            ["candidate-diff-check", "detected-tests", "user-check-1"],
        )
        self.assertEqual(summary["base_oid"], self.oid)
        self.assertTrue(summary["planned_roles"]["reviewer"]["profile_id"].startswith("managed-codex-reviewer-"))
        self.assertEqual(len(summary["task_sha256"]), 64)

    def test_issue_delivery_is_bounded_to_one_writer_and_required_checks(self):
        task, summary = build_managed_task(
            workflow="issue-delivery",
            project_dir=self.repo,
            base_ref="HEAD",
            target_ref="HEAD",
            goal="Correct the bounded fixture issue.",
            codex_identity=self.codex,
            write_paths=("src", "src/"),
            checks=parse_checks(["python3 -m unittest discover -s test"]),
            review_mode="adversarial",
            review_focus="state transitions",
            claude_model="claude-sonnet-fixture",
            claude_effort="high",
        )

        self.assertEqual(task["scope"]["write_paths"], ["src"])
        self.assertEqual(task["review"], {
            "mode": "adversarial", "focus": "state transitions",
        })
        self.assertTrue(all(
            check["required_to_pass"] for check in task["checks"]
        ))
        self.assertEqual(
            task["checks"][0]["argv"],
            ["git", "diff", "--check", self.oid, "HEAD", "--"],
        )
        self.assertTrue(summary["planned_roles"]["implementer"]["profile_id"].startswith("managed-claude-implementer-"))
        self.assertEqual(summary["planned_roles"]["reviewer"]["harness"], "codex")
        self.assertIn("different-harness", summary["selection_reason"])

        default_task, _ = build_managed_task(
            workflow="issue-delivery",
            project_dir=self.repo,
            base_ref="HEAD",
            target_ref="HEAD",
            goal="Default scope.",
            codex_identity=self.codex,
        )
        self.assertEqual(default_task["scope"]["write_paths"], ["."])

    def test_normal_roles_use_stable_trial_aliases_not_concrete_defaults(self):
        task, summary = build_managed_task(
            workflow="issue-delivery", project_dir=self.repo,
            base_ref="HEAD", target_ref="HEAD", goal="Bounded routing proof.",
            codex_identity=self.codex,
        )
        self.assertEqual(task["routing"]["policy"]["roles"], {
            "implementer": [{"kind": "alias", "id": "implement.balanced"}],
            "reviewer": [{"kind": "alias", "id": "review.deep"}],
        })
        self.assertIn("bounded trial", summary["selection_reason"])
        self.assertTrue(all(p["quality_status"] == "trial" for p in task["routing"]["profiles"]["profiles"]))

    def test_approved_alias_survives_provider_default_and_explicit_pin_is_truthful(self):
        original, _ = build_managed_task(
            workflow="branch-review", project_dir=self.repo,
            base_ref="HEAD", target_ref="HEAD", goal="Routing proof.",
            codex_identity=self.codex,
        )
        incumbent = copy.deepcopy(original["routing"]["profiles"]["profiles"][0])
        incumbent.update(id="approved-reviewer", quality_status="proven")
        bindings = {"reviewer": {"alias": "review.deep", "version": 7, "profile": incumbent}}
        changed_default = {**self.codex, "model_id": "gpt-new-default"}
        for pinned in (False, True):
            with self.subTest(pinned=pinned):
                task, summary = build_managed_task(
                    workflow="branch-review", project_dir=self.repo,
                    base_ref="HEAD", target_ref="HEAD", goal="Routing proof.",
                    codex_identity=changed_default, role_bindings=bindings,
                    pinned_roles=("reviewer",) if pinned else (),
                )
                routing = load_routing(task, canonical_json(task["routing"]["profiles"]), canonical_json(task["routing"]["policy"]))
                selected = routing["roles"]["reviewer"]["selected"]
                self.assertEqual(selected["profile"]["model_id"], "gpt-new-default" if pinned else "gpt-fixture")
                self.assertEqual(routing["roles"]["reviewer"]["source"], "override" if pinned else "automatic")
                self.assertEqual(summary["planned_roles"]["reviewer"]["selection_mode"], "pinned" if pinned else "approved_alias")
                self.assertEqual(task["routing"]["profiles"]["bindings"]["review.deep"]["version"], 7)

    def test_public_promotion_changes_normal_entry_but_not_the_old_run_or_pin(self):
        from experiment_runtime_fixture import ExperimentRuntimeFixture
        import test_lifecycle as lifecycle_fixtures

        fake_bin = Path(self.temp.name) / "fake-bin"
        fake_bin.mkdir()
        (fake_bin / "codex").symlink_to(ROOT / "test/core/fakes/codex_review_cli.py")
        fake_home = Path(self.temp.name) / "fake-home"
        fake_home.mkdir()
        (fake_home / "auth.json").write_text("{}\n")
        (fake_home / "auth.json").chmod(0o600)
        environment = mock.patch.dict(os.environ, {"PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}", "CODEX_HOME": str(fake_home)})
        environment.start()
        self.addCleanup(environment.stop)

        baseline_task, _ = build_managed_task(
            workflow="branch-review", project_dir=self.repo,
            base_ref="HEAD", target_ref="HEAD", goal="Normal alias proof.", codex_identity=self.codex,
        )
        incumbent = copy.deepcopy(baseline_task["routing"]["profiles"]["profiles"][0])
        incumbent.update(id="approved-a", model_id="gpt-a", quality_status="proven")
        candidate = {**copy.deepcopy(incumbent), "id": "approved-b", "model_id": "gpt-b"}
        service = Service(Path(self.temp.name) / "runtime")
        template = lifecycle_fixtures.lifecycle_template(update_mode="reviewed")
        template.update(
            policy={"id": "managed-normal-entry", "version": 1},
            allowed_harnesses=["codex"], allowed_model_families=["gpt"],
            allowed_account_pools=["codex-subscription"], allowed_task_classes=["managed-review"],
        )
        store = Store(service.database, service.artifacts)
        self.addCleanup(store.close)
        store.bootstrap_profile_binding(template, incumbent, version=7)
        fixture = ExperimentRuntimeFixture(
            Path(self.temp.name) / "alias-pair", service=service, repo=self.repo,
            experiment_id="normal-alias-pair", profiles={"control": incumbent, "candidate": candidate},
            policy=baseline_task["routing"]["policy"], task_class="managed-review", native_review=True,
        )
        self.addCleanup(fixture.close)
        fixture.run_all()
        old_task, _ = build_managed_task(
            workflow="branch-review", project_dir=self.repo,
            base_ref="HEAD", target_ref="HEAD", goal="Old normal run.", codex_identity=self.codex,
            role_bindings=service.normal_entry_bindings("branch-review"),
        )
        with mock.patch.object(service, "_spawn_daemon", return_value=0):
            old = service.start(old_task, "normal-before-promotion", _internal_review_fixture={"verdict": "clean", "summary": "Offline normal review.", "findings": []})
        self.addCleanup(lambda: service.cancel(old["run_id"]))
        self.assertEqual(old["state"], "queued", old)
        old_snapshot = store.run(old["run_id"])["mutable_snapshot"]
        evaluated = service.policy_evaluate(fixture.spec)
        helper = lifecycle_fixtures.ProfileLifecycleTest()
        helper.candidate = candidate
        qualification = helper.qualification(evaluated)
        qualification["task_class"] = "managed-review"
        qualification["budget"].update(max_worker_invocations=4, worker_invocations=4)
        store.record_profile_qualification(qualification)
        store.change_profile_binding(helper.promotion("normal-promote-b"))
        self.assertEqual(store.run(old["run_id"])["mutable_snapshot"], old_snapshot)
        self.assertEqual(json.loads(old_snapshot)["routing"]["roles"]["reviewer"]["selected"]["profile_id"], "approved-a")

        for pin in (False, True):
            output = io.StringIO()
            requested = {**self.codex, "model_id": "gpt-new-default" if pin else "gpt-b"}
            argv = ["review", "--base", "HEAD", "--project-dir", str(self.repo),
                    "--runtime-dir", str(service.runtime), "--dry-run", "--json"]
            if pin:
                argv.extend(["--model", "gpt-new-default", "--effort", "low"])
            with contextlib.redirect_stdout(output), mock.patch.object(cli, "discover_codex_identity", return_value=requested) as discovery:
                self.assertEqual(cli.main(argv), 0)
            planned = json.loads(output.getvalue())["data"]["planned_roles"]["reviewer"]
            self.assertEqual(planned["model_id"], "gpt-new-default" if pin else "gpt-b")
            self.assertEqual(planned["selection_mode"], "pinned" if pin else "approved_alias")
            discovery.assert_called_once_with(self.repo.resolve(), requested_model=planned["model_id"], requested_effort="low", runtime=service.runtime)
        self.assertEqual(service.normal_entry_bindings("branch-review")["reviewer"]["version"], 8)

    def test_managed_delivery_runs_offline_through_review_checks_and_handoff(self):
        task, _ = build_managed_task(
            workflow="issue-delivery",
            project_dir=self.repo,
            base_ref="HEAD",
            target_ref="HEAD",
            goal="Change the bounded fixture value.",
            codex_identity=self.codex,
            write_paths=("src/app.py",),
            claude_model="claude-sonnet-fixture",
        )
        service = Service(Path(self.temp.name) / "runtime")
        started = service.start(
            task,
            "managed-delivery-offline",
            _internal_implementation_fixture={
                "writes": [{"path": "src/app.py", "content": "VALUE = 2\n"}],
                "delay_seconds": 0,
            },
            _internal_review_fixture={
                "verdict": "clean",
                "summary": "The exact managed candidate is clean.",
                "findings": [],
            },
        )
        deadline = time.monotonic() + 15
        resumed_review = False
        while time.monotonic() < deadline:
            status = service.status(started["run_id"])
            if status["state"] == "awaiting_host":
                break
            if (status["state"] == "queued"
                    and status["next_action"] == "resume_candidate_review"
                    and not resumed_review):
                service.resume(started["run_id"])
                resumed_review = True
            if status["state"] in {"failed", "cancelled"}:
                self.fail(f"managed delivery terminalized early: {status}")
            time.sleep(0.05)
        else:
            self.fail(f"managed delivery did not reach handoff: {status}")
        self.assertEqual(status["next_action"], "claim_handoff")
        self.assertEqual((self.repo / "src/app.py").read_text(), "VALUE = 1\n")

    def test_entry_rejects_ambiguous_scope_focus_identity_and_checks(self):
        base = {
            "workflow": "issue-delivery",
            "project_dir": self.repo,
            "base_ref": "HEAD",
            "target_ref": "HEAD",
            "goal": "Bounded issue.",
            "codex_identity": self.codex,
        }
        for writes in (("../outside",), (str(self.repo),)):
            with self.subTest(writes=writes), self.assertRaises(ContractError):
                build_managed_task(**base, write_paths=writes)
        with self.assertRaisesRegex(ContractError, "focus requires adversarial"):
            build_managed_task(**base, review_focus="security")
        with self.assertRaisesRegex(ContractError, "identity is invalid"):
            build_managed_task(**{**base, "codex_identity": {"model_id": "x"}})
        with self.assertRaises(ContractError):
            build_managed_task(**base, check_timeout=True)
        with self.assertRaisesRegex(ContractError, "cannot declare write paths"):
            build_managed_task(
                **{**base, "workflow": "branch-review"}, write_paths=("src",),
            )
        with self.assertRaisesRegex(ContractError, "invalid quoting"):
            parse_checks(['python3 -c "unterminated'])
        with self.assertRaisesRegex(ContractError, "at most 12"):
            parse_checks(["true"] * 13)

    def test_check_discovery_inspects_selected_target_not_current_checkout(self):
        repo = self._init_repo()
        (repo / "src").mkdir()
        (repo / "src/app.py").write_text("VALUE = 1\n")
        without_tests = self._commit(repo, "no tests")
        (repo / "test").mkdir()
        (repo / "test/run.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
        with_tests = self._commit(repo, "add bash tests")

        subprocess.run(
            ["git", "checkout", without_tests], cwd=repo,
            check=True, text=True, capture_output=True,
        )
        task, _ = build_managed_task(
            workflow="branch-review", project_dir=repo,
            base_ref=without_tests, target_ref=with_tests,
            goal="Target tree has tests though the checkout does not.",
            codex_identity=self.codex,
        )
        detected = next(c for c in task["checks"] if c["id"] == "detected-tests")
        self.assertEqual(detected["argv"], ["bash", "test/run.sh"])

        subprocess.run(
            ["git", "checkout", with_tests], cwd=repo,
            check=True, text=True, capture_output=True,
        )
        task, _ = build_managed_task(
            workflow="branch-review", project_dir=repo,
            base_ref=without_tests, target_ref=without_tests,
            goal="Target tree lacks tests though the checkout has them.",
            codex_identity=self.codex,
        )
        self.assertNotIn("detected-tests", [c["id"] for c in task["checks"]])

    def test_check_discovery_python_tests_follow_selected_target_not_checkout(self):
        repo = self._init_repo()
        (repo / "src").mkdir()
        (repo / "src/app.py").write_text("VALUE = 1\n")
        without_tests = self._commit(repo, "no tests tree")
        (repo / "tests").mkdir()
        (repo / "tests/test_sample.py").write_text("def test_ok():\n    assert True\n")
        with_tests = self._commit(repo, "add python tests tree")

        subprocess.run(
            ["git", "checkout", without_tests], cwd=repo,
            check=True, text=True, capture_output=True,
        )
        task, _ = build_managed_task(
            workflow="branch-review", project_dir=repo,
            base_ref=without_tests, target_ref=with_tests,
            goal="Target tree has Python tests though the checkout does not.",
            codex_identity=self.codex,
        )
        detected = next(c for c in task["checks"] if c["id"] == "detected-tests")
        self.assertEqual(
            detected["argv"], ["python3", "-m", "unittest", "discover", "-s", "tests"],
        )

        subprocess.run(
            ["git", "checkout", with_tests], cwd=repo,
            check=True, text=True, capture_output=True,
        )
        task, _ = build_managed_task(
            workflow="branch-review", project_dir=repo,
            base_ref=without_tests, target_ref=without_tests,
            goal="Target tree lacks Python tests though the checkout has them.",
            codex_identity=self.codex,
        )
        self.assertNotIn("detected-tests", [c["id"] for c in task["checks"]])

    def test_check_discovery_selected_target_without_tests(self):
        repo = self._init_repo()
        (repo / "src").mkdir()
        (repo / "src/app.py").write_text("VALUE = 1\n")
        oid = self._commit(repo, "no tests at all")
        task, _ = build_managed_task(
            workflow="branch-review", project_dir=repo,
            base_ref=oid, target_ref=oid,
            goal="No tests are tracked anywhere in the selected target.",
            codex_identity=self.codex,
        )
        self.assertEqual(
            [check["id"] for check in task["checks"]], ["candidate-diff-check"],
        )

    def test_check_discovery_detects_real_python_tests(self):
        repo = self._init_repo()
        (repo / "tests").mkdir()
        (repo / "tests/test_sample.py").write_text("def test_ok():\n    assert True\n")
        (repo / "tests/README").write_text("Docs alongside real tests.\n")
        oid = self._commit(repo, "real python tests")
        task, _ = build_managed_task(
            workflow="branch-review", project_dir=repo,
            base_ref=oid, target_ref=oid,
            goal="Real tracked test*.py files under tests/ are detected.",
            codex_identity=self.codex,
        )
        detected = next(c for c in task["checks"] if c["id"] == "detected-tests")
        self.assertEqual(
            detected["argv"], ["python3", "-m", "unittest", "discover", "-s", "tests"],
        )

    def test_check_discovery_detects_unicode_tab_and_newline_named_python_tests(self):
        names = ("test_café.py", "test\tplan.py", "test\nplan.py")
        for name in names:
            with self.subTest(name=name):
                repo = self._init_repo()
                (repo / "tests").mkdir()
                (repo / "tests" / name).write_text("def test_ok():\n    assert True\n")
                oid = self._commit(repo, "special filename test")
                task, _ = build_managed_task(
                    workflow="branch-review", project_dir=repo,
                    base_ref=oid, target_ref=oid,
                    goal="Unicode, tab, and newline named test*.py files are detected.",
                    codex_identity=self.codex,
                )
                detected = next(c for c in task["checks"] if c["id"] == "detected-tests")
                self.assertEqual(
                    detected["argv"], ["python3", "-m", "unittest", "discover", "-s", "tests"],
                )

    def test_check_discovery_detects_tests_beside_non_utf8_documentation_filename(self):
        repo = self._init_repo()
        (repo / "tests").mkdir()
        (repo / "tests/test_sample.py").write_text("def test_ok():\n    assert True\n")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
        self._stage_raw_path_blob(
            repo, b"tests/doc_\xff.md", b"Non-UTF-8 named documentation.\n",
        )
        oid = self._commit_index(repo, "python tests plus a non-utf8 doc filename")
        task, _ = build_managed_task(
            workflow="branch-review", project_dir=repo,
            base_ref=oid, target_ref=oid,
            goal="A non-UTF-8 documentation filename alongside real tests must not raise.",
            codex_identity=self.codex,
        )
        detected = next(c for c in task["checks"] if c["id"] == "detected-tests")
        self.assertEqual(
            detected["argv"], ["python3", "-m", "unittest", "discover", "-s", "tests"],
        )

    def test_check_discovery_detects_non_utf8_named_python_test_file(self):
        repo = self._init_repo()
        self._stage_raw_path_blob(
            repo, b"tests/test_\xff.py", b"def test_ok():\n    assert True\n",
        )
        oid = self._commit_index(repo, "non-utf8 named python test file")
        task, _ = build_managed_task(
            workflow="branch-review", project_dir=repo,
            base_ref=oid, target_ref=oid,
            goal="A non-UTF-8 named test*.py file must itself be detected.",
            codex_identity=self.codex,
        )
        detected = next(c for c in task["checks"] if c["id"] == "detected-tests")
        self.assertEqual(
            detected["argv"], ["python3", "-m", "unittest", "discover", "-s", "tests"],
        )

    def test_check_discovery_excludes_readme_only_and_symlinked_test_trees(self):
        repo = self._init_repo()
        (repo / "tests").mkdir()
        (repo / "tests/README").write_text("Not a test suite.\n")
        readme_only = self._commit(repo, "readme only tests dir")
        task, _ = build_managed_task(
            workflow="branch-review", project_dir=repo,
            base_ref=readme_only, target_ref=readme_only,
            goal="A tests/README must not be treated as Python tests.",
            codex_identity=self.codex,
        )
        self.assertNotIn("detected-tests", [c["id"] for c in task["checks"]])

        (repo / "tests/README").unlink()
        (repo / "tests").rmdir()
        real_dir = Path(self.temp.name) / "external-tests"
        real_dir.mkdir()
        (real_dir / "test_real.py").write_text("def test_ok():\n    assert True\n")
        (repo / "tests").symlink_to(real_dir)
        symlinked_dir = self._commit(repo, "symlinked tests dir")
        task, _ = build_managed_task(
            workflow="branch-review", project_dir=repo,
            base_ref=symlinked_dir, target_ref=symlinked_dir,
            goal="A symlinked tests directory must not be treated as Python tests.",
            codex_identity=self.codex,
        )
        self.assertNotIn("detected-tests", [c["id"] for c in task["checks"]])

    def test_check_discovery_excludes_symlinked_test_file(self):
        repo = self._init_repo()
        (repo / "tests").mkdir()
        (repo / "tests/real_test.py").write_text("def test_ok():\n    assert True\n")
        (repo / "tests/test_link.py").symlink_to("real_test.py")
        oid = self._commit(repo, "symlinked python test file")
        task, _ = build_managed_task(
            workflow="branch-review", project_dir=repo,
            base_ref=oid, target_ref=oid,
            goal="A symlinked test*.py file must not be treated as a real Python test.",
            codex_identity=self.codex,
        )
        self.assertNotIn("detected-tests", [c["id"] for c in task["checks"]])

    def test_check_discovery_requires_regular_blob_not_symlink(self):
        repo = self._init_repo()
        (repo / "test").mkdir()
        (repo / "test/test_sample.py").write_text("def test_ok():\n    assert True\n")
        (repo / "test/real.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
        (repo / "test/real.sh").chmod(0o755)
        (repo / "test/run.sh").symlink_to("real.sh")
        symlinked = self._commit(repo, "symlinked run.sh")
        task, _ = build_managed_task(
            workflow="branch-review", project_dir=repo,
            base_ref=symlinked, target_ref=symlinked,
            goal="A symlinked run.sh must not select Bash.",
            codex_identity=self.codex,
        )
        detected = next(c for c in task["checks"] if c["id"] == "detected-tests")
        self.assertEqual(
            detected["argv"], ["python3", "-m", "unittest", "discover", "-s", "test"],
        )

        (repo / "test/run.sh").unlink()
        (repo / "test/run.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
        regular = self._commit(repo, "regular run.sh")
        task, _ = build_managed_task(
            workflow="branch-review", project_dir=repo,
            base_ref=regular, target_ref=regular,
            goal="A regular blob run.sh selects Bash.",
            codex_identity=self.codex,
        )
        detected = next(c for c in task["checks"] if c["id"] == "detected-tests")
        self.assertEqual(detected["argv"], ["bash", "test/run.sh"])

    def test_check_discovery_combines_bash_and_core_runner(self):
        repo = self._init_repo()
        (repo / "test").mkdir()
        (repo / "test/run.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
        (repo / "test/core").mkdir()
        (repo / "test/core/test_sample.py").write_text("def test_ok():\n    assert True\n")
        (repo / "plugin/core/src/devsquad").mkdir(parents=True)
        (repo / "plugin/core/src/devsquad/__init__.py").write_text("")
        (repo / "scripts").mkdir()
        (repo / "scripts/run-core-tests.py").write_text("#!/usr/bin/env python3\n")
        oid = self._commit(repo, "bash plus core runner")
        task, _ = build_managed_task(
            workflow="branch-review", project_dir=repo,
            base_ref=oid, target_ref=oid,
            goal="Both Bash and the DevSquad core runner are detected.",
            codex_identity=self.codex,
        )
        self.assertEqual(
            [
                check["argv"] for check in task["checks"]
                if check["id"] in {"detected-tests", "detected-core-tests"}
            ],
            [
                ["bash", "test/run.sh"],
                [
                    "env",
                    "PYTHONPATH=plugin/core/src:test/core",
                    "PYTHONWARNINGS=error::ResourceWarning",
                    "python3",
                    "scripts/run-core-tests.py",
                ],
            ],
        )

    def test_generated_reference_gate_follows_committed_regular_target_and_deduplicates(self):
        repo = self._init_repo()
        for directory in ("test/core", "plugin/core/src/devsquad", "scripts"):
            (repo / directory).mkdir(parents=True)
        (repo / "test/run.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
        (repo / "test/core/test_sample.py").write_text("# fixture test\n")
        (repo / "plugin/core/src/devsquad/__init__.py").write_text("")
        (repo / "scripts/run-core-tests.py").write_text("# fixture runner\n")
        generator = repo / "scripts/generate-core-reference.py"
        generator.write_text("# fixture generator\n")
        target = self._commit(repo, "required project gates")
        generator.unlink()
        generator.symlink_to("run-core-tests.py")
        symlink_target = self._commit(repo, "generator becomes a symlink")
        for oid, expected in ((target, 1), (symlink_target, 0)):
            with self.subTest(target=oid):
                task, _ = build_managed_task(
                    workflow="issue-delivery", project_dir=repo, base_ref=oid,
                    target_ref=oid, goal="Keep generated contracts current.",
                    codex_identity=self.codex,
                    checks=parse_checks(["python3 scripts/generate-core-reference.py --check"] if expected else []),
                )
                references = [check for check in task["checks"] if check["argv"] == [
                    "python3", "scripts/generate-core-reference.py", "--check",
                ]]
                self.assertEqual(len(references), expected)
                self.assertTrue(all(check["required_to_pass"] for check in task["checks"]))
                self.assertIn("detected-core-tests", [check["id"] for check in task["checks"]])
                if expected:
                    self.assertEqual(references[0]["id"], "generated-core-reference")

    def test_check_discovery_deduplicates_supplied_argv_matching_detected(self):
        repo = self._init_repo()
        (repo / "test").mkdir()
        (repo / "test/run.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
        oid = self._commit(repo, "bash only")
        task, _ = build_managed_task(
            workflow="issue-delivery", project_dir=repo,
            base_ref=oid, target_ref=oid,
            goal="An exact supplied duplicate of a detected check runs once.",
            codex_identity=self.codex,
            checks=parse_checks([
                "bash test/run.sh",
                "python3 -m unittest discover -s test",
            ]),
        )
        self.assertEqual(
            [(check["id"], check["argv"]) for check in task["checks"]],
            [
                ("candidate-diff-check", ["git", "diff", "--check", oid, "HEAD", "--"]),
                ("detected-tests", ["bash", "test/run.sh"]),
                (
                    "user-check-1",
                    ["python3", "-m", "unittest", "discover", "-s", "test"],
                ),
            ],
        )
        self.assertTrue(all(check["required_to_pass"] for check in task["checks"]))

    def test_check_discovery_deduplicates_supplied_core_runner_argv(self):
        repo = self._init_repo()
        (repo / "test/core").mkdir(parents=True)
        (repo / "test/core/test_sample.py").write_text("def test_ok():\n    assert True\n")
        (repo / "plugin/core/src/devsquad").mkdir(parents=True)
        (repo / "plugin/core/src/devsquad/__init__.py").write_text("")
        (repo / "scripts").mkdir()
        (repo / "scripts/run-core-tests.py").write_text("#!/usr/bin/env python3\n")
        oid = self._commit(repo, "core runner with python tests tree")
        task, _ = build_managed_task(
            workflow="issue-delivery", project_dir=repo,
            base_ref=oid, target_ref=oid,
            goal="A supplied duplicate of the full core runner check runs once.",
            codex_identity=self.codex,
            checks=parse_checks([
                "env PYTHONPATH=plugin/core/src:test/core "
                "PYTHONWARNINGS=error::ResourceWarning python3 scripts/run-core-tests.py",
            ]),
        )
        self.assertEqual(
            [(check["id"], check["argv"]) for check in task["checks"]],
            [
                ("candidate-diff-check", ["git", "diff", "--check", oid, "HEAD", "--"]),
                (
                    "detected-tests",
                    ["python3", "-m", "unittest", "discover", "-s", "test"],
                ),
                (
                    "detected-core-tests",
                    [
                        "env",
                        "PYTHONPATH=plugin/core/src:test/core",
                        "PYTHONWARNINGS=error::ResourceWarning",
                        "python3",
                        "scripts/run-core-tests.py",
                    ],
                ),
            ],
        )

    def test_codex_discovery_selects_requested_exact_model_and_effort(self):
        manifest = mock.Mock(verified_versions=("codex-cli fixture",))
        manifest.resolve_binary.return_value = "/fixture/codex"
        process = mock.Mock(
            stdin=io.StringIO(), stdout=io.StringIO(),
        )
        process.poll.return_value = None
        models = [
            {
                "id": "gpt-default", "family": "gpt", "is_default": True,
                "supported_efforts": ["low", "medium"],
            },
            {
                "id": "gpt-requested", "family": "gpt-6", "is_default": False,
                "supported_efforts": ["high", "xhigh"],
            },
        ]
        with (
            mock.patch("devsquad.task_entry.AdapterManifest.load", return_value=manifest),
            mock.patch("devsquad.task_entry.harness_version", return_value="codex-cli fixture"),
            mock.patch("devsquad.task_entry.subprocess.Popen", return_value=process) as spawn,
            mock.patch("devsquad.task_entry.capture_probe_identity", return_value="fixture-native-start") as capture,
            mock.patch("devsquad.task_entry.close_probe") as close,
            mock.patch.dict(os.environ, {"HOME": self.temp.name, "USER": "offline-test", "PATH": "/fixture/bin", "OPENAI_API_KEY": "synthetic-denied", "ANTHROPIC_API_KEY": "synthetic-denied", "CODEX_HOME": "/fixture/alternate-home"}),
            mock.patch("devsquad.task_entry.JsonLinePeer", return_value=mock.Mock()),
            mock.patch("devsquad.task_entry.receive_response", return_value={"result": {}}),
            mock.patch("devsquad.task_entry.discover_models", return_value=[]),
            mock.patch("devsquad.task_entry.normalize_models", return_value=models),
        ):
            identity = discover_codex_identity(
                self.repo,
                requested_model="gpt-requested",
                requested_effort="xhigh",
            )
        self.assertEqual(identity, {
            "harness": "codex",
            "harness_version": "codex-cli fixture",
            "model_id": "gpt-requested",
            "model_family": "gpt-6",
            "effort": "xhigh",
        })
        capture.assert_called_once_with(process)
        close.assert_called_once_with(process, start_identity="fixture-native-start")
        self.assertEqual(spawn.call_args.kwargs["env"], {"HOME": self.temp.name, "USER": "offline-test", "PATH": "/fixture/bin"})
        self.assertTrue(spawn.call_args.kwargs["start_new_session"])

        with (
            mock.patch("devsquad.task_entry.AdapterManifest.load", return_value=manifest),
            mock.patch("devsquad.task_entry.harness_version", return_value="codex-cli fixture"),
            mock.patch("devsquad.task_entry.subprocess.Popen", return_value=process),
            mock.patch("devsquad.task_entry.capture_probe_identity", return_value="fixture-native-start"),
            mock.patch("devsquad.task_entry.close_probe"),
            mock.patch("devsquad.task_entry.JsonLinePeer", return_value=mock.Mock()),
            mock.patch("devsquad.task_entry.receive_response", return_value={"result": {}}),
            mock.patch("devsquad.task_entry.discover_models", return_value=[]),
            mock.patch("devsquad.task_entry.normalize_models", return_value=models),
        ):
            with self.assertRaisesRegex(ContractError, "effort 'ultra' is unavailable"):
                discover_codex_identity(
                    self.repo,
                    requested_model="gpt-requested",
                    requested_effort="ultra",
                )

    def test_native_discovery_does_not_read_protocol_without_owned_identity(self):
        manifest = mock.Mock(verified_versions=("codex-cli fixture",))
        manifest.resolve_binary.return_value = "/fixture/codex"
        process = mock.Mock(stdin=io.StringIO(), stdout=io.StringIO())
        with (
            mock.patch("devsquad.task_entry.AdapterManifest.load", return_value=manifest),
            mock.patch("devsquad.task_entry.harness_version", return_value="codex-cli fixture"),
            mock.patch("devsquad.task_entry.subprocess.Popen", return_value=process),
            mock.patch("devsquad.task_entry.capture_probe_identity", return_value=None),
            mock.patch("devsquad.task_entry.close_probe") as close,
            mock.patch("devsquad.task_entry.JsonLinePeer") as peer,
        ):
            with self.assertRaisesRegex(ContractError, "ownership is unavailable"):
                discover_codex_identity(self.repo)
        peer.assert_not_called()
        close.assert_called_once_with(process, start_identity=None)

    def test_native_discovery_cleans_owned_child_after_provider_parent_exits(self):
        ready = Path(self.temp.name) / "native-child.json"
        child_code = (
            "import json,os,signal,sys,time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "with open(sys.argv[1], 'w') as handle: json.dump({'pid':os.getpid()},handle)\n"
            "while True: time.sleep(1)\n"
        )
        provider_code = (
            "import json,os,subprocess,sys,time\n"
            f"subprocess.Popen([sys.executable, '-c', {child_code!r}, {str(ready)!r}], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            f"while not os.path.exists({str(ready)!r}): time.sleep(.01)\n"
            "for line in sys.stdin:\n"
            "    item=json.loads(line)\n"
            "    if item.get('method') == 'initialize':\n"
            "        print(json.dumps({'id':item['id'],'result':{}}),flush=True)\n"
            "    elif item.get('method') == 'model/list':\n"
            "        print(json.dumps({'id':item['id'],'result':{'data':[{'id':'gpt-fixture','supportedReasoningEfforts':[{'reasoningEffort':'low'}]}],'nextCursor':None}}),flush=True)\n"
            "        os._exit(0)\n"
        )
        manifest = mock.Mock(verified_versions=("codex-cli fixture",))
        manifest.resolve_binary.return_value = "/fixture/codex"
        real_popen = subprocess.Popen
        spawned = {}
        def native_popen(argv, *positional, **keywords):
            if argv[:2] != ["/fixture/codex", "app-server"]:
                return real_popen(argv, *positional, **keywords)
            process = real_popen([sys.executable, "-c", provider_code], *positional, **keywords)
            spawned["process"] = process
            return process
        from devsquad.task_entry import discover_models as real_discover_models
        def discover_then_confirm_parent_exit(*arguments, **keywords):
            models = real_discover_models(*arguments, **keywords)
            # Observe exit without wait/poll: the unreaped child reserves its
            # PID while shared cleanup owns the TERM-ignoring descendant.
            deadline = time.monotonic() + 2
            observed = ""
            while time.monotonic() < deadline:
                observed = subprocess.run(["/bin/ps", "-p", str(spawned["process"].pid), "-o", "stat="],
                                          text=True, capture_output=True, timeout=1).stdout.strip()
                if observed.startswith("Z"):
                    break
                time.sleep(0.01)
            self.assertTrue(observed.startswith("Z"), "provider parent did not exit before cleanup")
            self.assertIsNone(spawned["process"].returncode)
            return models
        try:
            with (
                mock.patch("devsquad.task_entry.AdapterManifest.load", return_value=manifest),
                mock.patch("devsquad.task_entry.harness_version", return_value="codex-cli fixture"),
                mock.patch("devsquad.task_entry.subprocess.Popen", side_effect=native_popen),
                mock.patch("devsquad.task_entry.discover_models", side_effect=discover_then_confirm_parent_exit),
            ):
                identity = discover_codex_identity(self.repo)
            self.assertEqual(identity["model_id"], "gpt-fixture")
            child = json.loads(ready.read_text())["pid"]
            observed = subprocess.run(["/bin/ps", "-p", str(child), "-o", "stat="],
                                      text=True, capture_output=True, timeout=1).stdout.strip()
            self.assertTrue(not observed or observed.startswith("Z"), f"owned child {child} survives: {observed}")
        finally:
            process = spawned.get("process")
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=2)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()

    def test_normal_native_discovery_caches_across_projects_and_ingests_shared_quota(self):
        from datetime import datetime, timedelta, timezone
        manifest = mock.Mock(verified_versions=("codex-cli fixture",))
        manifest.resolve_binary.return_value = "/fixture/codex"
        process = mock.Mock(stdin=io.StringIO(), stdout=io.StringIO())
        process.poll.return_value = None
        runtime = Path(self.temp.name) / "runtime"
        second_repo = self._init_repo()
        (second_repo / "README.md").write_text("Second project\n")
        self._commit(second_repo, "second project")
        real_popen = subprocess.Popen
        def native_popen(argv, *positional, **keywords):
            return process if argv[:2] == ["/fixture/codex", "app-server"] else real_popen(argv, *positional, **keywords)
        reset = int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp())
        account = {"type": "chatgpt", "email": "fixture@example.invalid", "planType": "plus"}
        configuration = {"config": {"provider": "native"}}
        weekly_used = 100
        failed_limits = False
        def reply(peer, request_id, **unused):
            if request_id == 1000 and failed_limits:
                raise TimeoutError("private provider quota diagnostic")
            return {"result": {1: {}, 2: {"account": account}, 3: configuration,
                1000: {"rateLimits": {"primary": {"usedPercent": 5, "windowDurationMins": 300, "resetsAt": reset},
                    "secondary": {"usedPercent": weekly_used, "windowDurationMins": 10080, "resetsAt": reset}}}}[request_id]}
        with (
            mock.patch("devsquad.task_entry.AdapterManifest.load", return_value=manifest),
            mock.patch("devsquad.task_entry.harness_version", return_value="codex-cli fixture"),
            mock.patch("devsquad.task_entry.subprocess.Popen", side_effect=native_popen),
            mock.patch("devsquad.task_entry.capture_probe_identity", return_value="fixture-native-start"),
            mock.patch("devsquad.task_entry.close_probe"),
            mock.patch("devsquad.task_entry.JsonLinePeer", return_value=mock.Mock()),
            mock.patch("devsquad.task_entry.receive_response", side_effect=reply),
            mock.patch("devsquad.task_entry.discover_models", return_value=[{"id": "gpt-fixture", "supportedReasoningEfforts": ["low"]}]) as discovery,
        ):
            first = discover_codex_identity(self.repo, runtime=runtime)
            second = discover_codex_identity(second_repo, runtime=runtime)
            discovery.assert_called_once()
            self.assertEqual(first, second)
            with mock.patch.object(Service, "_spawn_daemon") as spawn:
                for project in (self.repo, second_repo):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(cli.main(["review", "--base", "HEAD", "--project-dir", str(project), "--runtime-dir", str(runtime), "--json"]), 0)
                    started = json.loads(output.getvalue())["data"]
                    self.assertEqual(started["state"], "failed")
                    self.assertEqual(started["service"]["error"]["error"], "CAPABILITY_UNAVAILABLE")
                spawn.assert_not_called()
            service = Service(runtime)
            store = service._store()
            try:
                target = {key: first[key] for key in ("harness", "model_family", "model_id")}
                self.assertEqual(store.capacity_snapshot(first["account_pool_id"], target=target)["status"], "exhausted")
            finally:
                store.close()
            failed_limits = True
            discover_codex_identity(self.repo, runtime=runtime)
            store = service._store()
            try:
                self.assertEqual(store.capacity_snapshot(first["account_pool_id"], target=target)["status"], "exhausted")
            finally:
                store.close()
            configuration["config"]["default_model"] = "changed"
            configured = discover_codex_identity(second_repo, runtime=runtime)
            self.assertEqual(configured["account_pool_id"], first["account_pool_id"])
            self.assertNotEqual(configured["native_scope"], first["native_scope"])
            store = service._store()
            try:
                self.assertEqual(store.capacity_snapshot(configured["account_pool_id"], target=target)["status"], "exhausted")
            finally:
                store.close()
            account["email"] = "another@example.invalid"
            other = discover_codex_identity(self.repo, runtime=runtime)
            self.assertNotEqual(first["account_pool_id"], other["account_pool_id"])
            self.assertEqual(discovery.call_count, 3)
            store = service._store()
            try:
                self.assertEqual(store.capacity_snapshot(other["account_pool_id"], target=target)["status"], "unknown")
            finally:
                store.close()
            account["email"] = "fixture@example.invalid"
            weekly_used, failed_limits = 5, False
            available = discover_codex_identity(second_repo, runtime=runtime)
            store = service._store()
            try:
                self.assertEqual(store.capacity_snapshot(available["account_pool_id"], target=target)["status"], "available")
                one = store.claim_start(self.repo, "native-pool-fence-a", {"task": {}}, "fixture-a")
                two = store.claim_start(second_repo, "native-pool-fence-b", {"task": {}}, "fixture-b")
                store.reserve_pool_capacity(one.run_id, first["account_pool_id"], "qualification", target=target)
                with self.assertRaises(ConflictError):
                    store.reserve_pool_capacity(two.run_id, configured["account_pool_id"], "qualification", target=target)
                store.cancel_preparing(one.run_id)
                store.cancel_preparing(two.run_id)
            finally:
                store.close()
        self.assertNotIn("fixture@example.invalid", "".join(path.read_text() for path in (runtime / "catalogs").glob("*.json")))

    def test_normal_alias_rejects_account_or_catalog_change_without_mutating_binding(self):
        identity = {**self.codex, "account_pool_id": "native-pool", "native_scope": "scope-a", "catalog_fingerprint": "a" * 64}
        task, _ = build_managed_task(workflow="branch-review", project_dir=self.repo, base_ref="HEAD", target_ref="HEAD", goal="Bounded review", codex_identity=identity)
        incumbent = copy.deepcopy(task["routing"]["profiles"]["profiles"][0])
        incumbent["quality_status"] = "proven"
        binding = {"alias": "review.deep", "version": 7, "profile": incumbent}
        for change in ({"account_pool_id": "other-pool"}, {"native_scope": "scope-b"}, {"catalog_fingerprint": "b" * 64}):
            with self.assertRaisesRegex(ContractError, "requalification"):
                build_managed_task(workflow="branch-review", project_dir=self.repo, base_ref="HEAD", target_ref="HEAD", goal="Bounded review", codex_identity={**identity, **change}, role_bindings={"reviewer": binding})
            self.assertEqual(binding["version"], 7)


if __name__ == "__main__":
    unittest.main()
