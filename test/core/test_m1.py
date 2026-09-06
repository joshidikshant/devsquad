from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

CORE = Path(__file__).resolve().parents[2] / "plugin" / "core"
import sys
sys.path.insert(0, str(CORE / "src"))

from devsquad.adapters import AdapterManifest, classify_cli, prepare_cli, prepare_native_codex
from devsquad.catalog import update_last_good
from devsquad.codex_protocol import NativeTurnState, model_list_request, parse_model_page
from devsquad.contracts import ContractError, validate_launch_payload
from devsquad.validation import validate_task


class M1ContractsTest(unittest.TestCase):
    def manifest(self, name: str) -> AdapterManifest:
        return AdapterManifest.load(CORE / "adapters" / name / "adapter.json")

    def fake_path(self, name: str) -> tuple[tempfile.TemporaryDirectory, Path]:
        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / name
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return temp, path

    def test_prepare_preserves_spaces_and_tsx_prompt(self):
        temp, binary = self.fake_path("agy")
        self.addCleanup(temp.cleanup)
        with patch.dict(os.environ, {"PATH": str(binary.parent)}):
            spec = prepare_cli(self.manifest("antigravity"), prompt="Review ui/My Card.tsx", cwd=temp.name, model="Gemini Test", effort=None, permission="read_only", timeout_seconds=9)
        self.assertIn("Review ui/My Card.tsx", spec.argv)
        self.assertIn("Gemini Test", spec.argv)
        self.assertEqual(spec.environment, {"DEVSQUAD_WORKER": "1"})

    def test_explicit_unsupported_effort_fails_before_launch(self):
        temp, binary = self.fake_path("grok")
        self.addCleanup(temp.cleanup)
        with patch.dict(os.environ, {"PATH": str(binary.parent)}):
            with self.assertRaisesRegex(ContractError, "unsupported or unverified effort"):
                prepare_cli(self.manifest("grok"), prompt="x", cwd=temp.name, model=None, effort="ultra", permission="read_only", timeout_seconds=9)

    def test_classifier_does_not_accept_empty_denied_or_malformed_exit_zero(self):
        temp, binary = self.fake_path("grok")
        self.addCleanup(temp.cleanup)
        with patch.dict(os.environ, {"PATH": str(binary.parent)}):
            spec = prepare_cli(self.manifest("grok"), prompt="x", cwd=temp.name, model=None, effort=None, permission="read_only", timeout_seconds=9)
        self.assertEqual(classify_cli(spec, returncode=0, stdout="", stderr="").execution_status, "malformed")
        self.assertEqual(classify_cli(spec, returncode=0, stdout='{"type":"result","is_error":true,"error":"tool denied"}', stderr="").execution_status, "denied")
        self.assertEqual(classify_cli(spec, returncode=0, stdout="not json", stderr="").execution_status, "malformed")

    def test_auth_precedes_rate_and_acceptance_is_separate(self):
        temp, binary = self.fake_path("grok")
        self.addCleanup(temp.cleanup)
        with patch.dict(os.environ, {"PATH": str(binary.parent)}):
            spec = prepare_cli(self.manifest("grok"), prompt="x", cwd=temp.name, model=None, effort=None, permission="read_only", timeout_seconds=9)
        result = classify_cli(spec, returncode=0, stdout='{"type":"result","is_error":true,"error":"401 quota rate limit"}', stderr="")
        self.assertEqual(result.error_code, "AUTH_ERROR")
        self.assertEqual(result.acceptance_status, "not_evaluated")
        self.assertEqual(result.artifact_status, "unknown")

    def test_valid_auth_topic_is_deliverable_and_startup_only_is_not(self):
        temp, binary = self.fake_path("grok")
        self.addCleanup(temp.cleanup)
        with patch.dict(os.environ, {"PATH": str(binary.parent)}):
            spec = prepare_cli(self.manifest("grok"), prompt="x", cwd=temp.name, model=None, effort=None, permission="read_only", timeout_seconds=9)
        valid = '{"type":"result","result":"The author explains authentication.","is_error":false}'
        self.assertEqual(classify_cli(spec, returncode=0, stdout=valid, stderr="").execution_status, "succeeded")
        startup = '{"type":"system","subtype":"init"}'
        self.assertEqual(classify_cli(spec, returncode=0, stdout=startup, stderr="").execution_status, "malformed")

    def test_codex_real_jsonl_shape_requires_agent_message(self):
        temp, binary = self.fake_path("codex"); self.addCleanup(temp.cleanup)
        with patch.dict(os.environ, {"PATH": str(binary.parent)}):
            spec = prepare_cli(self.manifest("codex"), prompt="x", cwd=temp.name, model=None, effort=None, permission="read_only", timeout_seconds=9)
        valid = '\n'.join([json.dumps({"type":"thread.started","thread_id":"x"}), json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"done"}}), json.dumps({"type":"turn.completed","usage":{"input_tokens":1}})])
        self.assertEqual(classify_cli(spec, returncode=0, stdout=valid, stderr="").execution_status, "succeeded")
        startup = json.dumps({"type":"thread.started","thread_id":"x"}) + "\n" + json.dumps({"type":"turn.completed","usage":{}})
        self.assertEqual(classify_cli(spec, returncode=0, stdout=startup, stderr="").execution_status, "malformed")

    def test_native_launch_is_preparation_only_and_version_scoped(self):
        temp, binary = self.fake_path("codex"); self.addCleanup(temp.cleanup)
        manifest = self.manifest("codex").with_model_efforts({"gpt-test": ("low",)})
        with patch.dict(os.environ, {"PATH": str(binary.parent)}):
            spec = prepare_native_codex(manifest, cwd=temp.name, model="gpt-test", effort="low", permission="read_only", timeout_seconds=9, harness_version_value="codex-cli 0.135.0")
            self.assertEqual(spec.transport, "native_protocol")
            self.assertEqual(spec.argv[-2:], ("--listen", "stdio://"))
            self.assertEqual(spec.environment["DEVSQUAD_WORKER"], "1")
            with self.assertRaises(ContractError):
                prepare_native_codex(manifest, cwd=temp.name, model="gpt-test", effort="low", permission="read_only", timeout_seconds=9, harness_version_value="codex-cli future")

    def test_launch_round_trip_is_strict(self):
        temp, binary = self.fake_path("grok"); self.addCleanup(temp.cleanup)
        with patch.dict(os.environ, {"PATH": str(binary.parent)}):
            spec = prepare_cli(self.manifest("grok"), prompt="x", cwd=temp.name, model=None, effort=None, permission="read_only", timeout_seconds=9)
        validate_launch_payload(spec.to_dict())
        invalid = spec.to_dict(); invalid["surprise"] = True
        with self.assertRaises(ContractError): validate_launch_payload(invalid)

    def test_task_examples_validate_and_unknown_fields_fail(self):
        root = Path(__file__).resolve().parents[2]
        for name in ("branch-review.json", "issue-delivery.json"):
            task = json.loads((root / "docs/plans/engineering-team/examples" / name).read_text())
            validate_task(task)
        task["unknown"] = 1
        with self.assertRaises(ContractError): validate_task(task)


