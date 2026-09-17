"""Detached M2 supervisor entrypoint; invoked only from the durable service."""
import argparse
import json
import os
from pathlib import Path
import sys

from .contracts import ExecutionIdentity, LaunchSpec
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
        if "internal_review_fixture" in snapshot or "review_adapter" in snapshot:
            selected = snapshot["routing"]["roles"]["reviewer"]["selected"]["profile"]
            adapter = snapshot.get("review_adapter")
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
                "devsquad.codex_review_worker"
                if adapter else "devsquad.review_worker"
            )
            command = [sys.executable, "-P", "-m", module]
            input_path, _, _ = store.finalize_artifact(
                args.run_id,
                "workflow-input.json",
                canonical_json(snapshot).encode(),
            )
            stdin_path = str(input_path)
        else:
            identity = ExecutionIdentity("devsquad-fake-step", "1", None, None, None, None)
            command = [sys.executable, "-P", "-m", "devsquad.fake_step"]
            if "internal_fake_delay" in snapshot:
                command += ["--delay", str(snapshot["internal_fake_delay"])]
        spec = LaunchSpec(
            1,
            identity.harness,
            "native_protocol" if "review_adapter" in snapshot else "cli_exec",
            tuple(command),
            run["worktree_path"],
            stdin_path,
            snapshot["task"]["budget"]["wall_seconds"],
            identity,
            environment,
        )
        supervisor = Supervisor(store)
        try: handle = supervisor.launch_durable(args.run_id, args.expected_version, spec, f"daemon:{os.getpid()}", args.package_digest)
        except ConflictError: return 0
        return 0 if supervisor.wait_durable(handle, spec.timeout_seconds) == 0 else 1
    finally: store.close()

if __name__ == "__main__": raise SystemExit(main())
