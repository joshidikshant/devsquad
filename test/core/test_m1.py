from __future__ import annotations

import json
import os
import stat
import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

CORE = Path(__file__).resolve().parents[2] / "plugin" / "core"
import sys
sys.path.insert(0, str(CORE / "src"))

from devsquad.adapters import AdapterManifest, classify_cli, prepare_cli, prepare_native_codex, prepare_native_codex_from_catalog
from devsquad.catalog import update_last_good
from devsquad.codex_protocol import JsonLinePeer, NativeTurnState, collect_model_pages, discover_models, initialize_request, initialized_notification, model_list_request, parse_model_page, receive_response, review_start_request, thread_start_request, turn_interrupt_request, turn_start_request
from devsquad.contracts import ContractError, validate_launch_payload
from devsquad.validation import validate_policy, validate_profile, validate_task


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
        self.assertEqual(classify_cli(spec, returncode=0, stdout="42", stderr="").execution_status, "malformed")
        nested_bad = '{"type":"item.completed","item":"bad"}\n{"type":"turn.completed"}'
        self.assertEqual(classify_cli(spec, returncode=0, stdout=nested_bad, stderr="").execution_status, "malformed")

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
        partial = json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"done"}})
        self.assertEqual(classify_cli(spec, returncode=0, stdout=partial, stderr="").execution_status, "malformed")
        startup = json.dumps({"type":"thread.started","thread_id":"x"}) + "\n" + json.dumps({"type":"turn.completed","usage":{}})
        self.assertEqual(classify_cli(spec, returncode=0, stdout=startup, stderr="").execution_status, "malformed")

    def test_native_launch_is_preparation_only_and_version_scoped(self):
        temp, binary = self.fake_path("codex"); self.addCleanup(temp.cleanup)
        manifest = self.manifest("codex").with_model_efforts({"gpt-test": ("low",)})
        with patch.dict(os.environ, {"PATH": str(binary.parent)}):
            spec = prepare_native_codex(manifest, cwd=temp.name, model="gpt-test", effort="low", permission="read_only", timeout_seconds=9, harness_version_value="codex-cli 0.135.0")
            self.assertEqual(spec.transport, "native_protocol")
            self.assertEqual(spec.argv[-2:], ("--listen", "stdio://"))
            self.assertIn('model_reasoning_effort="low"', spec.argv)
            self.assertEqual(spec.environment["DEVSQUAD_WORKER"], "1")
            with self.assertRaises(ContractError):
                prepare_native_codex(manifest, cwd=temp.name, model="gpt-test", effort="low", permission="read_only", timeout_seconds=9, harness_version_value="codex-cli future")

    def test_discovered_snapshot_feeds_native_preparation_and_rejects_drift(self):
        temp, binary = self.fake_path("codex"); self.addCleanup(temp.cleanup)
        manifest = self.manifest("codex")
        snapshot = {"complete":True,"harness":"codex","harness_version":"codex-cli 0.135.0","models":[{"id":"gpt-test","supported_efforts":["low"]}]}
        with patch.dict(os.environ, {"PATH": str(binary.parent)}):
            spec = prepare_native_codex_from_catalog(manifest, snapshot, cwd=temp.name, model="gpt-test", effort="low", permission="read_only", timeout_seconds=9, harness_version_value="codex-cli 0.135.0")
            self.assertEqual(spec.requested.model, "gpt-test")
            drifted = dict(snapshot); drifted["harness_version"] = "codex-cli future"
            with self.assertRaises(ContractError):
                prepare_native_codex_from_catalog(manifest, drifted, cwd=temp.name, model="gpt-test", effort="low", permission="read_only", timeout_seconds=9, harness_version_value="codex-cli 0.135.0")

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

    def test_nested_task_override_is_strict(self):
        root = Path(__file__).resolve().parents[2]
        task = json.loads((root / "docs/plans/engineering-team/examples/issue-delivery.json").read_text())
        task["routing"]["overrides"] = {"reviewer":{"profile_id":"x", "fallback":"anything", "unknown":True}}
        with self.assertRaises(ContractError): validate_task(task)

    def test_profile_and_policy_strict_fixtures(self):
        profile = {"id":"p1","harness":"codex","model_family":"gpt","model_id":"gpt-test","effort":{"value":"low","transport":"native"},"required_tools":["read"],"permission_policy":"read_only","account_pool_id":"codex-sub","billing_mode":"subscription","quality_status":"proven","evidence_refs":["e1"]}
        validate_profile(profile)
        broken = dict(profile); broken["surprise"] = 1
        with self.assertRaises(ContractError): validate_profile(broken)
        policy = {"schema_version":1,"id":"default","version":1,"roles":{"reviewer":[{"kind":"profile","id":"p1"}]},"task_classes":{},"require_different_model_for_review":True,"account_pools":{},"experiment_budget":{}}
        validate_policy(policy)
        policy["roles"]["reviewer"][0]["unknown"] = True
        with self.assertRaises(ContractError): validate_policy(policy)


