"""Reject experiment sample inflation through reused outcome observations."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from test_learning import NOW, experiment, experimental_final

from devsquad.contracts import ContractError
from devsquad.learning import evaluate_experiment, validate_experiment
from devsquad.store import Store


def repeated_pair_spec(project_path: Path) -> dict:
    """The original regression: two observations relabelled as three pairs."""
    spec = experiment(project_path)
    control_id = spec["cases"][0]["control_outcome_id"]
    candidate_id = spec["cases"][0]["candidate_outcome_id"]
    for case in spec["cases"]:
        case["control_outcome_id"] = control_id
        case["candidate_outcome_id"] = candidate_id
    return spec


def outcome_chains(spec: dict) -> dict:
    chains = {}
    for case in spec["cases"]:
        for arm, verdict in (("control", "failed"), ("candidate", "succeeded")):
            outcome_id = case[f"{arm}_outcome_id"]
            chains.setdefault(outcome_id, {
                "final": experimental_final(outcome_id, verdict),
                "late_corrections": [],
            })
    return chains


class ExperimentObservationReuseTest(unittest.TestCase):
    def test_validator_rejects_two_outcomes_relabelled_as_three_cases(self):
        spec = repeated_pair_spec(Path("/tmp/experiment-reuse-project"))
        self.assertEqual(len(outcome_chains(spec)), 2)
        self.assertEqual(len(spec["cases"]), 3)
        with self.assertRaises(ContractError):
            validate_experiment(spec)

    def test_validator_rejects_cross_arm_reuse_in_different_cases(self):
        spec = experiment(Path("/tmp/experiment-reuse-project"))
        spec["cases"][1]["control_outcome_id"] = spec["cases"][0]["candidate_outcome_id"]
        # Each local pair is distinct; uniqueness must cover all arms/cases.
        self.assertTrue(all(
            case["control_outcome_id"] != case["candidate_outcome_id"]
            for case in spec["cases"]
        ))
        with self.assertRaises(ContractError):
            validate_experiment(spec)

    def test_validator_rejects_same_arm_reuse_across_splits(self):
        spec = experiment(Path("/tmp/experiment-reuse-project"))
        spec["cases"][2]["candidate_outcome_id"] = spec["cases"][0]["candidate_outcome_id"]
        self.assertNotEqual(spec["cases"][0]["split"], spec["cases"][2]["split"])
        with self.assertRaises(ContractError):
            validate_experiment(spec)

    def test_evaluator_cannot_promote_two_observations_as_three_pairs(self):
        spec = repeated_pair_spec(Path("/tmp/experiment-reuse-project"))
        chains = outcome_chains(spec)
        with self.assertRaises(ContractError):
            evaluate_experiment(spec, chains, evaluated_at=NOW.isoformat())

    def test_store_rejects_reused_outcomes_without_persisting_evaluation(self):
        with tempfile.TemporaryDirectory(prefix="devsquad-experiment-reuse-") as temporary:
            root = Path(temporary)
            repo = root / "repo"
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            store = Store(root / "state.sqlite3", root / "artifacts")
            try:
                spec = repeated_pair_spec(repo)
                chains = outcome_chains(spec)
                for outcome_id, chain in chains.items():
                    final = chain["final"]
                    claim = store.claim_start(repo, f"run-{outcome_id}", {}, "owner")
                    # Seed the same legacy terminal fixture used by test_learning.
                    # It must never make reused observations valid evidence.
                    store.connection.execute(
                        "UPDATE runs SET state=?,phase=NULL WHERE id=?",
                        (final["verdict"], claim.run_id),
                    )
                    store.record_outcome(claim.run_id, final, now=NOW)
                rejected = None
                try:
                    store.evaluate_learning_experiment(spec, now=NOW)
                except ContractError as exc:
                    rejected = exc
                self.assertEqual(
                    store.connection.execute(
                        "SELECT COUNT(*) FROM experiments WHERE experiment_id=?",
                        (spec["experiment_id"],),
                    ).fetchone()[0],
                    0,
                    "invalid reused evidence must not leave a saved evaluation",
                )
                self.assertIsInstance(rejected, ContractError)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
