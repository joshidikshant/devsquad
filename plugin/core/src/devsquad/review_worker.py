"""Run one frozen branch-review fixture and its trusted declared checks."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from .contracts import ContractError
from .store import canonical_json
from .supervisor import BoundedDrain
from .workflows import (
    MAX_PREVIEW_CHARS,
    make_branch_review_evidence,
    validate_review_document,
)
from .workspaces import dirty_paths, reset_check_workspace


MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024


def _empty_stream() -> dict[str, Any]:
    return {
        "preview": "",
        "captured_bytes": 0,
        "total_bytes": 0,
        "truncated": False,
        "full_sha256": hashlib.sha256(b"").hexdigest(),
    }


def _stream_result(drain: BoundedDrain) -> dict[str, Any]:
    metadata = drain.finish()
    return {
        "preview": bytes(drain.content).decode("utf-8", "replace"),
        "captured_bytes": metadata["captured_bytes"],
        "total_bytes": metadata["total_bytes"],
        "truncated": metadata["truncated"],
        "full_sha256": metadata["full_sha256"],
    }


def _safe_check_cwd(root: Path, relative: str) -> Path:
    try:
        candidate = (root / relative).resolve(strict=True)
    except OSError as exc:
        raise ContractError(f"declared check cwd does not exist: {relative}") from exc
    if (candidate != root and root not in candidate.parents) or not candidate.is_dir():
        raise ContractError(f"declared check cwd escapes the check workspace: {relative}")
    return candidate


def _run_check(
    check: dict[str, Any],
    check_workspace: Path,
    candidate_sha256: str,
    target_oid: str,
) -> dict[str, Any]:
    started = time.monotonic()
    stdout_result, stderr_result = _empty_stream(), _empty_stream()
    cwd = _safe_check_cwd(check_workspace, check["cwd"])
    try:
        process = subprocess.Popen(
            check["argv"],
            cwd=cwd,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=False,
            close_fds=True,
        )
    except OSError:
        status, returncode, error_code = "launch_failed", None, "CLI_ERROR"
    else:
        assert process.stdout is not None and process.stderr is not None
        stdout = BoundedDrain(process.stdout, MAX_PREVIEW_CHARS)
        stderr = BoundedDrain(process.stderr, MAX_PREVIEW_CHARS)
        stdout.start()
        stderr.start()
        try:
            returncode = process.wait(timeout=check["timeout_seconds"])
            status = "passed" if returncode == 0 else "failed"
            error_code = None
        except subprocess.TimeoutExpired:
            status, returncode, error_code = "timed_out", None, "TIMEOUT"
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        stdout_result, stderr_result = _stream_result(stdout), _stream_result(stderr)
    return {
        "schema_version": 1,
        "candidate_sha256": candidate_sha256,
        "target_oid": target_oid,
        "id": check["id"],
        "argv": check["argv"],
        "cwd": check["cwd"],
        "required_to_pass": check["required_to_pass"],
        "status": status,
        "returncode": returncode,
        "error_code": error_code,
        "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
        "stdout": stdout_result,
        "stderr": stderr_result,
    }


def _verify_finding_locations(review: dict[str, Any], workspace: Path) -> None:
    for finding in review["findings"]:
        try:
            target = (workspace / finding["path"]).resolve(strict=True)
        except OSError as exc:
            raise ContractError(
                f"review finding path does not exist: {finding['path']}"
            ) from exc
        if target != workspace and workspace not in target.parents:
            raise ContractError("review finding path escapes the frozen workspace")
        if not target.is_file():
            raise ContractError(f"review finding path is not a file: {finding['path']}")
        lines = 0
        with target.open("rb") as stream:
            for lines, _ in enumerate(stream, 1):
                if lines >= finding["end_line"]:
                    break
        if lines < finding["end_line"]:
            raise ContractError(
                f"review finding line is outside the frozen file: {finding['path']}"
            )


def run_review_and_checks(
    snapshot: dict[str, Any],
    review: dict[str, Any],
    **attempt_metadata: Any,
) -> dict[str, Any]:
    """Validate one review, run the frozen checks, and build combined evidence."""
    task = snapshot.get("task")
    workspace = snapshot.get("workspace")
    check_workspace = snapshot.get("check_workspace")
    if not all(isinstance(value, dict) for value in (task, workspace, check_workspace)):
        raise ContractError("branch review snapshot is incomplete")
    normalized_review = validate_review_document(review, task, workspace)
    review_root = Path(workspace["path"]).resolve(strict=True)
    checks_root = Path(check_workspace["path"]).resolve(strict=True)
    reset_check_workspace(
        review_root,
        checks_root,
        workspace["target_oid"],
        check_workspace["scope"],
    )
    if dirty_paths(review_root):
        raise ContractError("frozen review workspace is dirty before reviewer execution")
    _verify_finding_locations(normalized_review, review_root)
    if dirty_paths(review_root):
        raise ContractError("reviewer modified the frozen read-only workspace")
    checks = [
        _run_check(
            check,
            checks_root,
            workspace["candidate_sha256"],
            workspace["target_oid"],
        )
        for check in task["checks"]
    ]
    return make_branch_review_evidence(
        snapshot, normalized_review, checks, **attempt_metadata,
    )


def run(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ContractError("workflow snapshot must be an object")
    fixture = snapshot.get("internal_review_fixture")
    if not isinstance(fixture, dict):
        raise ContractError("offline review snapshot is incomplete")
    selected = snapshot["routing"]["roles"]["reviewer"]["selected"]
    if selected.get("profile_id", "").endswith("-fixture-fail"):
        raise ContractError("offline reviewer fixture requested a failed attempt")
    return run_review_and_checks(snapshot, fixture)


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_SNAPSHOT_BYTES + 1)
    if len(payload) > MAX_SNAPSHOT_BYTES:
        raise ContractError("workflow snapshot exceeds its byte limit")
    try:
        snapshot = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("workflow snapshot is not valid UTF-8 JSON") from exc
    sys.stdout.write(canonical_json(run(snapshot)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
