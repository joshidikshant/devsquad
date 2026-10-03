"""Pure normalized-chain tests, not public or real-run provenance proof."""

from __future__ import annotations

import copy
from datetime import timedelta
import unittest

from test_experiment_provenance import digest, spec_v2
from test_learning import NOW, experimental_final

from devsquad.contracts import ContractError
from devsquad.experiment_provenance import assignment_for
from devsquad.learning import evaluate_experiment


PROJECT_COMMON_DIR = "/tmp/provenance-project/.git"


def normalized_chains(spec):
    """Fabricate the normalized reader boundary, never saved-run authority."""
    chains = {}
    for case in spec["cases"]:
        for arm, verdict in (("control", "failed"), ("candidate", "succeeded")):
            outcome_id = case[f"{arm}_outcome_id"]
            final = experimental_final(outcome_id, verdict)
            assignment = assignment_for(
                spec, case["case_id"], arm, project_common_dir=PROJECT_COMMON_DIR,
            )
            attempt_id = f"attempt-{outcome_id}"
            chains[outcome_id] = {
                "final": final,
                "late_corrections": [],
                "provenance": {
                    "run_id": f"run-{outcome_id}",
                    "assignment": assignment,
                    "profile_sha256": assignment["profile_sha256"],
                    "execution_sha256": assignment["execution_sha256"],
                    "input_sha256": assignment["input_sha256"],
                    "case_sha256": assignment["case_sha256"],
                    "attempt_ids": [attempt_id],
                    "attempts_sha256": digest([attempt_id]),
                    "final_outcome_sha256": digest(final),
                    "correction_sha256": [],
                },
            }
    return chains


def correction_for(final):
    correction = experimental_final(f"escaped-{final['outcome_id']}", "succeeded")
    correction.update({
        "kind": "late_correction",
        "verdict": "escaped_defect",
        "corrects_outcome_id": final["outcome_id"],
        "observed_at": (NOW + timedelta(seconds=1)).isoformat(),
        "summary": "A later check found an escaped defect in this exact arm.",
    })
    return correction


