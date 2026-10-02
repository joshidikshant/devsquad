"""Normal terminal operations use real offline workers and the saved gates."""

import contextlib
import copy
import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad import cli
from devsquad import detached
from devsquad.contracts import ContractError
from devsquad.service import Service
from devsquad.store import ConflictError, Store
from devsquad.task_entry import build_managed_task


class TerminalUxTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devsquad-terminal-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.new_repo("project")
        self.service = Service(self.root / "runtime")
        self.identity = {
            "harness": "codex", "harness_version": "codex-cli fixture",
            "model_id": "gpt-fixture", "model_family": "gpt", "effort": "low",
        }

    def new_repo(self, name):
        repo = self.root / name
        repo.mkdir()
        subprocess.run(["git", "init", "-qb", "main", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
        (repo / "app.py").write_text("VALUE = 1\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
        return repo

    def invoke(self, argv):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = cli.main([*argv, "--runtime-dir", str(self.service.runtime)])
        return code, output.getvalue(), errors.getvalue()

    def normal_run(self, key="normal-review", workflow="review", check=None):
        original_start = self.service.start
        def offline_start(task, *args):
            fixtures = {"_internal_review_fixture": {
                "verdict": "clean", "summary": "Exact fixture candidate is clean.", "findings": [],
            }}
            if task["workflow"] == "issue-delivery":
                fixtures["_internal_implementation_fixture"] = {
                    "writes": [{"path": "app.py", "content": "VALUE = 2\n"}], "delay_seconds": 0,
                }
            return original_start(task, *args, **fixtures)
        argv = [workflow]
        if workflow == "fix":
            argv += ["Correct the bounded value.", "--write-path", "app.py"]
        argv += ["--base", "HEAD", "--project-dir", str(self.repo), "--idempotency-key", key, "--wait", "--json"]
        if check:
            argv += ["--check", check]
        with mock.patch.object(cli, "discover_codex_identity", return_value=self.identity), mock.patch.object(cli, "_service", return_value=self.service), mock.patch.object(self.service, "start", side_effect=offline_start):
            code, output, errors = self.invoke(argv)
        if code != 2:
            run = self.service.resolve_run_id(None, self.repo)
            result = self.service.result(run)
            output += "\n" + next(Path(item["path"]).read_text() for item in result["artifacts"] if item["name"] == "receipt.json")
        self.assertEqual((code, errors), (2, ""), output)
        data = json.loads(output)["data"]
        self.assertEqual(data["state"], "awaiting_host")
        return data["run_id"]

    def test_zero_one_multiple_and_unrelated_project_resolution(self):
        with self.assertRaisesRegex(ContractError, "No saved runs.*squad review"):
            self.service.resolve_run_id(None, self.repo)
        code, output, _ = self.invoke(["status", "--project-dir", str(self.repo)])
        self.assertEqual(code, 64)
        self.assertIn("No saved runs", output)
        unrelated = self.new_repo("unrelated")
        store = Store(self.service.database, self.service.artifacts)
        try:
            other = store.claim_start(unrelated, "other", {}, "fixture").run_id
        finally:
            store.close()
        with self.assertRaisesRegex(ContractError, "No saved runs"):
            self.service.resolve_run_id(None, self.repo)
        run = self.normal_run()
        self.assertEqual(self.service.resolve_run_id(None, self.repo), run)
        linked = self.root / "linked"
        subprocess.run(["git", "-C", str(self.repo), "worktree", "add", "-q", "--detach", str(linked), "HEAD"], check=True)
        self.assertEqual(self.service.resolve_run_id(None, linked), run)
        self.assertEqual(self.service.resolve_run_id(run, unrelated), run)
        second = self.normal_run("second")
        with self.assertRaises(ConflictError) as raised:
            self.service.resolve_run_id(None, self.repo)
        self.assertIn(run, str(raised.exception))
        self.assertIn(second, str(raised.exception))
        self.assertNotIn(other, str(raised.exception))
        code, output, _ = self.invoke(["result", "--project-dir", str(self.repo)])
        self.assertEqual(code, 75)
        self.assertIn(run, output)
        self.assertIn(second, output)
        self.assertNotIn(other, output)

    def test_normal_review_and_fix_finish_without_task_or_decision_json(self):
        for workflow in ("review", "fix"):
            with self.subTest(workflow=workflow):
                run = self.normal_run(workflow, workflow)
                code, output, errors = self.invoke(["finish", run, "--accept", "--reason", "Reviewed exact evidence."])
                self.assertEqual((code, errors), (0, ""), output)
                self.assertIn("succeeded", output)
                self.assertIn(f"squad result {run}", output)
                result = self.service.result(run)
                self.assertTrue(result["ready"])
                self.assertEqual(result["state"], "succeeded")
                with self.assertRaises(ConflictError):
                    self.service.finish(run, "accept", "No terminal replay.")
                self.assertEqual((self.repo / "app.py").read_text(), "VALUE = 1\n")

    def test_guided_finish_does_not_take_over_or_accept_failed_required_check(self):
        run = self.normal_run("failed-check", "fix", "python3 -c 'raise SystemExit(7)'")
        with self.assertRaises(ContractError):
            self.service.finish(run, "accept", "Cannot override objective failure.")
        self.assertEqual(self.service.status(run)["state"], "awaiting_host")
        self.service.handoff_claim(run, self.service.status(run)["version"], "other-owner")
        for advance in (0, 700):
            with mock.patch("devsquad.store._authoritative_now", return_value=datetime.now(timezone.utc) + timedelta(seconds=advance)):
                with self.assertRaisesRegex(ConflictError, "claim"):
                    self.service.finish(run, "reject", "Another claim owns the packet.")

    def test_reject_and_revision_exhaustion_use_the_existing_terminal_gates(self):
        rejected = self.normal_run("reject")
        decision = self.service.finish(rejected, "reject", "Review remains incomplete.")
        self.assertEqual((decision["state"], decision["disposition"]), ("failed", "reject"))
        revised = self.normal_run("no-revision-budget")
        decision = self.service.finish(revised, "revise", "Recheck the bounded review.")
        self.assertEqual((decision["state"], decision["disposition"]), ("failed", "revise"))
        self.assertTrue(self.service.result(revised)["ready"])

    def test_finish_refuses_corrupt_current_evidence_before_claiming(self):
        run = self.normal_run("corrupt-evidence")
        before = self.service.status(run)
        store = Store(self.service.database, self.service.artifacts)
        try:
            handoff = store.handoff_snapshot(run)
            artifact = store.artifact_named(run, handoff.packet["artifacts"][0]["name"])
        finally:
            store.close()
        Path(artifact["path"]).write_text("corrupt fixture evidence\n")
        with self.assertRaisesRegex(ConflictError, "corrupt"):
            self.service.finish(run, "accept", "Must verify saved evidence.")
        after = self.service.status(run)
        self.assertEqual(after["version"], before["version"])
        self.assertIsNone(after["handoff"]["claimed_by"])

    def test_status_result_omitted_ids_and_readable_default(self):
        run = self.normal_run()
        for command in ("status", "result"):
            code, output, errors = self.invoke([command, "--project-dir", str(self.repo)])
            self.assertEqual((code, errors), (0, ""), output)
            self.assertIn(run, output)
            self.assertFalse(output.startswith("{"))
        code, output, _ = self.invoke(["status", "--project-dir", str(self.repo), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["data"]["run_id"], run)

    def test_native_offline_normal_review_uses_real_discovery_and_public_finish(self):
        fake_bin = self.root / "native-bin"
        fake_bin.mkdir()
        (fake_bin / "codex").symlink_to(ROOT / "test/core/fakes/codex_review_cli.py")
        fake_home = self.root / "native-home"
        fake_home.mkdir()
        (fake_home / "auth.json").write_text("{}\n")
        (fake_home / "auth.json").chmod(0o600)
        with mock.patch.dict(os.environ, {
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "CODEX_HOME": str(fake_home),
        }):
            code, output, errors = self.invoke([
                "review", "--base", "HEAD", "--project-dir", str(self.repo),
                "--idempotency-key", "native-normal", "--wait",
            ])
        self.assertEqual((code, errors), (2, ""), output)
        self.assertIn("Review: clean", output)
        self.assertIn("Check candidate-diff-check: passed", output)
        self.assertIn("handoff.md", output)
        run = self.service.resolve_run_id(None, self.repo)
        code, output, errors = self.invoke([
            "finish", "--project-dir", str(self.repo), "--accept",
            "--reason", "Reviewed exact offline native fixture evidence.", "--json",
        ])
        self.assertEqual((code, errors), (0, ""), output)
        self.assertEqual(json.loads(output)["data"]["run_id"], run)
        self.assertTrue(self.service.result(run)["ready"])

    def test_headless_wait_continues_awaiting_host_and_queued_handoff(self):
        service = mock.Mock()
        service.status.side_effect = [
            {"run_id": "headless", "state": "awaiting_host", "version": 8, "next_action": "continue_headless_lead"},
            {"run_id": "headless", "state": "queued", "version": 9, "next_action": None},
            {"run_id": "headless", "state": "succeeded", "version": 12},
        ]
        with mock.patch.object(cli.time, "sleep"):
            response, code = cli._wait_for_run(service, {"run_id": "headless"})
        self.assertEqual((code, response["data"]["state"]), (0, "succeeded"))
        service.resume.assert_called_once_with("headless")

    def test_headless_wait_keeps_observing_after_detached_continuation_race(self):
        service = mock.Mock()
        service.status.side_effect = [
            {"run_id": "headless", "state": "awaiting_host", "version": 8, "next_action": "continue_headless_lead"},
            {"run_id": "headless", "state": "running", "version": 10},
            {"run_id": "headless", "state": "succeeded", "version": 12},
        ]
        service.resume.side_effect = ConflictError("detached owner already continued")
        with mock.patch.object(cli.time, "sleep"):
            response, code = cli._wait_for_run(service, {"run_id": "headless"})
        self.assertEqual((code, response["data"]["state"]), (0, "succeeded"))

    def test_queued_headless_handoff_is_not_a_candidate_review(self):
        task, _ = build_managed_task(
            workflow="issue-delivery", project_dir=self.repo, base_ref="HEAD",
            target_ref="HEAD", goal="Correct the bounded fixture value.",
            codex_identity=self.identity, write_paths=("app.py",),
        )
        lead = copy.deepcopy(task["routing"]["profiles"]["profiles"][-1])
        lead.update({"id": "fixture-lead", "model_id": "gpt-fixture-lead"})
        task["routing"]["profiles"]["profiles"].append(lead)
        task["routing"]["policy"]["roles"]["lead"] = [{"kind": "profile", "id": lead["id"]}]
        task["lead"] = {"mode": "headless"}
        with mock.patch.object(self.service, "_spawn_daemon"):
            started = self.service.start(
                task, "headless-status",
                _internal_implementation_fixture={"writes": [{"path": "app.py", "content": "VALUE = 2\n"}], "delay_seconds": 0},
                _internal_review_fixture={"verdict": "clean", "summary": "Exact candidate is clean.", "findings": []},
                _internal_lead_fixture={"disposition": "accept", "reason": "Exact evidence is sufficient."},
            )
        run_id = started["run_id"]
        def run_stage():
            store = Store(self.service.database, self.service.artifacts)
            try:
                run = store.run(run_id)
            finally:
                store.close()
            with mock.patch.dict(os.environ, {"PYTHONPATH": run["package_path"]}):
                self.assertEqual(detached.main([
                    "--database", str(self.service.database), "--artifacts", str(self.service.artifacts),
                    "--run-id", run_id, "--expected-version", str(run["version"]),
                    "--package-digest", run["package_digest"],
                ]), 0)
        run_stage()
        with mock.patch.object(self.service, "_spawn_daemon"):
            self.service.resume(run_id)
        with mock.patch.object(Service, "resume"):
            run_stage()
        waiting = self.service.status(run_id)
        self.assertEqual(waiting["next_action"], "continue_headless_lead")
        with self.assertRaisesRegex(ConflictError, "headless"):
            self.service.finish(run_id, "accept", "Host must not replace the selected lead.")
        with mock.patch.object(self.service, "_spawn_daemon"):
            self.service.resume(run_id)
        queued = self.service.status(run_id)
        self.assertEqual(queued["state"], "queued")
        self.assertEqual(queued["handoff"]["status"], "open")
        self.assertIsNone(queued["next_action"])
        run_stage()
        self.assertEqual(self.service.status(run_id)["state"], "succeeded")


if __name__ == "__main__":
    unittest.main()
