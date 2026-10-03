from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/core/src"))
from devsquad.council_comparison import predeclare, report
from devsquad.contracts import ContractError
from devsquad.store import Store, request_hash, ConflictError
import test_council_runtime as council_fixture_tests


class CouncilComparisonTest(unittest.TestCase):
    setUp = council_fixture_tests.CouncilRuntimeTest.setUp
    git = council_fixture_tests.CouncilRuntimeTest.git
    wait = council_fixture_tests.CouncilRuntimeTest.wait
    receipt = council_fixture_tests.CouncilRuntimeTest.receipt

    def test_predeclared_actual_matched_heldout_process_receipts_are_inconclusive_not_native_quality(self):
        profiles = json.loads((self.repo / "profiles.json").read_text())
        policy = json.loads((self.repo / "policy.json").read_text())
        cases = []
        for identifier, split, question in (("retry-key", "matched", "Compare retry key retention alternatives"),
                                             ("retry-expiry", "heldout", "Compare safe retry after key expiry")):
            council = copy.deepcopy(self.task)
            council["goal"] = question
            council["project"]["base_ref"] = self.source_oid
            council["project"]["target_ref"] = self.source_oid
            council["lead"]["mode"] = "host"
            council["routing"] = {"profiles": profiles, "policy": policy}
            control = copy.deepcopy(council)
            control["workflow"] = "branch-review"
            control.pop("council")
            control["routing"]["policy"]["roles"] = {"reviewer": [{"kind": "profile", "id": "critic"}]}
            cases.append({"id": identifier, "split": split, "control": control, "council": council})
        declaration = predeclare(self.root / "predeclared-workflow-comparison.json", cases)
        copied = copy.deepcopy(cases[0])
        copied["id"], copied["split"] = "renamed-repetition", "heldout"
        with self.assertRaises(ContractError):
            predeclare(self.root / "invalid-heldout.json", [cases[0], copied])
        with self.assertRaises(FileExistsError):
            predeclare(Path(declaration["path"]), cases)
        pairs = []
        self.task["lead"]["mode"] = "host"  # wait helper does not auto-submit
        for case in cases:
            control = self.service.start(case["control"], case["id"] + "-control",
                _internal_review_fixture={"verdict": "clean", "summary": "Controlled public review", "findings": []})
            waiting = self.wait(control["run_id"], {"awaiting_host", "failed"})
            self.assertEqual(waiting["state"], "awaiting_host")
            claimed = self.service.handoff_claim(control["run_id"], waiting["version"], "public-fixture-host")
            body = {"schema_version": 1, "submission_id": "public-control", "disposition": "accept", "reason": "Controlled mechanical acceptance",
                "evidence_refs": [{"artifact_id": item["artifact_id"], "sha256": item["sha256"]} for item in claimed["handoff"]["packet"]["artifacts"]]}
            self.service.handoff_complete(control["run_id"], claimed["claim"], {**body, "submission_hash": request_hash(body)})
            council = self.service.start(case["council"], case["id"] + "-council", _internal_council_fixture=self.fixture)
            self.assertEqual(self.wait(council["run_id"], {"awaiting_host", "failed"})["state"], "awaiting_host")
            self.service.finish_council(council["run_id"], "accept", "Controlled mechanical acceptance", chosen="synthesis",
                supported_claims=["No blind retry"], discarded_alternatives=["Blind retry"], validation="Test duplicate effects")
            pairs.append({"id": case["id"], "control": control["run_id"], "council": council["run_id"]})
        store = Store(self.runtime / "state.sqlite3", self.runtime / "artifacts")
        self.addCleanup(store.close)
        compared = report(store, declaration, pairs)
        self.assertTrue(Path(compared["path"]).is_file())
        self.assertEqual(report(store, declaration, pairs), compared)
        self.assertEqual(compared["report"]["conclusion"], "inconclusive")
        self.assertFalse(compared["report"]["automatic_enabled"])
        self.assertEqual({case["split"] for case in compared["report"]["cases"]}, {"matched", "heldout"})
        for case in compared["report"]["cases"]:
            self.assertEqual(case["arms"]["control"]["worker_invocations"], 1)
            self.assertEqual(case["arms"]["council"]["worker_invocations"], 3)
            for arm in case["arms"].values():
                self.assertIsNone(arm["accepted_quality"])
                self.assertIsNone(arm["escaped_defects"])
                self.assertIsNone(arm["rework"])
                self.assertGreaterEqual(arm["execution_elapsed_ms"], 0)
                self.assertTrue(all(arm["actual_prompt_sha256"]))
        with self.assertRaises(ContractError):
            report(store, declaration, [pairs[0], pairs[0]])
        original = Path(declaration["path"]).read_bytes()
        tampered = json.loads(original)
        tampered["automatic_enabled"] = True
        Path(declaration["path"]).write_text(json.dumps(tampered))
        with self.assertRaises(ConflictError):
            report(store, declaration, pairs)
        Path(declaration["path"]).write_bytes(original)


if __name__ == "__main__":
    unittest.main()
