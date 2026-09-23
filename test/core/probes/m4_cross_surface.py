#!/usr/bin/env python3
"""Prepare and finish one private M4 cross-surface MCP acceptance run."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from devsquad.service import Service
from devsquad.store import request_hash


TERMINAL_STATES = {"succeeded", "failed", "cancelled", "timed_out"}


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wait(service: Service, run_id: str, states: set[str], timeout: int = 20) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = service.status(run_id)
        if status["state"] in states:
            return status
        time.sleep(0.05)
    raise TimeoutError(f"run did not reach {sorted(states)}: {service.status(run_id)}")


def _routing_documents() -> tuple[dict[str, Any], dict[str, Any]]:
    profiles = {
        "schema_version": 1,
        "profiles": [{
            "id": "m4-offline-reviewer",
            "harness": "fixture",
            "model_family": "offline-fixture",
            "model_id": "m4-offline-review",
            "effort": {"value": "low", "transport": "native"},
            "required_tools": ["read"],
            "permission_policy": "read_only",
            "account_pool_id": "m4-offline",
            "billing_mode": "subscription",
            "quality_status": "proven",
            "evidence_refs": ["m4-cross-surface-probe"],
        }],
        "bindings": {
            "review.deep": {"profile_id": "m4-offline-reviewer", "version": 1},
        },
    }
    policy = {
        "schema_version": 1,
        "id": "m4-cross-surface-policy",
        "version": 1,
        "roles": {"reviewer": [{"kind": "alias", "id": "review.deep"}]},
        "task_classes": {"m4-cross-surface": "proven"},
        "require_different_model_for_review": True,
        "prefer_different_harness_for_review": True,
        "account_pools": {
            "m4-offline": {
                "allowed_billing_modes": ["subscription"],
                "max_concurrency": 1,
                "unknown_capacity_policy": "allow_bounded",
            },
        },
        "experiment_budget": {},
    }
    return profiles, policy


def _make_repository(repo: Path) -> tuple[str, str]:
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "devsquad-probe@example.invalid")
    _git(repo, "config", "user.name", "DevSquad Probe")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "devsquad").mkdir()
    (repo / "src/value.py").write_text("VALUE = 'base'\n")
    (repo / "tests/test_value.py").write_text("# bounded fixture\n")
    profiles, policy = _routing_documents()
    _write_json(repo / "devsquad/profiles.json", profiles)
    _write_json(repo / "devsquad/policy.json", policy)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "probe base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "src/value.py").write_text("VALUE = 'candidate'\n")
    _git(repo, "add", "src/value.py")
    _git(repo, "commit", "-qm", "probe candidate")
    return base, _git(repo, "rev-parse", "HEAD")


def _task(repo: Path, base: str, target: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "project": {
            "repo_path": str(repo), "base_ref": base, "target_ref": target,
        },
        "workflow": "branch-review",
        "goal": "Prove that local MCP clients share one durable saved run.",
        "task_class": "m4-cross-surface",
        "acceptance": [{
            "id": "shared-run",
            "description": "Each client observes the same run and bound evidence.",
            "evidence_kind": "review",
        }],
        "checks": [{
            "id": "offline-check",
            "argv": [sys.executable, "-c", "print('m4 check passed')"],
            "cwd": ".",
            "timeout_seconds": 15,
            "required_to_pass": True,
        }],
        "scope": {"read_paths": ["src", "tests"], "write_paths": []},
        "lead": {"mode": "host"},
        "routing": {
            "profiles_file": "devsquad/profiles.json",
            "policy_file": "devsquad/policy.json",
        },
        "budget": {
            "wall_seconds": 120,
            "max_worker_invocations": 1,
            "max_revisions": 0,
            "max_fallbacks_per_step": 0,
        },
        "review": {"mode": "standard"},
        "origin": {"surface": "terminal-m4-probe"},
    }


def prepare(args: argparse.Namespace) -> int:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = args.output_dir.expanduser().resolve() / f"m4-cross-surface-{stamp}"
    run_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    run_dir.chmod(0o700)
    repo = run_dir / "repository"
    base, target = _make_repository(repo)
    task = _task(repo, base, target)
    fixture = {
        "verdict": "findings",
        "summary": "The bounded fixture changes the configured value.",
        "findings": [{
            "id": "M4-1",
            "severity": "low",
            "title": "Fixture value changed",
            "description": "The candidate intentionally changes the fixture value.",
            "path": "src/value.py",
            "start_line": 1,
            "end_line": 1,
            "evidence": "The candidate contains VALUE = 'candidate'.",
        }],
    }
    service = Service(args.runtime)
    started = service.start(
        task,
        f"m4-cross-surface-{stamp}",
        _internal_review_fixture=fixture,
    )
    waiting = _wait(service, started["run_id"], {"awaiting_host", "failed"})
    if waiting["state"] != "awaiting_host":
        raise RuntimeError(f"offline run did not produce a handoff: {waiting}")
    state = {
        "schema_version": 1,
        "run_dir": str(run_dir),
        "runtime": str(args.runtime.resolve()),
        "squad_executable": str(args.squad.resolve(strict=True)),
        "run_id": started["run_id"],
        "terminal_start": started,
        "waiting": waiting,
        "base_oid": base,
        "target_oid": target,
    }
    state_path = run_dir / "probe-state.json"
    _write_json(state_path, state)
    print(json.dumps({
        "state_file": str(state_path),
        "run_id": started["run_id"],
        "state": waiting["state"],
        "version": waiting["version"],
    }, sort_keys=True))
    return 0


def _data(result: Any) -> dict[str, Any]:
    payload = result.structured_content
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(f"MCP operation failed: {payload}")
    return payload["data"]


async def _client(
    state: dict[str, Any], surface: str,
):
    from mcp import Client, StdioServerParameters

    parameters = StdioServerParameters(
        command=state["squad_executable"],
        args=[
            "mcp", "serve", "--runtime-dir", state["runtime"],
            "--surface", surface,
        ],
    )
    return Client(parameters)


async def _complete(state: dict[str, Any]) -> dict[str, Any]:
    run_id = state["run_id"]
    codex = await _client(state, "codex-app")
    async with codex:
        codex_status = _data(await codex.call_tool("squad_status", {"run_id": run_id}))
        codex_events = _data(await codex.call_tool(
            "squad_events", {"run_id": run_id, "after": 0, "limit": 100},
        ))

    claude = await _client(state, "claude-code")
    async with claude:
        claude_status = _data(await claude.call_tool("squad_status", {"run_id": run_id}))
        claude_events = _data(await claude.call_tool(
            "squad_events", {"run_id": run_id, "after": 0, "limit": 100},
        ))
        claimed = _data(await claude.call_tool("squad_handoff_claim", {
            "run_id": run_id,
            "expected_version": claude_status["version"],
            "owner": "m4-claude-client",
        }))

    competing = await _client(state, "antigravity")
    async with competing:
        fenced_result = await competing.call_tool("squad_handoff_claim", {
            "run_id": run_id,
            "expected_version": claude_status["version"],
            "owner": "m4-competing-client",
        })
        fenced = fenced_result.structured_content

    packet = claimed["handoff"]["packet"]
    decision_body = {
        "schema_version": 1,
        "submission_id": "m4-cross-surface-accept",
        "disposition": "accept",
        "reason": "Accept the bounded offline evidence after cross-client inspection.",
        "evidence_refs": [{
            "artifact_id": item["artifact_id"], "sha256": item["sha256"],
        } for item in packet["artifacts"]],
    }
    decision = {**decision_body, "submission_hash": request_hash(decision_body)}
    claude_complete = await _client(state, "claude-code")
    async with claude_complete:
        completed = _data(await claude_complete.call_tool(
            "squad_handoff_complete",
            {"run_id": run_id, "claim": claimed["claim"], "decision": decision},
        ))

    codex_result = await _client(state, "codex-app")
    async with codex_result:
        result = _data(await codex_result.call_tool(
            "squad_result", {"run_id": run_id, "preview_bytes": 0},
        ))
        final_events = _data(await codex_result.call_tool(
            "squad_events", {"run_id": run_id, "after": 0, "limit": 100},
        ))
    return {
        "codex_status": codex_status,
        "claude_status": claude_status,
        "preclaim_ledgers_identical": codex_events == claude_events,
        "claim": claimed["claim"],
        "packet_sha256": claimed["handoff"]["packet_sha256"],
        "candidate_sha256": packet["candidate_sha256"],
        "competing_claim": fenced,
        "completed": completed,
        "result": result,
        "final_events": final_events,
    }


def complete(args: argparse.Namespace) -> int:
    state_path = args.state_file.expanduser().resolve(strict=True)
    state = json.loads(state_path.read_text())
    evidence = asyncio.run(_complete(state))
    if not evidence["preclaim_ledgers_identical"]:
        raise RuntimeError("Codex and Claude clients observed different ledgers")
    fenced = evidence["competing_claim"]
    if (not isinstance(fenced, dict) or fenced.get("ok") is not False
            or fenced.get("error", {}).get("code") != "CONFLICT"):
        raise RuntimeError(f"competing host was not fenced: {fenced}")
    if evidence["completed"]["state"] != "succeeded":
        raise RuntimeError(f"completion did not succeed: {evidence['completed']}")
    result = evidence["result"]
    if result["run_id"] != state["run_id"] or not result["ready"]:
        raise RuntimeError(f"terminal result is not ready: {result}")
    receipt = {
        "schema_version": 1,
        "status": "passed",
        "run_id": state["run_id"],
        "terminal_start": state["terminal_start"],
        "clients": ["codex-app", "claude-code", "antigravity"],
        "same_run_id": (
            evidence["codex_status"]["run_id"]
            == evidence["claude_status"]["run_id"]
            == result["run_id"]
            == state["run_id"]
        ),
        "preclaim_ledgers_identical": True,
        "packet_sha256": evidence["packet_sha256"],
        "candidate_sha256": evidence["candidate_sha256"],
        "competing_claim_error": fenced["error"]["code"],
        "terminal_state": result["state"],
        "event_count": len(evidence["final_events"]["events"]),
        "artifacts": [{
            key: artifact[key]
            for key in ("id", "name", "sha256", "byte_size")
        } for artifact in result["artifacts"]],
    }
    receipt_path = Path(state["run_dir"]) / "probe-receipt.json"
    _write_json(receipt_path, receipt)
    print(json.dumps({
        "status": "passed",
        "run_id": state["run_id"],
        "receipt": str(receipt_path),
        "receipt_sha256": _sha256(receipt_path),
    }, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime", type=Path,
        default=Path.home() / ".devsquad/runtime",
    )
    parser.add_argument(
        "--squad", type=Path,
        default=Path.home() / ".local/bin/squad",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path.home() / ".devsquad/private-probes",
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.set_defaults(function=prepare)
    complete_parser = subparsers.add_parser("complete")
    complete_parser.add_argument("--state-file", type=Path, required=True)
    complete_parser.set_defaults(function=complete)
    args = parser.parse_args()
    args.runtime = args.runtime.expanduser().resolve()
    args.squad = args.squad.expanduser().resolve(strict=True)
    args.output_dir = args.output_dir.expanduser().resolve()
    args.output_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    return args.function(args)


if __name__ == "__main__":
    raise SystemExit(main())