class NativeProtocolTest(unittest.TestCase):
    def test_peer_partial_frame_times_out_and_buffered_second_frame_drains(self):
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "r"); writer = os.fdopen(write_fd, "w")
        self.addCleanup(reader.close); self.addCleanup(writer.close)
        peer = JsonLinePeer(reader, writer)
        os.write(write_fd, b'{')
        started = __import__('time').monotonic()
        with self.assertRaises(TimeoutError): peer.receive(0.05)
        self.assertLess(__import__('time').monotonic() - started, 0.2)
        os.write(write_fd, b'}\n{"n":2}\n')
        self.assertEqual(peer.receive(0.1), {})
        self.assertEqual(peer.receive(0.1), {"n":2})
    def test_fake_app_server_protocol_conformance(self):
        fake = Path(__file__).parent / "fakes/codex_app_server.py"
        process = subprocess.Popen([sys.executable, str(fake)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        def cleanup():
            if process.poll() is None: process.terminate()
            process.wait(timeout=2)
            process.stdin.close(); process.stdout.close()
        self.addCleanup(cleanup)
        peer = JsonLinePeer(process.stdout, process.stdin)
        peer.send(initialize_request(1)); self.assertIn("result", receive_response(peer, 1, timeout_seconds=2)); peer.send(initialized_notification())
        self.assertEqual([m["id"] for m in discover_models(peer, first_request_id=2, timeout_seconds=2)], ["gpt-fake"])
        peer.send(turn_start_request(4, thread_id="thread-1", prompt="p", model="gpt-fake", effort="low", cwd="/tmp", permission="read_only"))
        response = receive_response(peer, 4, timeout_seconds=2); self.assertEqual(response["result"]["turn"]["id"], "turn-1")
        state = NativeTurnState(thread_id="thread-1", turn_id="turn-1")
        for _ in range(3): state.consume(peer.receive(2))
        self.assertTrue(state.terminal); self.assertEqual("".join(state.output), "ok")
    def test_paginated_model_request_and_response(self):
        self.assertEqual(model_list_request(2, "next")["params"]["cursor"], "next")
        models, cursor = parse_model_page({"result": {"data": [{"id": "gpt-x"}], "nextCursor": "c2"}})
        self.assertEqual(models[0]["id"], "gpt-x")
        self.assertEqual(cursor, "c2")

    def test_start_and_interrupt_ack_are_not_terminal(self):
        state = NativeTurnState(thread_id="th1")
        state.consume({"method": "turn/started", "params": {"threadId": "th1", "turn": {"id": "t1", "status":"inProgress"}}})
        state.acknowledge_interrupt({"id": 4, "result": {}})
        self.assertFalse(state.terminal)
        self.assertTrue(state.interrupted_acknowledged)
        state.consume({"method": "turn/completed", "params": {"threadId":"th1", "turn":{"id":"t1", "status":"interrupted"}}})
        self.assertTrue(state.terminal)

    def test_unrelated_turn_cannot_complete_ours_and_disconnect_is_visible(self):
        state = NativeTurnState(thread_id="th1", turn_id="ours")
        state.consume({"method":"turn/completed", "params":{"threadId":"th1", "turn":{"id":"other", "status":"completed"}}})
        self.assertFalse(state.terminal)
        state.disconnected(); self.assertEqual(state.terminal_status, "transport_disconnected")

    def test_typed_native_requests_match_installed_contract(self):
        thread = thread_start_request(1, cwd="/tmp/repo", model="gpt-test", permission="read_only")
        self.assertEqual(thread["params"]["sandbox"], "read-only")
        turn = turn_start_request(2, thread_id="th", prompt="p", model="gpt-test", effort="low", cwd="/tmp/repo", permission="read_only", output_schema={"type":"object"})
        self.assertEqual(turn["params"]["sandboxPolicy"], {"type":"readOnly", "networkAccess":False})
        self.assertEqual(turn["params"]["outputSchema"]["type"], "object")
        self.assertEqual(turn_interrupt_request(3, thread_id="th", turn_id="tu")["params"]["turnId"], "tu")
        self.assertEqual(review_start_request(4, thread_id="th", target={"type":"commit", "sha":"abc"})["method"], "review/start")

    def test_disconnect_or_malformed_page_is_visible(self):
        with self.assertRaises(ContractError):
            parse_model_page({"result": {"nextCursor": "never"}})

    def test_complete_pagination_empty_page_and_repeated_cursor(self):
        pages = {None:{"result":{"data":[{"id":"a"}],"nextCursor":"c"}}, "c":{"result":{"data":[],"nextCursor":None}}}
        self.assertEqual([m["id"] for m in collect_model_pages(lambda c: pages[c])], ["a"])
        with self.assertRaises(ContractError):
            collect_model_pages(lambda c: {"result":{"data":[],"nextCursor":"same"}})


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
