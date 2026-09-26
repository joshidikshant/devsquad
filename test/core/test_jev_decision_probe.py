from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "test" / "core" / "probes" / "jev_decision_eval.py"
SPEC = (
    ROOT
    / "docs"
    / "plans"
    / "engineering-team"
    / "experiments"
    / "jev-pilot-v1.json"
)
MODULE_SPEC = importlib.util.spec_from_file_location("jev_decision_eval", PROBE)
assert MODULE_SPEC and MODULE_SPEC.loader
jev = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(jev)


class JevDecisionProbeTest(unittest.TestCase):
    def setUp(self):
        self.spec = jev.load_spec(SPEC)
        self.request = jev.build_request(self.spec)

    def valid_response(self):
        answers = {}
        purpose_map = {item["id"]: item for item in self.spec["purposes"]}
        for case in self.spec["cases"]:
            for purpose_id, purpose in purpose_map.items():
                expected = case["expected"][purpose_id]
                labels = list(purpose["criteria"])
                remaining = (1.0 - 0.85) / (len(labels) - 1)
                probabilities = {
                    label: 0.85 if label == expected else remaining
                    for label in labels
                }
                answers[f"{case['id']}__{purpose_id}"] = {
                    "type": "choice",
                    "choice": expected,
                    "confidence": 0.8,
                    "probabilities": probabilities,
                }
        return {
            "model": "jev-1.13.0",
            "answers": answers,
            "usage": {"input_tokens": 2100, "output_tokens": 420},
        }

    def test_frozen_probe_is_one_request_and_dry_run_needs_no_key(self):
        expected_questions = len(self.spec["cases"]) * len(self.spec["purposes"])
        self.assertEqual(len(self.request["questions"]), expected_questions)
        completed = subprocess.run(
            [sys.executable, str(PROBE), "--spec", str(SPEC)],
            check=True,
            capture_output=True,
            text=True,
        )
        dry_run = json.loads(completed.stdout)
        self.assertEqual(dry_run["billable_requests"], 1)
        self.assertEqual(dry_run["retries"], 0)
        self.assertLessEqual(
            dry_run["documented_max_request_cost_usd"],
            dry_run["cost_ceiling_usd"],
        )

    def test_valid_response_is_redacted_and_scored(self):
        validated = jev.validate_response(
            self.spec, self.request, self.valid_response()
        )
        summary = jev.summarize(
            self.spec, validated, elapsed_ms=123, request_sha256="a" * 64
        )
        self.assertEqual(summary["billable_requests"], 1)
        self.assertEqual(summary["per_purpose"]["task_family"]["accuracy_percent"], 100.0)
        self.assertNotIn("Correct two spelling mistakes", json.dumps(summary))
        self.assertTrue(math.isclose(summary["pricing"]["estimated_cost_usd"], 0.0000882))

    def test_unknown_choice_and_non_finite_probability_are_rejected(self):
        response = self.valid_response()
        key = next(iter(response["answers"]))
        response["answers"][key]["choice"] = "not-a-label"
        with self.assertRaisesRegex(jev.ProbeError, "unknown choice"):
            jev.validate_response(self.spec, self.request, response)

        response = self.valid_response()
        key = next(iter(response["answers"]))
        label = next(iter(response["answers"][key]["probabilities"]))
        response["answers"][key]["probabilities"][label] = math.nan
        with self.assertRaisesRegex(jev.ProbeError, "probabilities are invalid"):
            jev.validate_response(self.spec, self.request, response)

    def test_live_mode_requires_output_before_network(self):
        completed = subprocess.run(
            [sys.executable, str(PROBE), "--spec", str(SPEC), "--execute"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("--output is required", completed.stderr)

    def test_live_result_cannot_be_written_into_the_repository(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(PROBE),
                "--spec",
                str(SPEC),
                "--execute",
                "--output",
                str(ROOT / "jev-result.json"),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("outside the Git repository", completed.stderr)


if __name__ == "__main__":
    unittest.main()
