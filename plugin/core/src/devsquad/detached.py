"""Detached M2 supervisor entrypoint; invoked only from the durable service."""
import argparse
import json
import os
from pathlib import Path
import sys

from .contracts import BudgetExhausted, ExecutionIdentity, LaunchSpec
from .service import Service
from .store import ConflictError, Store, canonical_json
from .supervisor import Supervisor


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True); parser.add_argument("--artifacts", required=True)
    parser.add_argument("--run-id", required=True); parser.add_argument("--expected-version", type=int, required=True)
    parser.add_argument("--package-digest", required=True)
    args = parser.parse_args(argv)
    store = Store(Path(args.database), Path(args.artifacts))
    try:
        run = store.run(args.run_id)
        if run.get("package_digest") != args.package_digest:
            raise ConflictError("detached package digest does not match prepared run")
        snapshot = json.loads(run["mutable_snapshot"])
        environment = {
            "DEVSQUAD_WORKER": "1",
            "DEVSQUAD_RUN_ID": args.run_id,
            "DEVSQUAD_DELEGATION_DEPTH": "1",
        }
        stdin_path = None
        adapter = None
        handoff = store.handoff_snapshot(args.run_id)
        headless_lead = (
            snapshot["task"]["lead"]["mode"] == "headless"
            and handoff is not None
            and handoff.status == "open"
        )
        role = "lead" if headless_lead else "reviewer"
        workflow_role = (
            "internal_review_fixture" in snapshot or "review_adapter" in snapshot
        )
        if workflow_role:
            selected = snapshot["routing"]["roles"][role]["selected"]["profile"]
            adapter_key = "lead_adapter" if headless_lead else "review_adapter"
            adapter = snapshot.get(adapter_key)
            identity = ExecutionIdentity(
                selected["harness"],
                adapter["harness_version"] if adapter else "fixture",
                adapter["model_provider"] if adapter else None,
                selected["model_family"],
                selected["model_id"],
                selected["effort"]["value"],
                tuple(selected["required_tools"]),
                selected["permission_policy"],
                selected["account_pool_id"],
                "verified" if adapter else "unknown",
            )
            module = (
                ("devsquad.codex_lead_worker" if adapter else "devsquad.lead_worker")
                if headless_lead
                else ("devsquad.codex_review_worker" if adapter else "devsquad.review_worker")
            )
            command = [sys.executable, "-P", "-m", module]
            worker_snapshot = dict(snapshot)
            if headless_lead:
                worker_snapshot["headless_handoff"] = {
                    "handoff_id": handoff.handoff_id,
                    "packet": handoff.packet,
                    "packet_sha256": handoff.packet_sha256,
                }
            input_path, _, _ = store.finalize_artifact(
                args.run_id,
                "lead-workflow-input.json" if headless_lead else "workflow-input.json",
                canonical_json(worker_snapshot).encode(),
            )
            stdin_path = str(input_path)
        else:
            identity = ExecutionIdentity("devsquad-fake-step", "1", None, None, None, None)
            command = [sys.executable, "-P", "-m", "devsquad.fake_step"]
            if "internal_fake_delay" in snapshot:
                command += ["--delay", str(snapshot["internal_fake_delay"])]
        remaining_wall = store.remaining_wall_seconds(args.run_id)
        if remaining_wall == 0:
            Service(Path(args.database).parent).fail_budget_exhausted(
                args.run_id, args.expected_version,
            )
            return 1
        spec = LaunchSpec(
            1,
            identity.harness,
            "native_protocol" if adapter else "cli_exec",
            tuple(command),
            run["worktree_path"],
            stdin_path,
            remaining_wall or snapshot["task"]["budget"]["wall_seconds"],
            identity,
            environment,
        )
        supervisor = Supervisor(store)
        try: handle = supervisor.launch_durable(args.run_id, args.expected_version, spec, f"daemon:{os.getpid()}", args.package_digest, role=role if workflow_role else "worker")
        except ConflictError: return 0
        except BudgetExhausted:
            Service(Path(args.database).parent).fail_budget_exhausted(
                args.run_id, args.expected_version,
            )
            return 1
        returncode = supervisor.wait_durable(handle, spec.timeout_seconds)
        current = store.run(args.run_id)
    finally:
        store.close()
    if (current["state"] == "awaiting_host"
            and snapshot["task"]["lead"]["mode"] == "headless"):
        try:
            Service(Path(args.database).parent).resume(args.run_id)
        except ConflictError:
            pass
    return 0 if returncode == 0 else 1

if __name__ == "__main__": raise SystemExit(main())
