import io
import copy
import contextlib
import json
from pathlib import Path
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
from devsquad.store import Store, canonical_json
from devsquad import cli


class ManagedTaskEntryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devsquad-task-entry-")
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

    def tearDown(self):
        self.temp.cleanup()

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
            policy=baseline_task["routing"]["policy"], task_class="managed-review",
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
            discovery.assert_called_once_with(self.repo.resolve(), requested_model=planned["model_id"], requested_effort="low")
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
            mock.patch("devsquad.task_entry.subprocess.Popen", return_value=process),
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
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=3)

        with (
            mock.patch("devsquad.task_entry.AdapterManifest.load", return_value=manifest),
            mock.patch("devsquad.task_entry.harness_version", return_value="codex-cli fixture"),
            mock.patch("devsquad.task_entry.subprocess.Popen", return_value=process),
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


if __name__ == "__main__":
    unittest.main()
