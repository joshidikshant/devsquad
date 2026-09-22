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


def _is_saved_fallback_failure(attempt) -> bool:
    encoded = attempt.get("output_metadata")
    if not encoded:
        return False
    try:
        metadata = json.loads(encoded)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ConflictError("saved attempt metadata is invalid") from exc
    if not isinstance(metadata, dict):
        raise ConflictError("saved attempt metadata is invalid")
    failure = metadata.get("failure")
    if failure is None:
        return False
    if not isinstance(failure, dict):
        raise ConflictError("saved fallback failure is invalid")
    return True


def _profile_index(
    store: Store,
    run_id: str,
    role: str,
    handoff,
) -> int:
    attempts = store.attempts_for_run(run_id)
    if handoff is None:
        return sum(
            attempt.get("role") == role and _is_saved_fallback_failure(attempt)
            for attempt in attempts
        )
    reviewer_id = handoff.packet.get("attempt_id")
    reviewer = next(
        (attempt for attempt in attempts if attempt["id"] == reviewer_id), None,
    )
    if reviewer is None:
        raise ConflictError("handoff reviewer attempt is missing")
    if role == "reviewer":
        seen = False
        used = 0
        for attempt in attempts:
            if attempt["id"] == reviewer_id:
                seen = True
            elif (seen and attempt.get("role") == "reviewer"
                    and _is_saved_fallback_failure(attempt)):
                used += 1
        return used
    return sum(
        attempt.get("role") == "lead"
        and attempt["created_at"] >= reviewer["created_at"]
        and _is_saved_fallback_failure(attempt)
        for attempt in attempts
    )


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
            "internal_review_fixture" in snapshot
            or "review_adapter" in snapshot
            or "review_adapters" in snapshot
        )
        profile_index = None
        profile_id = None
        if workflow_role:
            routed_role = snapshot["routing"]["roles"][role]
            candidates = [routed_role["selected"], *routed_role["fallbacks"]]
            profile_index = _profile_index(
                store, args.run_id, role, handoff,
            )
            if profile_index >= len(candidates):
                raise ConflictError("frozen role fallback set is exhausted")
            attempt_selection = candidates[profile_index]
            profile_id = attempt_selection["profile_id"]
            selected = attempt_selection["profile"]
            adapter_key = "lead_adapter" if headless_lead else "review_adapter"
            adapters_key = "lead_adapters" if headless_lead else "review_adapters"
            adapters = snapshot.get(adapters_key)
            adapter = (
                adapters.get(profile_id)
                if isinstance(adapters, dict)
                else snapshot.get(adapter_key) if profile_index == 0 else None
            )
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
            worker_snapshot = json.loads(canonical_json(snapshot))
            worker_snapshot["routing"]["roles"][role]["selected"] = attempt_selection
            if adapter is None:
                worker_snapshot.pop(adapter_key, None)
            else:
                worker_snapshot[adapter_key] = adapter
            if headless_lead:
                worker_snapshot["headless_handoff"] = {
                    "handoff_id": handoff.handoff_id,
                    "packet": handoff.packet,
                    "packet_sha256": handoff.packet_sha256,
                }
            input_path, _, _ = store.finalize_artifact(
                args.run_id,
                (
                    f"lead-workflow-input-{handoff.sequence}-{profile_index}.json"
                    if headless_lead
                    else f"workflow-input-{profile_index}.json"
                ),
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
        try:
            handle = supervisor.launch_durable(
                args.run_id,
                args.expected_version,
                spec,
                f"daemon:{os.getpid()}",
                args.package_digest,
                role=role if workflow_role else "worker",
                profile_id=profile_id,
                profile_index=profile_index,
            )
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
    elif current["state"] == "queued" and current["phase"] is None:
        try:
            Service(Path(args.database).parent).resume(args.run_id)
        except ConflictError:
            pass
    return 0 if returncode == 0 else 1

if __name__ == "__main__": raise SystemExit(main())
