#!/usr/bin/env python3
"""Opt-in, bounded live smoke for the production Codex native M1 path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

CORE_SRC = Path(__file__).resolve().parents[3] / "plugin" / "core" / "src"
sys.path.insert(0, str(CORE_SRC))

from devsquad.adapters import AdapterManifest, harness_version, prepare_native_codex, prepare_native_codex_from_catalog
from devsquad.catalog import update_last_good
from devsquad.codex_protocol import (
    JsonLinePeer,
    NativeTurnState,
    discover_models,
    initialize_request,
    initialized_notification,
    receive_response,
    thread_start_request,
    turn_start_request,
)


class RecordingPeer:
    def __init__(self, peer: JsonLinePeer, transcript: Any):
        self.peer, self.transcript = peer, transcript

    def send(self, message: dict[str, Any]) -> None:
        self.transcript.write(json.dumps({"direction": "send", "message": message}) + "\n")
        self.transcript.flush()
        self.peer.send(message)

    def receive(self, timeout_seconds: float) -> dict[str, Any]:
        message = self.peer.receive(timeout_seconds)
        self.transcript.write(json.dumps({"direction": "receive", "message": message}) + "\n")
        self.transcript.flush()
        return message


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stop(process: subprocess.Popen[str]) -> str:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    return f"exit:{process.returncode}"


def _server(spec: Any, stderr_path: Path, transcript_path: Path):
    stderr_stream = stderr_path.open("w", encoding="utf-8")
    transcript = transcript_path.open("w", encoding="utf-8")
    environment = os.environ.copy()
    environment.update(spec.env)
    process = subprocess.Popen(
        list(spec.argv), cwd=spec.cwd, env=environment, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=stderr_stream, text=True, bufsize=1,
    )
    assert process.stdin is not None and process.stdout is not None
    peer = RecordingPeer(JsonLinePeer(process.stdout, process.stdin), transcript)
    return process, peer, stderr_stream, transcript


def _initialize(peer: RecordingPeer, timeout: float) -> None:
    peer.send(initialize_request(1))
    response = receive_response(peer, 1, timeout_seconds=timeout)
    if "error" in response:
        raise RuntimeError(f"initialize failed: {response['error']}")
    peer.send(initialized_notification())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true", help="required acknowledgement for a live subscription-backed probe")
    parser.add_argument("--output-dir", type=Path, default=Path.home() / ".devsquad" / "private-probes")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    if not args.run_live:
        parser.error("--run-live is required")
    if args.timeout < 5 or args.timeout > 60:
        parser.error("--timeout must be between 5 and 60 seconds")

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=CORE_SRC, text=True, capture_output=True, check=True).stdout.strip()
    run_dir = args.output_dir.expanduser().resolve() / f"native-codex-{stamp}-{revision[:12]}"
    run_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    os.chmod(run_dir, 0o700)
    receipt: dict[str, Any] = {"started_at": stamp, "revision": revision, "status": "failed", "cleanup": []}
    manifest_path = CORE_SRC.parent / "adapters" / "codex" / "adapter.json"
    manifest = AdapterManifest.load(manifest_path)
    binary = manifest.resolve_binary()
    if not binary:
        raise RuntimeError("codex executable is unavailable")
    version = harness_version(binary)
    if not version:
        raise RuntimeError("could not determine codex version")

    try:
        with tempfile.TemporaryDirectory(prefix="devsquad-native-smoke-") as workspace_name:
            workspace = Path(workspace_name)
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            bootstrap = prepare_native_codex(
                manifest.with_model_efforts({"bootstrap": ("low",)}), cwd=str(workspace), model="bootstrap",
                effort="low", permission="read_only", timeout_seconds=args.timeout, harness_version_value=version,
            )
            first, peer, stderr_stream, transcript = _server(bootstrap, run_dir / "discovery.stderr.log", run_dir / "discovery.jsonl")
            try:
                _initialize(peer, args.timeout)
                models = discover_models(peer, first_request_id=10, timeout_seconds=args.timeout)
            finally:
                receipt["cleanup"].append({"discovery": _stop(first)})
                transcript.close(); stderr_stream.close()

            snapshot = update_last_good(run_dir / "catalog.json", harness="codex", version=version, models=models, complete=True)
            candidates = [m for m in snapshot["models"] if m.get("supported_efforts")]
            if not candidates:
                raise RuntimeError("discovery returned no model with verified effort metadata")
            selected = next((m for m in candidates if m.get("is_default")), candidates[0])
            supported = selected["supported_efforts"]
            effort = next((name for name in ("minimal", "low", "medium", "high", "xhigh") if name in supported), supported[0])
            spec = prepare_native_codex_from_catalog(
                manifest, snapshot, cwd=str(workspace), model=selected["id"], effort=effort,
                permission="read_only", timeout_seconds=args.timeout, harness_version_value=version,
            )
            second, peer, stderr_stream, transcript = _server(spec, run_dir / "turn.stderr.log", run_dir / "turn.jsonl")
            try:
                _initialize(peer, args.timeout)
                peer.send(thread_start_request(20, cwd=str(workspace), model=selected["id"], permission="read_only"))
                thread_response = receive_response(peer, 20, timeout_seconds=args.timeout)
                result = thread_response.get("result", {})
                thread = result.get("thread", {}) if isinstance(result, dict) else {}
                thread_id = thread.get("id") or result.get("threadId")
                if not isinstance(thread_id, str) or not thread_id:
                    raise RuntimeError(f"thread/start returned no thread id: {thread_response}")
                peer.send(turn_start_request(21, thread_id=thread_id, prompt="Reply with exactly DEVSQUAD_M1_NATIVE_OK. Do not use tools.", model=selected["id"], effort=effort, cwd=str(workspace), permission="read_only"))
                turn_response = receive_response(peer, 21, timeout_seconds=args.timeout)
                turn_result = turn_response.get("result", {})
                turn = turn_result.get("turn", {}) if isinstance(turn_result, dict) else {}
                turn_id = turn.get("id") or turn_result.get("turnId")
                if not isinstance(turn_id, str) or not turn_id:
                    raise RuntimeError(f"turn/start returned no turn id: {turn_response}")
                state = NativeTurnState(thread_id=thread_id, turn_id=turn_id)
                deadline = time.monotonic() + args.timeout
                while not state.terminal:
                    state.consume(peer.receive(max(0.01, deadline - time.monotonic())))
                output = "".join(state.output).strip()
                if state.terminal_status != "completed" or output != "DEVSQUAD_M1_NATIVE_OK":
                    raise RuntimeError(f"native verdict was not successful: status={state.terminal_status!r}, output={output!r}")
                receipt.update({"status": "passed", "harness_version": version, "model": selected["id"], "effort": effort, "permission": "read_only", "terminal_status": state.terminal_status})
            finally:
                receipt["cleanup"].append({"turn": _stop(second)})
                transcript.close(); stderr_stream.close()
    except Exception as exc:
        receipt["error_type"] = type(exc).__name__
        receipt["error"] = str(exc)
    finally:
        receipt["finished_at"] = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        for path in run_dir.iterdir():
            if path.is_file(): os.chmod(path, 0o600)
        receipt["private_artifacts"] = {p.name: _sha256(p) for p in sorted(run_dir.iterdir()) if p.is_file()}
        receipt_path = run_dir / "receipt.json"
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        os.chmod(receipt_path, 0o600)
        print(json.dumps({"status": receipt["status"], "run_dir": str(run_dir), "receipt_sha256": _sha256(receipt_path)}))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
