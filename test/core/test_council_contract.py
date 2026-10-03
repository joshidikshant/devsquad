from __future__ import annotations

import unittest
import copy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugin/core/src"))

from devsquad.contracts import ContractError
from devsquad.council import digest, label_mapping, validate_spec, validate_critique, PROMPT_VERSION, output_schema, prompt_digest
from devsquad.council_runtime import validate_document


class CouncilContractTest(unittest.TestCase):
    def spec(self):
        return {"schema_version": 1, "enabled": True, "automatic": False,
                "reason": "Resolve the competing hypotheses", "min_valid_proposals": 2,
                "required_critics": 1, "max_invocations": 4, "seed": "a" * 64,
                "evidence": [], "rubric": [{"id": "correctness", "description": "Fits evidence"}]}

    def test_strict_spec_and_automatic_off(self):
        validate_spec(self.spec())
        for changes in ({"automatic": True}, {"min_valid_proposals": 1},
                        {"required_critics": 0}, {"unknown": 1}):
            with self.assertRaises(ContractError):
                validate_spec({**self.spec(), **changes})

    def test_seeded_labels_are_reversible(self):
        mapping = label_mapping(self.spec()["seed"], ["proposer_a", "proposer_b"])
        self.assertEqual(set(mapping), {"A", "B"})
        self.assertEqual(set(mapping.values()), {"proposer_a", "proposer_b"})
        self.assertEqual(mapping, label_mapping(self.spec()["seed"], ["proposer_a", "proposer_b"]))

    def test_missing_unknown_duplicate_criterion_or_label_is_invalid(self):
        good = {"summary": "Both considered", "assessments": [
            {"label": label, "criterion_id": "correctness", "status": "supported",
             "reason": "Matches the packet", "evidence_ids": []} for label in ("A", "B")],
            "objections": [{"id": "o1", "label": "B", "reason": "Unmeasured alternative", "evidence_ids": []}]}
        validate_critique(good, self.spec())
        for assessments in (good["assessments"][:1], good["assessments"] * 2,
                            [{**good["assessments"][0], "label": "C"}, good["assessments"][1]]):
            with self.assertRaises(ContractError):
                validate_critique({**good, "assessments": assessments}, self.spec())

    def test_native_import_requires_exact_frozen_identity_correlated_ids_and_usage(self):
        profile = {"id": "author", "model_id": "actual-model", "effort": {"value": "high"}}
        selected = {"profile_id": "author", "profile": profile, "profile_sha256": digest(profile)}
        adapter = {"harness": "codex", "harness_version": "codex-cli tested", "model_provider": "openai"}
        brief = {"source_files": []}
        snapshot = {"routing": {"roles": {"proposer_a": {"selected": selected, "fallbacks": []}}},
            "council_brief": brief, "council_fixture": None, "task": {"council": self.spec()},
            "council_state": {"documents": {}},
            "council_adapters": {"proposer_a": {"author": adapter}},
            "council_boundaries": {"proposer_a": {"author": {"profile_sha256": "b" * 64}}}}
        evidence = {"schema_version": 1, "role": "proposer_a", "profile": profile, "profile_sha256": digest(profile),
            "prompt_version": PROMPT_VERSION, "prompt_sha256": prompt_digest("proposer_a", {"brief": brief}),
            "role_packet_sha256": digest({"brief": brief}), "output_schema_sha256": digest(output_schema("proposer_a")),
            "brief_sha256": digest(brief), "identity_scope": "native_verified", "boundary_sha256": "b" * 64,
            "observed_identity": {**adapter, "model_id": "actual-model", "effort": "high", "permission_policy": "read_only", "verification": "verified"},
            "native_ids": {"thread_id": "observed-thread", "turn_id": "observed-turn"},
            "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None, "source": "unavailable"},
            "document": {"summary": "Proposal", "approach": "Approach", "claims": [{"text": "Claim", "evidence_ids": []}], "validation": "Check"}, "checks": []}
        attempt = {"role": "proposer_a", "profile_id": "author", "profile_index": 0}
        validate_document(evidence, snapshot, attempt)
        invalid = []
        for key, value in (("effort", "low"), ("harness_version", "other"), ("model_provider", "other"), ("permission_policy", "write"), ("model_id", "requested-only")):
            altered = copy.deepcopy(evidence)
            altered["observed_identity"][key] = value
            invalid.append(altered)
        for field, value in (("native_ids", {"thread_id": "thread"}), ("native_ids", {"thread_id": "thread", "turn_id": ""}),
                             ("usage", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "source": "unavailable"}),
                             ("observed_identity", {"verification": "verified", "model_id": "actual-model"})):
            altered = copy.deepcopy(evidence)
            altered[field] = value
            invalid.append(altered)
        for altered in invalid:
            with self.assertRaises(ContractError):
                validate_document(altered, snapshot, attempt)
