"""Detached M2 supervisor entrypoint; invoked only from the durable service."""
import argparse
import json
import os
from pathlib import Path
import sys

from .contracts import ExecutionIdentity, LaunchSpec
from .store import ConflictError, Store
from .supervisor import Supervisor


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True); parser.add_argument("--artifacts", required=True)
    parser.add_argument("--run-id", required=True); parser.add_argument("--expected-version", type=int, required=True)
    parser.add_argument("--package-digest", required=True)
    args = parser.parse_args(argv)
    store = Store(Path(args.database), Path(args.artifacts))
    try:
        run = store.run(args.run_id); snapshot = json.loads(run["mutable_snapshot"])
        environment = {"DEVSQUAD_WORKER": "1", "DEVSQUAD_RUN_ID": args.run_id}
        identity = ExecutionIdentity("devsquad-fake-step", "1", None, None, None, None)
        command = [sys.executable, "-m", "devsquad.fake_step"]
        if "internal_fake_delay" in snapshot: command += ["--delay", str(snapshot["internal_fake_delay"])]
        spec = LaunchSpec(1, "devsquad-fake-step", "cli_exec", tuple(command), run["worktree_path"], None, snapshot["task"]["budget"]["wall_seconds"], identity, environment)
        supervisor = Supervisor(store)
        try: handle = supervisor.launch(args.run_id, args.expected_version, spec, f"daemon:{os.getpid()}", args.package_digest)
        except ConflictError: return 0
        return 0 if supervisor.wait(handle, spec.timeout_seconds) == 0 else 1
    finally: store.close()

if __name__ == "__main__": raise SystemExit(main())
