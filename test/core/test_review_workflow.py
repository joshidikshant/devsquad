import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))

from devsquad.contracts import ContractError
from devsquad.workflows import (
    apply_lead_disposition,
    build_review_prompt,
    decode_review_document,
    evaluate_branch_review,
    make_branch_review_evidence,
    review_output_schema,
    validate_branch_review_evidence,
    validate_check_results,
    validate_review_document,
)


class BranchReviewWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.task = json.loads(
            (ROOT / "docs/plans/engineering-team/examples/branch-review.json").read_text()
        )
        self.task["project"]["repo_path"] = "/tmp/fixture-repository"
        self.workspace = {
            "candidate_sha256": "c" * 64,
            "base_oid": "a" * 40,
            "target_oid": "b" * 40,
        }
        self.review = {
            "schema_version": 1,
            "candidate_sha256": "c" * 64,
            "base_oid": "a" * 40,
            "target_oid": "b" * 40,
            "review_mode": "standard",
            "verdict": "findings",
            "summary": "One supported defect was found.",
            "findings": [{
                "id": "F-1",
                "severity": "high",
                "title": "Incorrect boundary",
                "description": "The candidate accepts one value beyond the limit.",
                "path": "src/example.py",
                "start_line": 12,
                "end_line": 13,
                "evidence": "The comparison uses <= where the contract requires <.",
            }],
        }
        self.check = self.check_result("failed", returncode=1)
        self.snapshot = {
            "task": self.task,
            "workspace": self.workspace,
            "routing": {
                "roles": {
                    "reviewer": {
                        "selected": {
                            "profile_id": "fixture-reviewer",
                            "profile_sha256": "1" * 64,
                            "profile": {"harness": "fixture"},
                            "reference": {"kind": "profile", "id": "fixture-reviewer"},
                            "binding": None,
                        },
                    },
                },
            },
        }

    def stream(self, content=""):
        encoded = content.encode()
        return {
            "preview": content,
            "captured_bytes": len(encoded),
            "total_bytes": len(encoded),
            "truncated": False,
            "full_sha256": hashlib.sha256(encoded).hexdigest(),
        }

    def test_native_output_schema_types_every_property(self):
        schema = review_output_schema()

        def visit(value):
            if "properties" in value:
                self.assertEqual(set(value["required"]), set(value["properties"]))
                self.assertFalse(value["additionalProperties"])
                for child in value["properties"].values():
                    self.assertIn("type", child)
                    visit(child)
            if isinstance(value.get("items"), dict):
                visit(value["items"])

        visit(schema)
        self.assertEqual(schema["properties"]["schema_version"]["enum"], [1])

    def check_result(self, status, *, returncode=None, error_code=None):
        configured = self.task["checks"][0]
        return {
            "schema_version": 1,
            "candidate_sha256": "c" * 64,
            "target_oid": "b" * 40,
            "id": configured["id"],
            "argv": configured["argv"],
            "cwd": configured["cwd"],
            "required_to_pass": configured["required_to_pass"],
            "status": status,
            "returncode": returncode,
            "error_code": error_code,
            "duration_ms": 12,
            "stdout": self.stream("check output\n"),
            "stderr": self.stream(),
        }

    def test_review_is_strict_and_bound_to_candidate_commits_and_mode(self):
        normalized = validate_review_document(self.review, self.task, self.workspace)
        self.assertEqual(normalized, self.review)
        for field, replacement, message in (
            ("candidate_sha256", "d" * 64, "different candidate"),
            ("base_oid", "e" * 40, "different base"),
            ("target_oid", "f" * 40, "different target"),
            ("review_mode", "adversarial", "mode does not match"),
        ):
            invalid = copy.deepcopy(self.review)
            invalid[field] = replacement
            with self.assertRaisesRegex(ContractError, message):
                validate_review_document(invalid, self.task, self.workspace)

    def test_review_rejects_unknown_duplicate_nonfinite_and_oversized_output(self):
        unknown = copy.deepcopy(self.review)
        unknown["extra"] = True
        with self.assertRaisesRegex(ContractError, "fields invalid"):
            validate_review_document(unknown, self.task, self.workspace)
        duplicate = json.dumps(self.review)[:-1] + ',"summary":"replacement"}'
        with self.assertRaisesRegex(ContractError, "duplicate key"):
            decode_review_document(duplicate, self.task, self.workspace)
        nonfinite = json.dumps(self.review).replace('"schema_version": 1', '"schema_version": NaN')
        with self.assertRaisesRegex(ContractError, "non-finite"):
            decode_review_document(nonfinite, self.task, self.workspace)
        with self.assertRaisesRegex(ContractError, "byte limit"):
            decode_review_document(b" " * (512 * 1024 + 1), self.task, self.workspace)

    def test_clean_and_findings_verdicts_cannot_contradict_payload(self):
        clean = copy.deepcopy(self.review)
        clean["verdict"], clean["findings"] = "clean", []
        self.assertEqual(
            decode_review_document(json.dumps(clean), self.task, self.workspace), clean,
        )
        for verdict, findings in (("clean", self.review["findings"]), ("findings", [])):
            invalid = copy.deepcopy(self.review)
            invalid["verdict"], invalid["findings"] = verdict, findings
            with self.assertRaisesRegex(ContractError, "verdict and findings disagree"):
                validate_review_document(invalid, self.task, self.workspace)

    def test_findings_require_actionable_canonical_locations_and_unique_ids(self):
        for field, value in (
            ("path", "../outside.py"),
            ("start_line", 0),
            ("end_line", 11),
            ("severity", "urgent"),
        ):
            invalid = copy.deepcopy(self.review)
            invalid["findings"][0][field] = value
            with self.assertRaises(ContractError):
                validate_review_document(invalid, self.task, self.workspace)
        outside_scope = copy.deepcopy(self.review)
        outside_scope["findings"][0]["path"] = "docs/unreviewed.md"
        with self.assertRaisesRegex(ContractError, "outside the declared read scope"):
            validate_review_document(outside_scope, self.task, self.workspace)
        duplicate = copy.deepcopy(self.review)
        duplicate["findings"].append(copy.deepcopy(duplicate["findings"][0]))
        with self.assertRaisesRegex(ContractError, "duplicated"):
            validate_review_document(duplicate, self.task, self.workspace)

    def test_check_results_cannot_change_host_supplied_commands_or_candidate(self):
        self.assertEqual(
            validate_check_results([self.check], self.task, self.workspace), [self.check],
        )
        for field, value in (
            ("id", "other"),
            ("argv", ["sh", "-c", "echo widened"]),
            ("cwd", "src"),
            ("required_to_pass", True),
        ):
            invalid = copy.deepcopy(self.check)
            invalid[field] = value
            with self.assertRaisesRegex(ContractError, "changes declared field"):
                validate_check_results([invalid], self.task, self.workspace)
        invalid = copy.deepcopy(self.check)
        invalid["candidate_sha256"] = "d" * 64
        with self.assertRaisesRegex(ContractError, "different candidate"):
            validate_check_results([invalid], self.task, self.workspace)

    def test_check_status_and_bounded_stream_metadata_are_consistent(self):
        passing = self.check_result("passed", returncode=0)
        timed_out = self.check_result("timed_out", error_code="TIMEOUT")
        launch_failed = self.check_result("launch_failed", error_code="CLI_ERROR")
        for result in (passing, self.check, timed_out, launch_failed):
            self.assertEqual(
                validate_check_results([result], self.task, self.workspace), [result],
            )
        invalid = copy.deepcopy(passing)
        invalid["returncode"] = 1
        with self.assertRaisesRegex(ContractError, "passing check"):
            validate_check_results([invalid], self.task, self.workspace)
        invalid = copy.deepcopy(self.check)
        invalid["stdout"]["truncated"] = True
        with self.assertRaisesRegex(ContractError, "truncation metadata"):
            validate_check_results([invalid], self.task, self.workspace)

    def test_report_only_failure_is_visible_but_does_not_block_delivery(self):
        evaluation = evaluate_branch_review(
            self.task, self.workspace, self.review, [self.check],
        )
        self.assertTrue(evaluation["accept_allowed"])
        self.assertEqual(evaluation["required_failures"], [])
        self.assertEqual(evaluation["report_only_failures"], ["fixture-tests"])
        self.assertEqual(
            [item["status"] for item in evaluation["criteria"]],
            ["evidence_available", "evidence_available"],
        )
        accepted = apply_lead_disposition(
            evaluation, "accept", revisions_used=0, max_revisions=0,
        )
        self.assertEqual(
            (accepted["action"], accepted["terminal_state"]),
            ("complete", "succeeded"),
        )

    def test_required_failure_cannot_be_overridden_by_lead_prose(self):
        required = copy.deepcopy(self.check)
        required["required_to_pass"] = True
        task = copy.deepcopy(self.task)
        task["checks"][0]["required_to_pass"] = True
        evaluation = evaluate_branch_review(task, self.workspace, self.review, [required])
        self.assertFalse(evaluation["accept_allowed"])
        self.assertEqual(evaluation["required_failures"], ["fixture-tests"])
        with self.assertRaisesRegex(ContractError, "blocked by required evidence"):
            apply_lead_disposition(
                evaluation, "accept", revisions_used=0, max_revisions=2,
            )

    def test_revise_is_bounded_and_reject_is_terminal(self):
        evaluation = evaluate_branch_review(
            self.task, self.workspace, self.review, [self.check],
        )
        revised = apply_lead_disposition(
            evaluation, "revise", revisions_used=0, max_revisions=1,
        )
        self.assertEqual(
            (revised["action"], revised["terminal_state"], revised["next_revision"]),
            ("repeat_review", None, 1),
        )
        exhausted = apply_lead_disposition(
            evaluation, "revise", revisions_used=1, max_revisions=1,
        )
        self.assertEqual(
            (exhausted["action"], exhausted["terminal_state"]),
            ("budget_exhausted", "failed"),
        )
        rejected = apply_lead_disposition(
            evaluation, "reject", revisions_used=0, max_revisions=1,
        )
        self.assertEqual(rejected["terminal_state"], "failed")

    def test_prompt_is_deterministic_read_only_and_distinguishes_adversarial_mode(self):
        first = build_review_prompt(self.task, self.workspace)
        second = build_review_prompt(copy.deepcopy(self.task), copy.deepcopy(self.workspace))
        self.assertEqual(first, second)
        self.assertIn("read-only reviewer", first)
        self.assertIn('"candidate_sha256":"' + "c" * 64 + '"', first)
        adversarial = copy.deepcopy(self.task)
        adversarial["review"] = {"mode": "adversarial", "focus": "trust boundaries"}
        prompt = build_review_prompt(adversarial, self.workspace)
        self.assertIn('"review_mode":"adversarial"', prompt)
        self.assertIn('"focus":"trust boundaries"', prompt)
        self.assertNotEqual(prompt, first)

    def test_combined_evidence_recomputes_gates_profile_and_accounting(self):
        evidence = make_branch_review_evidence(
            self.snapshot, self.review, [self.check],
        )
        self.assertEqual(
            validate_branch_review_evidence(evidence, self.snapshot), evidence,
        )
        for mutate, message in (
            (lambda value: value["evaluation"].__setitem__("accept_allowed", False),
             "derived gates"),
            (lambda value: value["attempt"].__setitem__(
                "selected_profile", {"profile_id": "substituted"}),
             "frozen fallback set"),
            (lambda value: value["attempt"].__setitem__("worker_invocations", 2),
             "invocation accounting"),
            (lambda value: value["attempt"]["usage"].__setitem__("total_tokens", 0),
             "cannot invent token counts"),
        ):
            invalid = copy.deepcopy(evidence)
            mutate(invalid)
            with self.assertRaisesRegex(ContractError, message):
                validate_branch_review_evidence(invalid, self.snapshot)


if __name__ == "__main__":
    unittest.main()
