"""Candidate-integrity gates exercised through durable public service runs."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.contracts import ContractError
from devsquad.service import Service
from devsquad.store import ConflictError, Store, request_hash
import test_delivery_workflow as delivery_fixtures


class PublicCheckIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="devsquad-check-integrity-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.runtime = self.root / "runtime"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        for name in ("src", "tests", "devsquad"):
            (self.repo / name).mkdir()
        (self.repo / "src/app.py").write_text("VALUE = 'base'\n")
        (self.repo / "tests/test_app.py").write_text("# fixture\n")
        profiles, policy = delivery_fixtures.DeliveryWorkspaceTest.delivery_routing_documents()
        policy["task_classes"]["fixture-review-small"] = "proven"
        for name, document in (("profiles", profiles), ("policy", policy)):
            (self.repo / "devsquad" / f"{name}.json").write_text(json.dumps(document))
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.baseline = self.git("rev-parse", "HEAD").strip()
        (self.repo / "src/app.py").write_text("VALUE = 'wrong'\n")
        self.git("add", "src/app.py")
        self.git("commit", "-qm", "candidate needing a fix")
        self.target = self.git("rev-parse", "HEAD").strip()
        self.original = {
            "head": self.target,
            "index": self.git("write-tree"),
            "status": self.git("status", "--porcelain"),
            "refs": self.git("show-ref"),
            "source": (self.repo / "src/app.py").read_bytes(),
        }
        self.service = Service(self.runtime)
        self.run_ids = []
        self.addCleanup(self.cancel_unfinished_runs)

    def git(self, *arguments):
        return subprocess.run(
            ["git", "-C", str(self.repo), *arguments], check=True,
            text=True, capture_output=True,
        ).stdout

    def cancel_unfinished_runs(self):
        for run_id in self.run_ids:
            if self.service.status(run_id)["state"] not in {"succeeded", "failed", "cancelled"}:
                self.service.cancel(run_id)

    def task(self, workflow, mode, checks):
        task = json.loads(
            (ROOT / "docs/plans/engineering-team/examples/branch-review.json").read_text()
        )
        task["project"] = {
            "repo_path": str(self.repo), "base_ref": self.baseline,
            "target_ref": self.target if workflow == "branch-review" else self.baseline,
        }
        task["workflow"] = workflow
        task["lead"] = {"mode": mode}
        task["checks"] = checks
        task["budget"] = {
            "wall_seconds": 60, "max_worker_invocations": 3,
            "max_revisions": 0, "max_fallbacks_per_step": 0,
        }
        if workflow == "issue-delivery":
            task["task_class"] = "fixture-delivery-small"
            task["scope"]["write_paths"] = ["src/app.py"]
        return task

    @staticmethod
    def check(check_id, script, *arguments, required=False, output_paths=None):
        result = {
            "id": check_id,
            "argv": [sys.executable, "-c", script, *arguments],
            "cwd": ".", "timeout_seconds": 10, "required_to_pass": required,
        }
        if output_paths is not None:
            result["output_paths"] = output_paths
        return result

    def wait_state(self, run_id, states):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status = self.service.status(run_id)
            if status["state"] in states:
                return status
            time.sleep(0.05)
        log = self.runtime / "private-logs" / f"{run_id}.supervisor.log"
        self.fail(
            f"run did not reach {states}: {self.service.status(run_id)}\n"
            f"{log.read_text() if log.exists() else 'no supervisor log'}"
        )

    def start(self, workflow, mode, checks):
        options = {
            "_internal_review_fixture": {
                "verdict": "clean", "summary": "Fixture review of the exact candidate.",
                "findings": [],
            },
        }
        if workflow == "issue-delivery":
            options["_internal_implementation_fixture"] = {
                "writes": [{"path": "src/app.py", "content": "VALUE = 'wrong'\n"}],
                "delay_seconds": 0,
            }
        if mode == "headless":
            options["_internal_lead_fixture"] = {
                "disposition": "accept", "reason": "Attempt to accept the saved evidence.",
            }
        started = self.service.start(
            self.task(workflow, mode, checks), f"integrity-{workflow}-{mode}", **options,
        )
        run_id = started["run_id"]
        self.run_ids.append(run_id)
        self.assertTrue(started["created"])
        if workflow == "issue-delivery":
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                status = self.service.status(run_id)
                if status["state"] == "queued" and status.get("next_action") == "resume_candidate_review":
                    break
                self.assertNotIn(status["state"], {"failed", "cancelled"}, status)
                time.sleep(0.05)
            else:
                self.fail(f"delivery candidate was not frozen: {self.service.status(run_id)}")
            self.assertTrue(self.service.resume(run_id)["launched"])
        return run_id

    @staticmethod
    def decision(packet, submission_id, disposition):
        body = {
            "schema_version": 1, "submission_id": submission_id,
            "disposition": disposition, "reason": "Exercise the candidate integrity gate.",
            "evidence_refs": [
                {"artifact_id": item["artifact_id"], "sha256": item["sha256"]}
                for item in packet["artifacts"]
            ],
        }
        return {**body, "submission_hash": request_hash(body)}

    def receipt(self, run_id):
        artifact = next(
            item for item in self.service.result(run_id)["artifacts"]
            if item["name"] == "receipt.json"
        )
        return json.loads(Path(artifact["path"]).read_text())

    def snapshot(self, run_id):
        store = Store(self.service.database, self.service.artifacts)
        try:
            return json.loads(store.run(run_id)["mutable_snapshot"])
        finally:
            store.close()

    def assert_original_unchanged(self):
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.original["head"])
        self.assertEqual(self.git("write-tree"), self.original["index"])
        self.assertEqual(self.git("status", "--porcelain"), self.original["status"])
        self.assertEqual(self.git("show-ref"), self.original["refs"])
        self.assertEqual((self.repo / "src/app.py").read_bytes(), self.original["source"])

    def exercise_mutation(self, workflow, mode, *, add_source=False):
        second_marker = self.root / "second-check-ran"
        mutated_path = "src/helper.py" if add_source else "src/app.py"
        expected_output = {
            "before": None if add_source else "VALUE = 'wrong'\n",
            "after": "VALUE = 'fixed'\n",
        }
        checks = [
            self.check(
                "mutating-report-only-check",
                "from pathlib import Path; import json,sys; p=Path(sys.argv[1]); "
                "before=p.read_text() if p.exists() else None; "
                "p.write_text(\"VALUE = 'fixed'\\n\"); "
                "print(json.dumps({'before':before,'after':p.read_text()}))",
                mutated_path,
            ),
            self.check(
                "must-not-use-contaminated-source",
                "from pathlib import Path; import sys; "
                "Path(sys.argv[1]).write_text(Path(sys.argv[2]).read_text())",
                str(second_marker), mutated_path,
            ),
        ]
        run_id = self.start(workflow, mode, checks)
        status = self.wait_state(run_id, {"awaiting_host", "succeeded", "failed"})
        if mode == "host":
            self.assertEqual(status["state"], "awaiting_host", status)
            claimed = self.service.handoff_claim(run_id, status["version"], "integrity-host")
            evidence = claimed["handoff"]["packet"]
        else:
            evidence = self.receipt(run_id)
            self.assertEqual(status["state"], "failed", "headless lead accepted changed source")

        # A successful shell exit is not a successful check of the frozen source.
        self.assertIsInstance(evidence["evaluation"], dict, evidence)
        self.assertFalse(evidence["evaluation"]["accept_allowed"])
        self.assertFalse(second_marker.exists(), "later check ran on mutated candidate source")
        self.assertEqual(evidence["checks"][0]["status"], "invalidated")
        self.assertEqual(evidence["checks"][0]["returncode"], 0)
        self.assertEqual(evidence["checks"][0]["integrity"]["status"], "violated")
        integrity = evidence["checks"][0]["integrity"]
        self.assertNotEqual(integrity["before_state_sha256"], integrity["after_state_sha256"])
        self.assertEqual(integrity["changes"][0]["path"], mutated_path)
        self.assertNotEqual(integrity["changes"][0]["before"], integrity["changes"][0]["after"])
        self.assertEqual(evidence["checks"][1]["status"], "not_run")
        self.assertIsNone(evidence["checks"][1]["returncode"])
        self.assertEqual(evidence["checks"][1]["integrity"]["status"], "not_run")
        output = json.loads(evidence["checks"][0]["stdout"]["preview"])
        self.assertEqual(output, expected_output)
        snapshot = self.snapshot(run_id)
        candidate_oid = snapshot["workspace"]["target_oid"]
        self.assertEqual(self.git("show", f"{candidate_oid}:src/app.py"), "VALUE = 'wrong'\n")
        if add_source:
            self.assertEqual(self.git("ls-tree", candidate_oid, "--", mutated_path), "")
        self.assertEqual(
            (Path(snapshot["check_workspace"]["path"]) / mutated_path).read_text(),
            "VALUE = 'fixed'\n",
        )

        # A new service instance must replay the invalid evidence, not rerun it
        # against the altered tree or lose the gate when recovering a handoff.
        self.service = Service(self.runtime)
        with self.assertRaises(ConflictError):
            self.service.resume(run_id)
        if mode == "host":
            with self.assertRaisesRegex(ContractError, "blocked by required evidence"):
                self.service.handoff_complete(
                    run_id, claimed["claim"], self.decision(evidence, "invalid-accept", "accept"),
                )
            terminal = self.service.handoff_complete(
                run_id, claimed["claim"], self.decision(evidence, "reject-mutation", "reject"),
            )
            self.assertEqual(terminal["state"], "failed")
        receipt = self.receipt(run_id)
        self.assertFalse(receipt["evaluation"]["accept_allowed"])
        self.assertEqual(receipt["checks"], evidence["checks"])
        with self.assertRaises(ConflictError):
            self.service.resume(run_id)
        self.assertEqual(self.service.status(run_id)["state"], "failed")
        self.assertFalse(second_marker.exists())
        self.assert_original_unchanged()

    def test_branch_review_host_rejects_source_mutating_report_only_check(self):
        self.exercise_mutation("branch-review", "host")

    def test_branch_review_headless_rejects_source_mutating_report_only_check(self):
        self.exercise_mutation("branch-review", "headless")

    def test_delivery_host_rejects_source_mutating_report_only_check(self):
        self.exercise_mutation("issue-delivery", "host")

    def test_delivery_headless_rejects_source_mutating_report_only_check(self):
        self.exercise_mutation("issue-delivery", "headless")

    def test_branch_review_host_rejects_new_untracked_source_input(self):
        self.exercise_mutation("branch-review", "host", add_source=True)

    def test_delivery_host_rejects_new_untracked_source_input(self):
        self.exercise_mutation("issue-delivery", "host", add_source=True)

    def exercise_build_output(self, workflow):
        checks = [
            self.check(
                "ordinary-build-output",
                "from pathlib import Path; Path('tests/build-output.bin').write_bytes(b'build')",
                required=True,
                output_paths=["tests/build-output.bin"],
            ),
            self.check(
                "clean-source-and-build-output",
                "from pathlib import Path; "
                "assert Path('src/app.py').read_text() == \"VALUE = 'wrong'\\n\"; "
                "assert Path('tests/build-output.bin').read_bytes() == b'build'",
                required=True,
            ),
        ]
        run_id = self.start(workflow, "headless", checks)
        self.assertEqual(self.wait_state(run_id, {"succeeded", "failed"})["state"], "succeeded")
        receipt = self.receipt(run_id)
        self.assertTrue(receipt["evaluation"]["accept_allowed"])
        self.assertEqual([check["status"] for check in receipt["checks"]], ["passed", "passed"])
        self.assert_original_unchanged()

    def test_branch_review_permits_untracked_build_output_and_clean_checks(self):
        self.exercise_build_output("branch-review")

    def test_delivery_permits_untracked_build_output_and_clean_checks(self):
        self.exercise_build_output("issue-delivery")

    def test_terminal_completion_replay_preserves_historical_acceptance(self):
        run_id = self.start("branch-review", "host", [self.check("clean", "print('ok')")])
        status = self.wait_state(run_id, {"awaiting_host", "failed"})
        self.assertEqual(status["state"], "awaiting_host")
        claimed = self.service.handoff_claim(run_id, status["version"], "replay-host")
        decision = self.decision(claimed["handoff"]["packet"], "accept-once", "accept")
        first = self.service.handoff_complete(run_id, claimed["claim"], decision)
        self.assertEqual(first["state"], "succeeded")
        receipt = self.receipt(run_id)
        with patch("devsquad.service.require_check_integrity", side_effect=ContractError("legacy check")) as gate:
            replay = self.service.handoff_complete(run_id, claimed["claim"], decision)
            gate.assert_not_called()
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.receipt(run_id), receipt)
        conflicting = self.decision(claimed["handoff"]["packet"], "accept-once", "reject")
        with self.assertRaises(ConflictError):
            self.service.handoff_complete(run_id, claimed["claim"], conflicting)


if __name__ == "__main__":
    unittest.main()
