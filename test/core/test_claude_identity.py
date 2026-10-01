"""Native Claude identity conformance without provider or authentication calls."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

CORE = Path(__file__).resolve().parents[2] / "plugin" / "core"
sys.path.insert(0, str(CORE / "src"))

from devsquad.claude_delivery_worker import (
    freeze_claude_implementer,
    run as run_claude_implementer,
)
from devsquad.contracts import ContractError


class ClaudeImplementationIdentityTest(unittest.TestCase):
    MODEL = "claude-sonnet-4-6"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.output = self.root / "provider-result.json"
        self.binary = self.root / "claude"
        self.binary.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--version\" ]; then\n"
            "  printf '%s\\n' '2.1.220 (Claude Code)'\n"
            "  exit 0\n"
            "fi\n"
            f"/bin/cat {shlex.quote(str(self.output))}\n"
        )
        self.binary.chmod(0o700)

    @staticmethod
    def model_usage():
        # Native modelUsage keys identify the model; these values are usage,
        # not a requested configuration or an effective-effort observation.
        return {
            "inputTokens": 12,
            "outputTokens": 7,
            "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 0,
            "webSearchRequests": 0,
            "costUSD": 0.0,
            "contextWindow": 200000,
            "maxOutputTokens": 64000,
        }

    def document(self):
        return {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Applied the bounded implementation.",
            "session_id": "session-identity-fixture",
            "usage": {"input_tokens": 12, "output_tokens": 7},
            "modelUsage": {self.MODEL: self.model_usage()},
        }

    def snapshot(self, model=None):
        profile = {
            "id": "claude-implementer",
            "harness": "claude",
            "model_family": "claude-sonnet",
            "model_id": model or self.MODEL,
            "effort": {"value": "high", "transport": "native"},
            "required_tools": ["read", "write"],
            "permission_policy": "workspace_write",
            "account_pool_id": "claude-subscription",
            "billing_mode": "subscription",
            "quality_status": "proven",
            "evidence_refs": ["identity-fixture"],
        }
        selected = {
            "reference": {"kind": "profile", "id": profile["id"]},
            "binding": None,
            "profile_id": profile["id"],
            "profile_sha256": hashlib.sha256(json.dumps(
                profile, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest(),
            "profile": profile,
        }
        baseline = "a" * 40
        task = {
            "schema_version": 1,
            "project": {
                "repo_path": str(self.workspace),
                "base_ref": baseline,
                "target_ref": baseline,
            },
            "workflow": "issue-delivery",
            "goal": "Implement the identity fixture.",
            "task_class": "identity-fixture",
            "acceptance": [{
                "id": "implemented",
                "description": "Implementation is complete.",
                "evidence_kind": "review",
            }],
            "checks": [],
            "scope": {"read_paths": ["src"], "write_paths": ["src"]},
            "lead": {"mode": "host"},
            "routing": {
                "profiles_file": "devsquad/profiles.json",
                "policy_file": "devsquad/policy.json",
            },
            "budget": {
                "wall_seconds": 5,
                "max_worker_invocations": 1,
                "max_revisions": 0,
                "max_fallbacks_per_step": 0,
            },
            "origin": {"surface": "test"},
        }
        with patch.dict(os.environ, {"PATH": str(self.root)}):
            adapter = freeze_claude_implementer(selected)
        return {
            "task": task,
            "delivery_workspace": {
                "path": str(self.workspace),
                "baseline_oid": baseline,
                "write_scope": task["scope"]["write_paths"],
            },
            "routing": {
                "roles": {
                    "implementer": {"selected": selected, "fallbacks": []},
                },
            },
            "implementation_adapter": adapter,
            "implementation_adapters": {profile["id"]: adapter},
        }

    def run_document(self, document, *, requested_model=None):
        self.output.write_text(json.dumps(document) + "\n")
        return run_claude_implementer(self.snapshot(requested_model))

    def test_exact_native_model_is_observed_but_effective_effort_is_unknown(self):
        evidence = self.run_document(self.document())
        attempt = evidence["attempt"]
        observed = attempt["observed_identity"]
        self.assertEqual(observed["model_id"], self.MODEL)
        self.assertEqual(observed["verification"], "verified")
        self.assertIsNone(observed["effort"])
        self.assertIsNone(observed["backing_revision"])
        self.assertEqual(
            attempt["selected_profile"]["profile"]["effort"]["value"], "high",
        )
        self.assertEqual(
            attempt["native_ids"]["session_id"], "session-identity-fixture",
        )
        self.assertEqual(attempt["usage"]["total_tokens"], 19)

    def test_unexpected_model_cannot_be_verified_as_requested_model(self):
        document = self.document()
        document["modelUsage"] = {"claude-opus-4-6": self.model_usage()}
        with self.assertRaises(ContractError):
            self.run_document(document)

    def test_missing_model_usage_cannot_be_verified_from_requested_argv(self):
        document = self.document()
        del document["modelUsage"]
        with self.assertRaises(ContractError):
            self.run_document(document)

    def test_empty_or_malformed_model_usage_cannot_verify_identity(self):
        values = [
            None, {}, [], "invalid", {"": self.model_usage()},
            {self.MODEL: None}, {self.MODEL: []}, {self.MODEL: "invalid"},
            {self.MODEL: {}},
        ]
        for value in values:
            with self.subTest(model_usage=value):
                document = self.document()
                document["modelUsage"] = value
                with self.assertRaises(ContractError):
                    self.run_document(document)

    def test_malformed_native_counters_cannot_verify_identity(self):
        for field in ("inputTokens", "outputTokens"):
            for invalid in (None, -1, True, "12", 1.5):
                with self.subTest(field=field, invalid=invalid):
                    document = self.document()
                    document["modelUsage"][self.MODEL][field] = invalid
                    with self.assertRaises(ContractError):
                        self.run_document(document)
            with self.subTest(missing=field):
                document = self.document()
                del document["modelUsage"][self.MODEL][field]
                with self.assertRaises(ContractError):
                    self.run_document(document)

    def test_multiple_model_entries_do_not_identify_a_unique_writer(self):
        document = self.document()
        document["modelUsage"]["claude-haiku-4-5-20251001"] = self.model_usage()
        with self.assertRaises(ContractError):
            self.run_document(document)

    def stream_records(self):
        document = self.document()
        document["modelUsage"]["claude-haiku-4-5-20251001"] = self.model_usage()
        return [{
            "type": "assistant", "session_id": document["session_id"],
            "parent_tool_use_id": None,
            "message": {"role": "assistant", "model": self.MODEL},
        }, document]

    def run_stream(self, records, *, requested_model=None):
        self.output.write_text("\n".join(json.dumps(item) for item in records) + "\n")
        return run_claude_implementer(self.snapshot(requested_model))

    def test_correlated_stream_identifies_writer_and_retains_auxiliary_usage(self):
        evidence = self.run_stream(self.stream_records(), requested_model="sonnet")
        attempt = evidence["attempt"]
        observed = attempt["observed_identity"]
        self.assertEqual(observed["model_id"], self.MODEL)
        self.assertEqual(observed["model_source"], "claude.stream.assistant.message.model")
        native = observed["native_evidence"]
        self.assertEqual(native["schema_version"], 2)
        self.assertEqual(native["writer_messages"], {
            "session_id": "session-identity-fixture", "models": [self.MODEL], "message_count": 1,
        })
        self.assertEqual(len(native["model_usage"]), 2)
        self.assertIsNone(observed["effort"])
        self.assertIsNone(observed["backing_revision"])

    def test_stream_cannot_guess_writer_from_usage_or_requested_settings(self):
        import copy
        original = self.stream_records()
        mutations = []
        for path, value in (
            ((0, "session_id"), "unrelated-session"),
            ((0, "parent_tool_use_id"), "delegated-tool"),
            ((0, "message", "model"), "claude-other"),
            ((0, "message", "role"), "user"),
            ((0, "message", "model"), None),
            ((1, "modelUsage"), {"claude-haiku-4-5-20251001": self.model_usage()}),
        ):
            records = copy.deepcopy(original)
            target = records
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            mutations.append(records)
        contradictory = copy.deepcopy(original[0])
        contradictory["message"]["model"] = "claude-haiku-4-5-20251001"
        mutations += [original[1:], [original[0], contradictory, original[1]],
                      [*original, original[1]], [*original, {"type": "system"}]]
        for index, records in enumerate(mutations):
            with self.subTest(case=index), self.assertRaises(ContractError):
                self.run_stream(records)

    def test_stream_session_model_proof_is_revalidated_on_import(self):
        import copy
        from devsquad.workflows import validate_implementation_evidence
        snapshot = self.snapshot()
        evidence = self.run_stream(self.stream_records())
        for field, value in (("session_id", "other"), ("models", [self.MODEL, "claude-haiku"]),
                             ("message_count", True), ("message_count", 0)):
            changed = copy.deepcopy(evidence)
            changed["attempt"]["observed_identity"]["native_evidence"]["writer_messages"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ContractError):
                validate_implementation_evidence(changed, snapshot)

    def test_native_first_party_transport_label_is_version_bound_not_a_provider_override(self):
        from devsquad.claude_identity import observed_identity
        records = self.stream_records()
        for entry in records[-1]["modelUsage"].values():
            entry["provider"] = "firstParty"
        snapshot = self.snapshot()
        observed = self.run_stream(records)["attempt"]["observed_identity"]
        self.assertEqual(observed["model_provider"], "anthropic")
        self.assertEqual(observed["native_evidence"]["model_usage"][self.MODEL]["provider"], "firstParty")
        with self.assertRaisesRegex(ContractError, "provider"):
            observed_identity(observed["native_evidence"],
                              {**snapshot["implementation_adapter"], "harness_version": "unverified-version"},
                              snapshot["routing"]["roles"]["implementer"]["selected"]["profile"])
        records[-1]["modelUsage"]["claude-haiku-4-5-20251001"]["provider"] = "other-provider"
        with self.assertRaisesRegex(ContractError, "implementation failed"):
            self.run_stream(records)

    def test_family_alias_resolves_only_to_reported_concrete_model(self):
        evidence = self.run_document(self.document(), requested_model="sonnet")
        attempt = evidence["attempt"]
        observed = attempt["observed_identity"]
        self.assertEqual(observed["model_id"], self.MODEL)
        self.assertEqual(observed["verification"], "verified")
        self.assertIsNone(observed["effort"])
        self.assertIsNone(observed["backing_revision"])
        self.assertEqual(
            attempt["selected_profile"]["profile"]["model_id"], "sonnet",
        )

    def test_alias_cannot_resolve_to_a_different_model_family(self):
        document = self.document()
        document["modelUsage"] = {"claude-opus-4-6": self.model_usage()}
        with self.assertRaises(ContractError):
            self.run_document(document, requested_model="sonnet")

    def test_supported_family_aliases_resolve_without_a_baked_in_revision(self):
        for alias, concrete in (
            ("sonnet", "claude-sonnet-4-6"),
            ("opus", "claude-opus-4-6"),
            ("haiku", "claude-haiku-4-5-20251001"),
        ):
            with self.subTest(alias=alias, concrete=concrete):
                document = self.document()
                document["modelUsage"] = {concrete: self.model_usage()}
                evidence = self.run_document(document, requested_model=alias)
                observed = evidence["attempt"]["observed_identity"]
                self.assertEqual(observed["model_id"], concrete)
                self.assertIsNone(observed["backing_revision"])

    def test_reported_alias_is_not_concrete_execution_identity(self):
        document = self.document()
        document["modelUsage"] = {"sonnet": self.model_usage()}
        with self.assertRaises(ContractError):
            self.run_document(document, requested_model="sonnet")

    def test_pricing_canonical_model_does_not_replace_native_model_key(self):
        document = self.document()
        document["modelUsage"][self.MODEL]["canonicalModel"] = "pricing-only-model"
        evidence = self.run_document(document)
        self.assertEqual(evidence["attempt"]["observed_identity"]["model_id"], self.MODEL)

    def test_pricing_canonical_model_cannot_hide_an_unexpected_native_model(self):
        document = self.document()
        usage = self.model_usage()
        usage["canonicalModel"] = self.MODEL
        document["modelUsage"] = {"unexpected-serving-model": usage}
        with self.assertRaises(ContractError):
            self.run_document(document)

    def test_top_level_model_cannot_override_native_model_usage(self):
        document = self.document()
        document["model"] = self.MODEL
        evidence = self.run_document(document)
        self.assertEqual(evidence["attempt"]["observed_identity"]["model_id"], self.MODEL)
        document["model"] = "claude-opus-4-6"
        with self.assertRaises(ContractError):
            self.run_document(document)

    def test_only_a_success_result_envelope_can_supply_identity(self):
        changes = [
            {"type": "assistant"},
            {"subtype": "error_during_execution"},
            {"is_error": True},
            {"is_error": "false"},
            {"result": ""},
            {"result": None},
        ]
        for change in changes:
            with self.subTest(change=change):
                document = {**self.document(), **change}
                with self.assertRaises(ContractError):
                    self.run_document(document)
        for missing in ("type", "subtype", "is_error", "result"):
            with self.subTest(missing=missing):
                document = self.document()
                del document[missing]
                with self.assertRaises(ContractError):
                    self.run_document(document)

    def test_missing_or_invalid_native_session_cannot_supply_identity(self):
        for session in (None, "", "   ", 42, [], "s" * 10000):
            with self.subTest(session=session):
                document = self.document()
                document["session_id"] = session
                with self.assertRaises(ContractError):
                    self.run_document(document)
        document = self.document()
        del document["session_id"]
        with self.assertRaises(ContractError):
            self.run_document(document)

    def test_non_object_native_json_fails_as_contract_error(self):
        for document in (None, [], "success", 42):
            with self.subTest(document=document):
                with self.assertRaises(ContractError):
                    self.run_document(document)


if __name__ == "__main__":
    unittest.main()
