from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


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


class JevEnvFileTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.env_file = Path(self.temporary.name) / ".env"
        environment = mock.patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def write_env(self, text):
        self.env_file.write_text(text, encoding="utf-8")
        self.env_file.chmod(0o600)

    def test_explicit_env_file_supports_plain_and_quoted_key(self):
        for declaration in (
            "TYPESAFE_API_KEY=fake-test-key",
            "TYPESAFE_API_KEY='fake-test-key'",
            'export TYPESAFE_API_KEY="fake-test-key"',
        ):
            with self.subTest(declaration=declaration):
                self.write_env("# Local credentials\nUNRELATED=ignored\n" + declaration + "\n")
                self.assertEqual(jev.load_api_key(self.env_file), "fake-test-key")
                self.assertNotIn("TYPESAFE_API_KEY", os.environ)

    def test_exported_key_wins_without_reading_env_file(self):
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "exported-test-key"}):
            self.assertEqual(jev.load_api_key(self.env_file), "exported-test-key")

    def test_no_implicit_env_loading_and_blank_template_has_no_key(self):
        self.write_env("TYPESAFE_API_KEY=local-test-key\n")
        with mock.patch.object(jev.os, "open", side_effect=AssertionError("implicit read")):
            self.assertIsNone(jev.load_api_key())
        self.write_env("TYPESAFE_API_KEY=\n")
        self.assertIsNone(jev.load_api_key(self.env_file))

    def test_values_are_literal_not_shell_expanded(self):
        self.write_env("TYPESAFE_API_KEY='$UNDEFINED_KEY'\n")
        self.assertEqual(jev.load_api_key(self.env_file), "$UNDEFINED_KEY")

    def test_malformed_and_duplicate_key_errors_never_echo_contents(self):
        for contents in (
            "TYPESAFE_API_KEY='private-test-secret\n",
            "TYPESAFE_API_KEY=private-test-secret with-spaces\n",
            "TYPESAFE_API_KEY=private-test-secret\nTYPESAFE_API_KEY=second-key\n",
        ):
            with self.subTest():
                self.write_env(contents)
                with self.assertRaises(jev.ProbeError) as caught:
                    jev.load_api_key(self.env_file)
                self.assertNotIn("private-test-secret", str(caught.exception))

    def test_missing_insecure_symlink_and_oversized_files_fail_safely(self):
        with self.assertRaises(jev.ProbeError):
            jev.load_api_key(self.env_file)
        self.write_env("TYPESAFE_API_KEY=private-test-secret\n")
        self.env_file.chmod(0o644)
        with self.assertRaisesRegex(jev.ProbeError, "permissions"):
            jev.load_api_key(self.env_file)
        self.env_file.chmod(0o600)
        link = Path(self.temporary.name) / "linked.env"
        link.symlink_to(self.env_file)
        with self.assertRaises(jev.ProbeError):
            jev.load_api_key(link)
        self.write_env("#" * (jev.MAX_ENV_BYTES + 1))
        with self.assertRaisesRegex(jev.ProbeError, "size"):
            jev.load_api_key(self.env_file)

    def test_env_file_dry_run_only_reports_presence_and_never_calls_api(self):
        for value, configured in (("", False), ("private-test-secret", True)):
            with self.subTest(configured=configured):
                self.write_env(f"TYPESAFE_API_KEY={value}\n")
                stdout, stderr = io.StringIO(), io.StringIO()
                with mock.patch.object(jev, "urlopen") as network, \
                        contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    result = jev.main(["--env-file", str(self.env_file)])
                self.assertEqual(result, 0, stderr.getvalue())
                self.assertEqual(json.loads(stdout.getvalue())["api_key_configured"], configured)
                self.assertNotIn("private-test-secret", stdout.getvalue() + stderr.getvalue())
                network.assert_not_called()

    def test_blank_key_blocks_execution_before_network(self):
        self.write_env("TYPESAFE_API_KEY=\n")
        spec = jev.load_spec(SPEC)
        with mock.patch.object(jev, "urlopen") as network:
            with self.assertRaisesRegex(jev.ProbeError, "not set"):
                jev.execute(spec, jev.build_request(spec), env_file=self.env_file)
            network.assert_not_called()

    def test_explicit_key_is_used_for_exactly_one_mocked_request(self):
        self.write_env("TYPESAFE_API_KEY=private-test-secret\n")
        spec = jev.load_spec(SPEC)
        fixture = JevDecisionProbeTest()
        fixture.setUp()
        response = io.BytesIO(json.dumps(fixture.valid_response()).encode())
        with mock.patch.object(jev, "urlopen", return_value=response) as network:
            result = jev.execute(spec, jev.build_request(spec), env_file=self.env_file)
        network.assert_called_once()
        request = network.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer private-test-secret")
        self.assertNotIn("private-test-secret", json.dumps(result))
        self.assertNotIn("TYPESAFE_API_KEY", os.environ)


if __name__ == "__main__":
    unittest.main()
