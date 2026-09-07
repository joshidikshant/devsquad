"""Bounded process-group supervision for persisted M2 attempts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import signal
import subprocess
import threading
import time
from typing import Any, BinaryIO

from .contracts import ContractError, LaunchSpec
from .store import AttemptReservation, ConflictError, Store


def process_start_identity(pid: int) -> str | None:
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], text=True, capture_output=True, check=False)
    value = result.stdout.strip()
    return value or None


def inspect_process(pid: int, pgid: int, expected_start: str) -> str:
    observed = process_start_identity(pid)
    if observed is None:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return "dead"
        except PermissionError:
            return "ambiguous"
        return "ambiguous"
    try:
        observed_pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return "ambiguous"
    return "live" if observed == expected_start and observed_pgid == pgid else "ambiguous"


class BoundedDrain:
    def __init__(self, stream: BinaryIO, limit: int):
        self.stream, self.limit = stream, limit
        self.content = bytearray()
        self.total_bytes = 0
        self.digest = hashlib.sha256()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while True:
            chunk = self.stream.read(65536)
            if not chunk:
                return
            self.total_bytes += len(chunk)
            self.digest.update(chunk)
            remaining = self.limit - len(self.content)
            if remaining > 0:
                self.content.extend(chunk[:remaining])

    def start(self) -> None:
        self.thread.start()

    def finish(self) -> dict[str, Any]:
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("output drain did not finish")
        return {"total_bytes": self.total_bytes, "captured_bytes": len(self.content), "truncated": self.total_bytes > len(self.content), "full_sha256": self.digest.hexdigest()}


@dataclass
class RunningAttempt:
    reservation: AttemptReservation
    process: subprocess.Popen[bytes]
    start_identity: str
    stdout: BoundedDrain
    stderr: BoundedDrain


class Supervisor:
    def __init__(self, store: Store, *, output_limit: int = 1024 * 1024, grace_seconds: float = 5.0):
        if output_limit <= 0 or grace_seconds < 0:
            raise ContractError("supervisor bounds must be positive")
        self.store, self.output_limit, self.grace_seconds = store, output_limit, grace_seconds

    def launch(self, run_id: str, expected_version: int, spec: LaunchSpec, owner_id: str, package_digest: str) -> RunningAttempt:
        reservation = self.store.reserve_attempt(run_id, expected_version, owner_id, package_digest)
        environment = os.environ.copy(); environment.update(spec.environment)
        process = subprocess.Popen(list(spec.argv), cwd=spec.cwd, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        assert process.stdout is not None and process.stderr is not None
        started = process_start_identity(process.pid)
        if started is None:
            process.wait(timeout=2)
            raise RuntimeError("child exited before its identity could be persisted")
        pgid = os.getpgid(process.pid)
        self.store.mark_attempt_running(reservation, process.pid, pgid, started)
        stdout, stderr = BoundedDrain(process.stdout, self.output_limit), BoundedDrain(process.stderr, self.output_limit)
        stdout.start(); stderr.start()
        return RunningAttempt(reservation, process, started, stdout, stderr)

    def _persist_output(self, handle: RunningAttempt) -> dict[str, Any]:
        stdout_meta, stderr_meta = handle.stdout.finish(), handle.stderr.finish()
        run_id, token = handle.reservation.run_id, handle.reservation.attempt_token
        stdout_id = self.store.store_artifact(run_id, f"{handle.reservation.attempt_id}.stdout", bytes(handle.stdout.content))
        stderr_id = self.store.store_artifact(run_id, f"{handle.reservation.attempt_id}.stderr", bytes(handle.stderr.content))
        metadata = {"stdout": stdout_meta, "stderr": stderr_meta}
        self.store.record_attempt_output(run_id, token, stdout_id, stderr_id, metadata)
        return metadata

    def wait(self, handle: RunningAttempt, timeout_seconds: float) -> int:
        try:
            returncode = handle.process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            self._terminate(handle)
            metadata = self._persist_output(handle)
            self.store.finish_attempt(handle.reservation.run_id, handle.reservation.attempt_token, "failed", {"error": "TIMEOUT", "output": metadata})
            return 124
        metadata = self._persist_output(handle)
        terminal = "succeeded" if returncode == 0 else "failed"
        self.store.finish_attempt(handle.reservation.run_id, handle.reservation.attempt_token, terminal, {"returncode": returncode, "output": metadata})
        return returncode

    def _terminate(self, handle: RunningAttempt) -> None:
        attempt = self.store.active_attempt(handle.reservation.run_id)
        if not attempt or inspect_process(attempt["pid"], attempt["pgid"], attempt["process_start_id"]) != "live":
            raise ConflictError("process identity is not safe to signal")
        os.killpg(attempt["pgid"], signal.SIGTERM)
        try:
            handle.process.wait(timeout=self.grace_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(attempt["pgid"], signal.SIGKILL)
            handle.process.wait(timeout=2)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try: os.killpg(attempt["pgid"], 0)
            except ProcessLookupError: return
            time.sleep(0.05)
        raise RuntimeError("process group cleanup was not confirmed")

    def cancel(self, run_id: str, handle: RunningAttempt | None = None) -> int:
        version, attempt = self.store.request_cancel(run_id)
        if attempt is None:
            return version
        classification = inspect_process(attempt["pid"], attempt["pgid"], attempt["process_start_id"])
        if classification != "live":
            if classification == "dead":
                return self.store.block_recovery(run_id, attempt["attempt_token"], "child exited before cancellation cleanup")
            return self.store.block_recovery(run_id, attempt["attempt_token"], "process identity is ambiguous or reused")
        if handle is None or handle.reservation.attempt_token != attempt["attempt_token"]:
            return version
        self._terminate(handle)
        metadata = self._persist_output(handle)
        return self.store.finish_attempt(run_id, attempt["attempt_token"], "cancelled", {"output": metadata})

    def recover(self, run_id: str) -> str:
        attempt = self.store.active_attempt(run_id)
        if not attempt:
            raise ConflictError("run has no active attempt")
        classification = inspect_process(attempt["pid"], attempt["pgid"], attempt["process_start_id"])
        if classification == "live":
            return "live_owned"
        reason = "confirmed dead child" if classification == "dead" else "process identity is ambiguous or reused"
        self.store.block_recovery(run_id, attempt["attempt_token"], reason)
        return "RECOVERY_REQUIRED"
