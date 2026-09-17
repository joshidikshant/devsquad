#!/usr/bin/env python3
"""Opt-in live proof for the public M3 native Codex branch-review path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
CORE_SRC = ROOT / "plugin" / "core" / "src"
sys.path.insert(0, str(CORE_SRC))

from devsquad.service import Service
from devsquad.store import request_hash


TERMINAL_STATES = {"succeeded", "failed", "cancelled", "timed_out"}
SAFE_MODEL = re.compile(r"[A-Za-z0-9._-]+\Z")


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wait(service: Service, run_id: str, timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = service.status(run_id)
        if status["state"] in TERMINAL_STATES | {"awaiting_host", "blocked"}:
            return status
        time.sleep(0.1)
    raise TimeoutError("public M3 review did not reach a bounded handoff or terminal state")


def _make_repository(repo: Path, model: str, effort: str) -> tuple[str, str]:
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "devsquad-probe@example.invalid")
    _git(repo, "config", "user.name", "DevSquad Probe")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "devsquad").mkdir()
    (repo / "src/__init__.py").write_text("")
    (repo / "src/ratio.py").write_text(
        "def safe_ratio(numerator, denominator):\n"
        "    if denominator == 0:\n"
        "        return None\n"
        "    return numerator / denominator\n"
    )
    (repo / "tests/test_ratio.py").write_text(
        "import unittest\n"
        "from src.ratio import safe_ratio\n\n"
        "class RatioTest(unittest.TestCase):\n"
        "    def test_positive_ratio(self):\n"
        "        self.assertEqual(safe_ratio(6, 3), 2)\n"
    )
    profiles = {
        "schema_version": 1,
        "profiles": [{
            "id": "m3-live-codex-reviewer",
            "harness": "codex",
            "model_family": "gpt",
            "model_id": model,
            "effort": {"value": effort, "transport": "native"},
            "required_tools": ["read"],
            "permission_policy": "read_only",
            "account_pool_id": "codex-subscription",
            "billing_mode": "subscription",
            "quality_status": "trial",
            "evidence_refs": ["m3-live-probe"],
        }],
        "bindings": {
            "review.deep": {
                "profile_id": "m3-live-codex-reviewer",
                "version": 1,
            },
        },
    }
    policy = {
        "schema_version": 1,
        "id": "m3-live-probe-policy",
        "version": 1,
        "roles": {"reviewer": [{"kind": "alias", "id": "review.deep"}]},
        "task_classes": {"live-review-small": "trial"},
        "require_different_model_for_review": True,
        "prefer_different_harness_for_review": True,
        "account_pools": {
            "codex-subscription": {
                "allowed_billing_modes": ["subscription"],
                "max_concurrency": 1,
                "unknown_capacity_policy": "allow_bounded",
            },
        },
        "experiment_budget": {},
    }
    _write_json(repo / "devsquad/profiles.json", profiles)
    _write_json(repo / "devsquad/policy.json", policy)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "probe base")
    base = _git(repo, "rev-parse", "HEAD")

    # Deliberately introduce a small reviewable regression while leaving the
    # declared positive-path check green. The integration proof does not
    # require a particular verdict, but a supported finding is useful evidence.
    (repo / "src/ratio.py").write_text(
        "def safe_ratio(numerator, denominator):\n"
        "    if numerator == 0:\n"
        "        return None\n"
        "    return numerator / denominator\n"
    )
    _git(repo, "add", "src/ratio.py")
    _git(repo, "commit", "-qm", "candidate regression")
    return base, _git(repo, "rev-parse", "HEAD")


def _task(repo: Path, base: str, target: str, wall_seconds: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "project": {
            "repo_path": str(repo),
            "base_ref": base,
            "target_ref": target,
        },
        "workflow": "branch-review",
        "goal": "Review the exact candidate for correctness regressions with file-and-line evidence.",
        "task_class": "live-review-small",
        "acceptance": [{
            "id": "candidate-bound-review",
            "description": "Return a candidate-bound review with supported file locations.",
            "evidence_kind": "review",
        }, {
            "id": "declared-check",
            "description": "Record the trusted declared check outcome.",
            "evidence_kind": "check",
        }],
        "checks": [{
            "id": "unit-tests",
            "argv": [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
            "cwd": ".",
            "timeout_seconds": 30,
            "required_to_pass": True,
        }],
        "scope": {"read_paths": ["src", "tests"], "write_paths": []},
        "lead": {"mode": "host"},
        "routing": {
            "profiles_file": "devsquad/profiles.json",
            "policy_file": "devsquad/policy.json",
        },
        "budget": {
            "wall_seconds": wall_seconds,
            "max_worker_invocations": 1,
            "max_revisions": 0,
            "max_fallbacks_per_step": 0,
        },
        "review": {"mode": "standard"},
        "origin": {"surface": "live-probe"},
    }


def _decision(packet: dict[str, Any]) -> dict[str, Any]:
    body = {
        "schema_version": 1,
        "submission_id": "m3-live-host-accept",
        "disposition": "accept",
        "reason": "Accept the bounded reviewer and required-check evidence.",
        "evidence_refs": [{
            "artifact_id": reference["artifact_id"],
            "sha256": reference["sha256"],
        } for reference in packet["artifacts"]],
    }
    return {**body, "submission_hash": request_hash(body)}


def _lock_down(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        try:
            if path.is_dir():
                path.chmod(0o700)
            elif path.is_file():
                path.chmod(0o600)
        except OSError:
            pass
    root.chmod(0o700)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-live", action="store_true",
        help="required acknowledgement for one subscription-backed model turn",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path.home() / ".devsquad" / "private-probes",
    )
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument(
        "--effort", default="low",
        choices=("minimal", "low", "medium", "high", "xhigh"),
    )
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    if not args.run_live:
        parser.error("--run-live is required")
    if not SAFE_MODEL.fullmatch(args.model):
        parser.error("--model must contain only letters, numbers, dot, underscore or hyphen")
    if args.timeout < 30 or args.timeout > 600:
        parser.error("--timeout must be between 30 and 600 seconds")

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    revision = _git(ROOT, "rev-parse", "HEAD")
    run_dir = (
        args.output_dir.expanduser().resolve()
        / f"m3-codex-review-{stamp}-{revision[:12]}"
    )
    run_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    run_dir.chmod(0o700)
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "status": "failed",
        "started_at": stamp,
        "revision": revision,
        "requested": {"model": args.model, "effort": args.effort},
    }
    service: Service | None = None
    run_id: str | None = None
    try:
        repo = run_dir / "repository"
        runtime = run_dir / "runtime"
        base, target = _make_repository(repo, args.model, args.effort)
        service = Service(runtime)
        started = service.start(
            _task(repo, base, target, args.timeout),
            f"m3-live-{stamp}",
        )
        run_id = started["run_id"]
        receipt["run_id"] = run_id
        if started["state"] == "failed":
            raise RuntimeError(f"public start failed: {started.get('error')}")
        waiting = _wait(service, run_id, args.timeout + 30)
        if waiting["state"] != "awaiting_host":
            raise RuntimeError(f"review did not produce a host handoff: {waiting}")
        claimed = service.handoff_claim(
            run_id, waiting["version"], "m3-live-probe-host",
        )
        packet = claimed["handoff"]["packet"]
        attempt = packet["attempt"]
        observed = attempt["observed_identity"]
        if (observed["harness"] != "codex"
                or observed["model_id"] != args.model
                or observed["effort"] != args.effort
                or observed["permission_policy"] != "read_only"
                or observed["verification"] != "verified"):
            raise RuntimeError(f"observed reviewer identity drifted: {observed}")
        if attempt["usage"]["source"] != "native_reported":
            raise RuntimeError("Codex did not report native token usage")
        if packet["evaluation"]["accept_allowed"] is not True:
            raise RuntimeError(f"required evidence blocked acceptance: {packet['evaluation']}")
        completed = service.handoff_complete(
            run_id, claimed["claim"], _decision(packet),
        )
        if completed["state"] != "succeeded":
            raise RuntimeError(f"host completion did not succeed: {completed}")
        result = service.result(run_id)
        artifacts = {item["name"]: item for item in result["artifacts"]}
        required_reports = {
            "receipt.json", "receipt.md", "events.jsonl",
            "artifact-manifest.json", "result-receipt.json",
        }
        if not result["ready"] or not required_reports <= set(artifacts):
            raise RuntimeError("terminal result is missing required M3 reports")
        receipt.update({
            "status": "passed",
            "observed": observed,
            "native_ids_present": {
                key: bool(attempt["native_ids"].get(key))
                for key in ("thread_id", "turn_id")
            },
            "usage": attempt["usage"],
            "review": {
                "verdict": packet["review"]["verdict"],
                "finding_count": len(packet["review"]["findings"]),
            },
            "checks": [{
                "id": check["id"], "status": check["status"],
                "required_to_pass": check["required_to_pass"],
            } for check in packet["checks"]],
            "candidate_sha256": packet["candidate_sha256"],
            "terminal_state": completed["state"],
            "report_sha256": {
                name: artifacts[name]["sha256"] for name in sorted(required_reports)
            },
        })
    except Exception as exc:
        receipt["error_type"] = type(exc).__name__
        receipt["error"] = str(exc)
    finally:
        if service is not None and run_id is not None:
            try:
                status = service.status(run_id)
                if status["state"] not in TERMINAL_STATES:
                    receipt["cleanup"] = service.cancel(run_id)
                    receipt["cleanup_terminal"] = _wait(service, run_id, 15)["state"]
            except Exception as cleanup_error:
                receipt["cleanup_error"] = str(cleanup_error)
        receipt["finished_at"] = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        receipt_path = run_dir / "probe-receipt.json"
        _write_json(receipt_path, receipt)
        _lock_down(run_dir)
        print(json.dumps({
            "status": receipt["status"],
            "run_dir": str(run_dir),
            "receipt_sha256": _sha256(receipt_path),
        }, sort_keys=True))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
