"""Real offline experiment runs with a test-only preflight assignment seam.

This is not a public experiment controller or native-provider quality proof.
Profiles and reviews are explicit fixtures, but preparation, worker processes,
checks, host decisions and outcomes use the actual Service/Store workflow.
"""

from __future__ import annotations

from contextlib import ExitStack
import copy
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

from test_experiment_provenance import digest, execution_digest
from test_learning import experimental_final
from test_lifecycle import profile, review_task, routing_policy

from devsquad.experiment_provenance import assignment_for, paired_input_identity
from devsquad.router import load_routing
from devsquad.service import Service
from devsquad.store import Store, canonical_json, git_common_dir, request_hash


class ExperimentRuntimeFixture:
    def __init__(self, root: Path, *, with_fallback=False):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.with_fallback = with_fallback
        self.repo = self.root / "repo"
        self.service = Service(self.root / "runtime")
        self.runs = {}
        self.git("init", "-q", str(self.repo), outside=True)
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        (self.repo / "README").write_text("base\n")
        self.git("add", "README")
        self.git("commit", "-qm", "baseline")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.targets = {}
        for case_id in ("eval-1", "hold-1"):
            (self.repo / "README").write_text(f"candidate {case_id}\n")
            self.git("add", "README")
            self.git("commit", "-qm", f"candidate {case_id}")
            self.targets[case_id] = self.git("rev-parse", "HEAD").strip()
        self.common = str(git_common_dir(self.repo))
        self.profiles = {
            arm: profile(f"profile-{letter}", f"model-{letter}")
            for arm, letter in (("control", "a"), ("candidate", "b"))
        }
        if with_fallback:
            # The real offline worker deliberately errors on this suffix.
            self.profiles["candidate"]["id"] = "candidate-fixture-fail"
        self.registry = {
            "schema_version": 1, "profiles": list(self.profiles.values()),
            "bindings": {"review.deep": {"profile_id": "profile-a", "version": 7}},
        }
        self.policy = routing_policy()
        if with_fallback:
            fallback = profile("profile-fallback", "model-fallback")
            self.registry["profiles"].append(fallback)
            self.policy["roles"]["reviewer"] = [{"kind": "profile", "id": fallback["id"]}]
        self.package_path, self.package_digest = self.service._freeze_package()
        cases = []
        for case_id, split in (("eval-1", "evaluation"), ("hold-1", "held_out")):
            identities = paired_input_identity(
                self.declaration_snapshot(case_id, "control"),
                role="reviewer", package_digest=self.package_digest,
            )
            cases.append({
                "case_id": case_id, "split": split,
                "control_outcome_id": f"control-{case_id}",
                "candidate_outcome_id": f"candidate-{case_id}",
                **identities,
            })
        self.spec = {
            "schema_version": 2, "experiment_id": "saved-run-review-pair",
            "project_path": str(self.repo),
            "question": "Does the candidate fixture improve paired acceptance?",
            "hypothesis": "Candidate outcomes improve in evaluation and held-out cases.",
            "evidence_availability": "tracked_fixture",
            "variable": {
                "kind": "profile_binding", "alias": "review.deep", "role": "reviewer",
                **{f"{arm}_profile_id": value["id"] for arm, value in self.profiles.items()},
                **{f"{arm}_profile_sha256": digest(value) for arm, value in self.profiles.items()},
                **{f"{arm}_execution_sha256": execution_digest(value) for arm, value in self.profiles.items()},
            },
            "cases": cases,
            "gate": {
                "min_evaluation_pairs": 1, "min_held_out_pairs": 1,
                "noninferiority_margin": 0.0, "minimum_success_gain": 1.0,
                "max_candidate_escaped_defects": 0,
            },
            "budget": {"max_cases": 2, "max_worker_invocations": 8 if with_fallback else 4, "wall_seconds": 600},
            "rollback_target": {"profile_id": "profile-a", "binding_version": 7},
        }

    def git(self, *args, outside=False):
        command = ["git"] if outside else ["git", "-C", str(self.repo)]
        return subprocess.run(command + list(args), check=True, capture_output=True, text=True).stdout

    def task(self, case_id, arm):
        task = review_task(self.repo)
        task["project"].update({"base_ref": self.base, "target_ref": self.targets[case_id]})
        task["goal"] = "Review the exact README candidate and pass the declared check."
        task["routing"] = {
            "profiles": copy.deepcopy(self.registry), "policy": copy.deepcopy(self.policy),
            "overrides": {"reviewer": {
                "profile_id": self.profiles[arm]["id"],
                "fallback": "policy" if self.with_fallback else "none",
            }},
        }
        task["checks"] = [{
            "id": "read-candidate", "argv": [sys.executable, "-c", "from pathlib import Path; assert Path('README').read_text().startswith('candidate')"],
            "cwd": ".", "timeout_seconds": 10, "required_to_pass": True,
        }]
        task["budget"]["wall_seconds"] = 120
        if self.with_fallback:
            task["budget"].update(max_worker_invocations=2, max_fallbacks_per_step=1)
        return task

    def declaration_snapshot(self, case_id, arm):
        task = self.task(case_id, arm)
        identity = {
            "schema_version": 1, "base_oid": self.base,
            "target_oid": self.targets[case_id], "changed_paths": ["README"],
        }
        return {
            "task": task, "base_oid": self.base, "target_oid": self.targets[case_id],
            "workspace": {**identity, "candidate_sha256": digest(identity)},
            "configs": {"policy_file": {"sha256": digest(self.policy)}},
            "routing": load_routing(task, canonical_json(self.registry), canonical_json(self.policy)),
        }

    def store(self):
        return Store(self.service.database, self.service.artifacts)

    def wait(self, run_id):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status = self.service.status(run_id)
            if status["state"] in {"awaiting_host", "failed", "succeeded", "cancelled"}:
                return status
            time.sleep(0.05)
        raise AssertionError(f"saved-run fixture did not reach a gate: {self.service.status(run_id)}")

    def run_arm(self, case_id, arm, *, no_attempt=False):
        original = self.service._resolve_snapshot

        def predeclared_assignment(*args, **kwargs):
            snapshot = original(*args, **kwargs)
            snapshot["experiment_spec"] = copy.deepcopy(self.spec)
            snapshot["experiment_assignment"] = assignment_for(
                self.spec, case_id, arm, project_common_dir=self.common,
            )
            return snapshot

        with ExitStack() as stack:
            stack.enter_context(patch.object(self.service, "_resolve_snapshot", side_effect=predeclared_assignment))
            if no_attempt:
                stack.enter_context(patch.object(self.service, "_spawn_daemon", return_value=0))
            started = self.service.start(
                self.task(case_id, arm), f"{arm}-{case_id}",
                _internal_review_fixture={"verdict": "clean", "summary": "Fixture review of the frozen candidate.", "findings": []},
            )
        run_id = started["run_id"]
        self.runs[(case_id, arm)] = run_id
        if started["state"] != "queued":
            raise AssertionError(f"public fixture preparation failed: {started}")
        if no_attempt:
            self.service.cancel(run_id)
            verdict = "cancelled"
        else:
            waiting = self.wait(run_id)
            if waiting["state"] != "awaiting_host":
                raise AssertionError(f"public fixture worker failed: {waiting}")
            claimed = self.service.handoff_claim(run_id, waiting["version"], "experiment-fixture-host")
            packet = claimed["handoff"]["packet"]
            body = {
                "schema_version": 1, "submission_id": f"finish-{arm}-{case_id}",
                "disposition": "accept" if arm == "candidate" else "reject",
                "reason": "Predeclared offline fixture disposition.",
                "evidence_refs": [{"artifact_id": reference["artifact_id"], "sha256": reference["sha256"]} for reference in packet["artifacts"]],
            }
            completed = self.service.handoff_complete(run_id, claimed["claim"], {**body, "submission_hash": request_hash(body)})
            verdict = "succeeded" if arm == "candidate" else "failed"
            if completed["state"] != verdict:
                raise AssertionError(f"public fixture disposition failed: {completed}")
        outcome = experimental_final(f"{arm}-{case_id}", verdict)
        outcome["observed_at"] = datetime.now(timezone.utc).isoformat()
        outcome["evidence_refs"] = ["receipt.json", "result-receipt.json"]
        self.service.outcome_add(run_id, outcome)
        return run_id

    def run_all(self, *, skip=None, no_attempt=None):
        for case in self.spec["cases"]:
            for arm in ("control", "candidate"):
                key = (case["case_id"], arm)
                if key != skip:
                    self.run_arm(*key, no_attempt=key == no_attempt)

    def close(self):
        for run_id in self.runs.values():
            if self.service.status(run_id)["state"] not in {"succeeded", "failed", "cancelled"}:
                self.service.cancel(run_id)
