"""Adversarial native evidence parsing and coordinator import validation."""

from __future__ import annotations

import copy
import hashlib
import json
import unittest
from unittest.mock import patch

import test_claude_identity as fixtures
from devsquad.claude_delivery_worker import run
from devsquad.claude_identity import (
    ClaudeResultError, failure_envelope, strict_json, validate_failure,
)
from devsquad.contracts import ContractError
from devsquad.supervisor import Supervisor
from devsquad.workflows import validate_implementation_evidence


class ImplementationIdentityEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ClaudeImplementationIdentityTest(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def evidence(self):
        self.fixture.output.write_text(json.dumps(self.fixture.document()))
        snapshot = self.fixture.snapshot()
        return snapshot, run(snapshot)

    def test_strict_native_json_rejects_duplicates_and_nonfinite_numbers(self):
        document = json.dumps(self.fixture.document())
        for payload in (
            document[:-1] + ', "session_id": "other"}',
            document[:-1] + ', "unrelated": NaN}',
            document[:-1] + ', "unrelated": Infinity}',
            document[:-1] + ', "unrelated": 1e9999}',
        ):
            with self.subTest(payload=payload[-80:]):
                self.fixture.output.write_text(payload)
                with self.assertRaises(ClaudeResultError):
                    run(self.fixture.snapshot())

    def test_optional_native_usage_metadata_is_typed_and_bounded(self):
        for field, invalid in (
            ("costUSD", -1), ("costUSD", True), ("costUSD", 10 ** 400),
            ("cacheReadInputTokens", -1), ("contextWindow", True),
            ("canonicalModel", {}), ("provider", "other-provider"),
            ("inputTokens", 2 ** 63),
        ):
            with self.subTest(field=field, invalid_type=type(invalid).__name__):
                document = self.fixture.document()
                document["modelUsage"][self.fixture.MODEL][field] = invalid
                with self.assertRaises(ContractError):
                    self.fixture.run_document(document)

    def test_missing_aggregate_usage_stays_unknown_with_valid_identity(self):
        document = self.fixture.document()
        del document["usage"]
        attempt = self.fixture.run_document(document)["attempt"]
        self.assertEqual(attempt["usage"]["source"], "unavailable")
        self.assertIsNone(attempt["usage"]["total_tokens"])
        self.assertEqual(attempt["observed_identity"]["verification_scope"], "reported_model")
        self.assertIsNone(attempt["native_model_requests"])

    def test_coordinator_rejects_tampering_before_candidate_finalization(self):
        snapshot, evidence = self.evidence()
        mutations = [
            (("schema_version",), 1),
            (("attempt", "observed_identity"), {}),
            (("attempt", "observed_identity", "model_id"), "claude-other"),
            (("attempt", "observed_identity", "effort"), "high"),
            (("attempt", "observed_identity", "backing_revision"), "invented"),
            (("attempt", "observed_identity", "verification_scope"), "everything"),
            (("attempt", "observed_identity", "model_source"), "requested_argv"),
            (("attempt", "observed_identity", "harness_version"), "different"),
            (("attempt", "observed_identity", "native_evidence"), None),
            (("attempt", "observed_identity", "native_evidence", "schema_version"), True),
            (("attempt", "observed_identity", "native_evidence", "is_error"), 0),
            (("attempt", "observed_identity", "native_evidence", "model_usage"), {}),
            (("attempt", "native_ids", "session_id"), "different-session"),
            (("attempt", "usage", "input_tokens"), 999),
        ]
        for path, value in mutations:
            with self.subTest(path=path):
                tampered = copy.deepcopy(evidence)
                target = tampered
                for field in path[:-1]:
                    target = target[field]
                target[path[-1]] = value
                with self.assertRaises(ContractError):
                    validate_implementation_evidence(tampered, snapshot)
                with patch("devsquad.supervisor.freeze_delivery_candidate") as freeze:
                    with self.assertRaises(ContractError):
                        # This import boundary validates before accessing its store.
                        Supervisor._commit_delivery_candidate(
                            object(), "unused", {}, [], {}, snapshot,
                            json.dumps(tampered).encode(),
                        )
                    freeze.assert_not_called()

    def test_native_claim_cannot_downgrade_to_fixture_evidence(self):
        snapshot, evidence = self.evidence()
        evidence["schema_version"] = 1
        evidence["attempt"]["observed_identity"] = None
        evidence["attempt"]["native_ids"] = {}
        with self.assertRaises(ContractError):
            validate_implementation_evidence(evidence, snapshot)

    def test_frozen_fallback_is_validated_against_its_own_profile(self):
        snapshot, _ = self.evidence()
        route = snapshot["routing"]["roles"]["implementer"]
        fallback = copy.deepcopy(route["selected"])
        fallback["profile"]["id"] = fallback["profile_id"] = "fallback-writer"
        fallback["reference"]["id"] = "fallback-writer"
        fallback["profile"]["model_id"] = "sonnet"
        fallback["profile_sha256"] = hashlib.sha256(json.dumps(
            fallback["profile"], sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        route["fallbacks"] = [fallback]
        snapshot["implementation_adapters"]["fallback-writer"] = snapshot["implementation_adapter"]
        launched = copy.deepcopy(snapshot)
        launched["routing"]["roles"]["implementer"]["selected"] = fallback
        evidence = run(launched)
        self.assertEqual(validate_implementation_evidence(evidence, snapshot), evidence)
        self.assertEqual(evidence["attempt"]["selected_profile"]["profile_id"], "fallback-writer")

    def test_failed_identity_keeps_only_typed_native_diagnostics(self):
        document = self.fixture.document()
        document["result"] = "private provider prose must not enter diagnostics"
        document["modelUsage"]["claude-other-model"] = {
            **self.fixture.model_usage(), "unrelated_secret": "do not retain",
        }
        with self.assertRaises(ClaudeResultError) as caught:
            self.fixture.run_document(document)
        diagnostics = caught.exception.diagnostics
        self.assertEqual(len(diagnostics["model_usage"]), 2)
        self.assertEqual(diagnostics["usage"]["total_tokens"], 19)
        self.assertNotIn("private provider prose", json.dumps(diagnostics))
        self.assertNotIn("unrelated_secret", json.dumps(diagnostics))
        self.assertNotIn("do not retain", json.dumps(diagnostics))
        value = failure_envelope(caught.exception, "a" * 64)
        self.assertEqual(validate_failure(value, "a" * 64), value)
        with self.assertRaises(ContractError):
            validate_failure(value, "b" * 64)
        for field, invalid in (("identity_status", "verified"),
                               ("output_sha256", "bad"), ("output_bytes", True)):
            altered = copy.deepcopy(value)
            altered["native_diagnostics"][field] = invalid
            with self.assertRaises(ContractError):
                validate_failure(altered, "a" * 64)

    def test_import_decoder_rejects_duplicate_native_evidence_keys(self):
        snapshot, evidence = self.evidence()
        encoded = json.dumps(evidence)
        encoded = encoded[:-1] + ', "schema_version": 2}'
        with self.assertRaises(ContractError):
            strict_json(encoded)
        with patch("devsquad.supervisor.freeze_delivery_candidate") as freeze:
            with self.assertRaises(ContractError):
                Supervisor._commit_delivery_candidate(
                    object(), "unused", {}, [], {}, snapshot, encoded.encode(),
                )
            freeze.assert_not_called()

    def test_import_must_match_actual_attempt_not_another_allowed_fallback(self):
        snapshot, evidence = self.evidence()
        route = snapshot["routing"]["roles"]["implementer"]
        primary = route["selected"]
        fallback = copy.deepcopy(primary)
        fallback["profile"]["id"] = fallback["profile_id"] = "fallback-writer"
        fallback["reference"]["id"] = "fallback-writer"
        fallback["profile_sha256"] = hashlib.sha256(json.dumps(
            fallback["profile"], sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        route["fallbacks"] = [fallback]
        snapshot["implementation_adapters"]["fallback-writer"] = snapshot["implementation_adapter"]
        evidence["attempt"]["selected_profile"] = fallback
        # It is valid evidence for this frozen fallback, but not for the actual
        # primary invocation importing it. Validate the coordinator boundary.
        validate_implementation_evidence(evidence, snapshot)
        for attempt in (
            {"profile_index": 0, "profile_id": primary["profile_id"]},
            {"profile_index": 0, "profile_id": fallback["profile_id"]},
            {"profile_index": True, "profile_id": fallback["profile_id"]},
        ):
            with self.subTest(attempt=attempt):
                with patch("devsquad.supervisor.freeze_delivery_candidate", side_effect=AssertionError("unbound evidence reached freeze")) as freeze:
                    with self.assertRaises(ContractError):
                        Supervisor._commit_delivery_candidate(
                            object(), "unused", attempt, [], {}, snapshot,
                            json.dumps(evidence).encode(),
                        )
                    freeze.assert_not_called()

    def test_invalid_utf8_stdout_retains_original_native_byte_hash(self):
        payload = b"\xff\xfe\r\n"
        self.fixture.output.write_bytes(payload)
        with self.assertRaises(ClaudeResultError) as caught:
            run(self.fixture.snapshot())
        diagnostics = caught.exception.diagnostics
        self.assertEqual(diagnostics["output_bytes"], len(payload))
        self.assertEqual(diagnostics["output_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertIsNone(diagnostics["session_id"])

    def test_failed_result_hash_preserves_crlf_native_bytes(self):
        document = self.fixture.document()
        document["modelUsage"]["claude-other-model"] = self.fixture.model_usage()
        payload = (json.dumps(document, indent=2) + "\n").replace("\n", "\r\n").encode()
        self.fixture.output.write_bytes(payload)
        with self.assertRaises(ClaudeResultError) as caught:
            run(self.fixture.snapshot())
        self.assertEqual(caught.exception.diagnostics["output_bytes"], len(payload))
        self.assertEqual(caught.exception.diagnostics["output_sha256"], hashlib.sha256(payload).hexdigest())

    def test_invalid_utf8_stderr_cannot_preempt_valid_result_parsing(self):
        content = self.fixture.binary.read_text()
        self.fixture.binary.write_text(content.replace("/bin/cat", "printf '\\377' >&2\n/bin/cat"))
        evidence = self.fixture.run_document(self.fixture.document())
        self.assertEqual(evidence["attempt"]["observed_identity"]["model_id"], self.fixture.MODEL)


if __name__ == "__main__":
    unittest.main()