class NativeProtocolTest(unittest.TestCase):
    def test_paginated_model_request_and_response(self):
        self.assertEqual(model_list_request(2, "next")["params"]["cursor"], "next")
        models, cursor = parse_model_page({"result": {"data": [{"id": "gpt-x"}], "nextCursor": "c2"}})
        self.assertEqual(models[0]["id"], "gpt-x")
        self.assertEqual(cursor, "c2")

    def test_start_and_interrupt_ack_are_not_terminal(self):
        state = NativeTurnState()
        state.consume({"method": "turn/started", "params": {"turn": {"id": "t1"}}})
        state.consume({"method": "turn/interrupt/completed", "params": {}})
        self.assertFalse(state.terminal)
        self.assertTrue(state.interrupted_acknowledged)
        state.consume({"method": "turn/interrupted", "params": {}})
        self.assertTrue(state.terminal)

    def test_disconnect_or_malformed_page_is_visible(self):
        with self.assertRaises(ContractError):
            parse_model_page({"result": {"nextCursor": "never"}})


class CatalogTest(unittest.TestCase):
    def test_incomplete_refresh_retains_last_good(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.json"
            first = update_last_good(path, harness="codex", version="1", models=[{"id": "gpt-a", "supportedReasoningEfforts": ["low"]}], complete=True)
            retained = update_last_good(path, harness="codex", version="2", models=None, complete=False, error="timeout")
            self.assertEqual(retained["models"], first["models"])
            self.assertEqual(retained["last_refresh"]["status"], "error")

    def test_discovered_model_is_unqualified_and_unknown_family_stays_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            value = update_last_good(Path(tmp) / "catalog.json", harness="codex", version="1", models=[{"id": "surprise-9"}], complete=True)
            self.assertEqual(value["models"][0]["qualification"], "unqualified")
            self.assertIsNone(value["models"][0]["family"])


if __name__ == "__main__":
    unittest.main()