class ExperimentV2EvaluationTest(unittest.TestCase):
    def setUp(self):
        self.spec = spec_v2()
        self.chains = normalized_chains(self.spec)

    def evaluate(self, chains=None):
        return evaluate_experiment(
            self.spec, self.chains if chains is None else chains,
            evaluated_at=(NOW + timedelta(seconds=2)).isoformat(),
            project_common_dir=PROJECT_COMMON_DIR,
        )

    def test_disjoint_paired_observations_produce_a_bound_proposal(self):
        result = self.evaluate()
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["verdict"], "promotion_proposal")
        self.assertFalse(result["active_policy_changed"])
        self.assertEqual(result["metrics"]["evaluation"]["available_pairs"], 2)
        self.assertEqual(result["metrics"]["held_out"]["available_pairs"], 1)
        self.assertEqual(result["metrics"]["evaluation"]["control_successes"], 0)
        self.assertEqual(result["evidence_sha256"], digest({
            outcome_id: chain["provenance"]
            for outcome_id, chain in self.chains.items()
        }))
        self.assertEqual(self.evaluate(), result)

    def test_failed_candidate_remains_visible_and_does_not_qualify(self):
        chain = self.chains["candidate-hold-1"]
        chain["final"]["verdict"] = "failed"
        chain["provenance"]["final_outcome_sha256"] = digest(chain["final"])
        result = self.evaluate()
        self.assertEqual(result["verdict"], "no_change")
        self.assertEqual(result["metrics"]["held_out"]["available_pairs"], 1)
        self.assertEqual(result["metrics"]["held_out"]["candidate_successes"], 0)
        self.assertIn({"case_id": "hold-1", "reason": "candidate_not_successful"}, result["failures"])

    def test_missing_partner_remains_visible_without_counting_a_pair(self):
        before = self.evaluate()
        for arm in ("control", "candidate"):
            with self.subTest(arm=arm):
                chains = copy.deepcopy(self.chains)
                missing_id = f"{arm}-hold-1"
                del chains[missing_id]
                result = self.evaluate(chains)
                self.assertEqual(result["verdict"], "no_change")
                self.assertEqual(result["metrics"]["held_out"]["available_pairs"], 0)
                row = next(row for row in result["cases"] if row["case_id"] == "hold-1")
                self.assertEqual(row["status"], "missing")
                self.assertEqual(row["missing"], [arm])
                self.assertIn("insufficient_held_out_pairs", result["reasons"])
                self.assertNotEqual(result["evidence_sha256"], before["evidence_sha256"])

    def test_duplicate_runs_are_rejected_even_with_a_missing_partner(self):
        for missing_partner in (False, True):
            with self.subTest(missing_partner=missing_partner):
                chains = copy.deepcopy(self.chains)
                chains["candidate-eval-2"]["provenance"]["run_id"] = chains["candidate-eval-1"]["provenance"]["run_id"]
                if missing_partner:
                    del chains["control-eval-2"]
                with self.assertRaisesRegex(ContractError, "run ids.*unique"):
                    self.evaluate(chains)

    def test_duplicate_attempts_are_rejected_even_with_a_missing_partner(self):
        for missing_partner in (False, True):
            with self.subTest(missing_partner=missing_partner):
                chains = copy.deepcopy(self.chains)
                prior = chains["candidate-eval-1"]["provenance"]
                current = chains["candidate-eval-2"]["provenance"]
                current["attempt_ids"] = list(prior["attempt_ids"])
                current["attempts_sha256"] = prior["attempts_sha256"]
                if missing_partner:
                    del chains["control-eval-2"]
                with self.assertRaisesRegex(ContractError, "attempt ids.*unique"):
                    self.evaluate(chains)

    def test_crossed_assignments_profiles_and_inputs_fail_closed(self):
        changes = [
            (("assignment", "experiment_id"), "foreign-experiment"),
            (("assignment", "spec_sha256"), "0" * 64),
            (("assignment", "project_common_dir"), "/tmp/foreign-project/.git"),
            (("assignment", "case_id"), "eval-2"),
            (("assignment", "split"), "held_out"),
            (("assignment", "arm"), "control"),
            (("assignment", "role"), "implementer"),
            (("assignment", "profile_id"), "profile-a"),
            (("profile_sha256",), self.spec["variable"]["control_profile_sha256"]),
            (("execution_sha256",), "0" * 64),
            (("input_sha256",), self.spec["cases"][1]["input_sha256"]),
            (("case_sha256",), self.spec["cases"][1]["case_sha256"]),
        ]
        for path, replacement in changes:
            with self.subTest(path=path):
                chains = copy.deepcopy(self.chains)
                target = chains["candidate-eval-1"]["provenance"]
                for field in path[:-1]:
                    target = target[field]
                target[path[-1]] = replacement
                with self.assertRaises(ContractError):
                    self.evaluate(chains)

    def test_final_outcome_bytes_must_match_the_saved_hash(self):
        chains = copy.deepcopy(self.chains)
        chains["candidate-eval-1"]["final"]["summary"] = "Changed after the witness was frozen."
        with self.assertRaisesRegex(ContractError, "final outcome hash"):
            self.evaluate(chains)

    def test_strict_late_correction_blocks_promotion_and_changes_evidence_digest(self):
        before = self.evaluate()
        chain = self.chains["candidate-hold-1"]
        correction = correction_for(chain["final"])
        chain["late_corrections"] = [correction]
        chain["provenance"]["correction_sha256"] = [digest(correction)]
        after = self.evaluate()
        self.assertEqual(after["verdict"], "no_change")
        self.assertEqual(after["metrics"]["held_out"]["candidate_escaped_defects"], 1)
        self.assertIn("candidate_escaped_defect_limit_exceeded", after["reasons"])
        self.assertIn({"case_id": "hold-1", "reason": "candidate_escaped_defect"}, after["failures"])
        self.assertNotEqual(after["evidence_sha256"], before["evidence_sha256"])

    def test_malformed_or_crossed_late_corrections_are_rejected(self):
        changes = [
            ("corrects_outcome_id", "candidate-eval-1"),
            ("selection_mode", "automatic"),
            ("observed_at", (NOW - timedelta(seconds=1)).isoformat()),
            ("evidence_refs", []),
            ("kind", "late"),
        ]
        for field, replacement in changes:
            with self.subTest(field=field):
                chains = copy.deepcopy(self.chains)
                chain = chains["candidate-hold-1"]
                correction = correction_for(chain["final"])
                correction[field] = replacement
                chain["late_corrections"] = [correction]
                chain["provenance"]["correction_sha256"] = [digest(correction)]
                with self.assertRaises(ContractError):
                    self.evaluate(chains)

    def test_correction_hash_cannot_omit_or_misrepresent_saved_correction(self):
        for invalid_hashes in ([], ["0" * 64]):
            with self.subTest(hashes=invalid_hashes):
                chains = copy.deepcopy(self.chains)
                chain = chains["candidate-hold-1"]
                chain["late_corrections"] = [correction_for(chain["final"])]
                chain["provenance"]["correction_sha256"] = invalid_hashes
                with self.assertRaises(ContractError):
                    self.evaluate(chains)


if __name__ == "__main__":
    unittest.main()
