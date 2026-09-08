"""Bounded process-group supervision for persisted M2 attempts."""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
import hashlib
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Any, BinaryIO

from .contracts import ContractError, LaunchSpec
from .store import AttemptReservation, ConflictError, Store


def process_start_identity(pid: int) -> str | None:
    if sys.platform.startswith("linux"):
        try:
            fields = open(f"/proc/{pid}/stat", encoding="ascii").read().rsplit(") ", 1)[1].split()
            return f"linux-start-ticks:{fields[19]}"
        except (OSError, IndexError):
            return None
    if sys.platform == "darwin":
        class ProcBsdInfo(ctypes.Structure):
            _fields_ = [("prefix", ctypes.c_byte * 120), ("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64)]
        info = ProcBsdInfo()
        try:
            function = ctypes.CDLL("/usr/lib/libproc.dylib").proc_pidinfo
            function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
            function.restype = ctypes.c_int
            copied = function(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        except OSError:
            return None
        return f"darwin-start:{info.start_sec}:{info.start_usec}" if copied == ctypes.sizeof(info) else None
    return None


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


def _live_group_exists(pgid: int) -> bool:
    result = subprocess.run(["ps", "-axo", "pgid=,stat="], text=True, capture_output=True, check=False)
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0].isdigit() and int(fields[0]) == pgid and not fields[1].startswith("Z"):
            return True
    return False


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
        self.stream.close()
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
        try:
            stdin_stream: Any = subprocess.DEVNULL
            if spec.stdin_path is not None:
                stdin_path = os.path.realpath(spec.stdin_path)
                if os.path.islink(spec.stdin_path) or not os.path.isfile(stdin_path):
                    raise ContractError("stdin artifact must be a regular non-symlink file")
                stdin_stream = open(stdin_path, "rb")
            try:
                process = subprocess.Popen(list(spec.argv), cwd=spec.cwd, env=environment, stdin=stdin_stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            finally:
                if stdin_stream is not subprocess.DEVNULL:
                    stdin_stream.close()
        except Exception:
            self.store.fail_launch(reservation, "process spawn failed")
            raise
        assert process.stdout is not None and process.stderr is not None
        started = process_start_identity(process.pid)
        if started is None:
            returncode = process.poll()
            if returncode is None:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait(timeout=2)
                process.stdout.close(); process.stderr.close()
                self.store.fail_launch(reservation, "live child had no strong process identity")
                raise RuntimeError("live child identity could not be persisted")
            started = f"exited-before-observation:{returncode}:non-signalable"
            pgid = process.pid
        else:
            pgid = os.getpgid(process.pid)
        try:
            self.store.mark_attempt_running(reservation, process.pid, pgid, started)
        except Exception:
            if process.poll() is None:
                try: os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait(timeout=2)
            process.stdout.close(); process.stderr.close()
            self.store.fail_launch(reservation, "process identity could not be persisted")
            raise
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
        deadline = time.monotonic() + timeout_seconds
        returncode = None
        while returncode is None and time.monotonic() < deadline:
            returncode = handle.process.poll()
            if returncode is None:
                self.store.heartbeat_attempt(handle.reservation.run_id, handle.reservation.attempt_token, handle.reservation.supervisor_token)
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
        if returncode is None:
            self._terminate(handle)
            metadata = self._persist_output(handle)
            self.store.finish_attempt(handle.reservation.run_id, handle.reservation.attempt_token, "failed", {"error": "TIMEOUT", "output": metadata})
            return 124
        self._cleanup_owned_group(handle)
        metadata = self._persist_output(handle)
        terminal = "succeeded" if returncode == 0 else "failed"
        self.store.finish_attempt(handle.reservation.run_id, handle.reservation.attempt_token, terminal, {"returncode": returncode, "output": metadata})
        return returncode

    def _cleanup_owned_group(self, handle: RunningAttempt) -> None:
        pgid = handle.process.pid
        handle.process.poll()
        if not _live_group_exists(pgid): return
        os.killpg(pgid, signal.SIGTERM)
        deadline = time.monotonic() + self.grace_seconds
        while time.monotonic() < deadline:
            handle.process.poll()
            if not _live_group_exists(pgid): return
            time.sleep(0.05)
        try: os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError: return
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            handle.process.poll()
            if not _live_group_exists(pgid): return
            time.sleep(0.05)
        raise RuntimeError("owned process group remains after KILL")

    def _terminate(self, handle: RunningAttempt) -> None:
        attempt = self.store.active_attempt(handle.reservation.run_id)
        if not attempt or inspect_process(attempt["pid"], attempt["pgid"], attempt["process_start_id"]) != "live":
            raise ConflictError("process identity is not safe to signal")
        os.killpg(attempt["pgid"], signal.SIGTERM)
        deadline = time.monotonic() + self.grace_seconds
        while time.monotonic() < deadline:
            handle.process.poll()
            if not _live_group_exists(attempt["pgid"]): return
            time.sleep(0.05)
        try: os.killpg(attempt["pgid"], signal.SIGKILL)
        except ProcessLookupError: return
        try: handle.process.wait(timeout=2)
        except subprocess.TimeoutExpired: pass
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if not _live_group_exists(attempt["pgid"]): return
            time.sleep(0.05)
        raise RuntimeError("process group cleanup was not confirmed after KILL")

    def cancel(self, run_id: str, handle: RunningAttempt | None = None) -> int:
        version, attempt = self.store.request_cancel(run_id)
        if attempt is None:
            return version
        classification = inspect_process(attempt["pid"], attempt["pgid"], attempt["process_start_id"])
        if classification != "live":
            if classification == "dead":
                return self.store.block_recovery(run_id, attempt["attempt_token"], "child exited before cancellation cleanup", release_writer=True)
            return self.store.block_recovery(run_id, attempt["attempt_token"], "process identity is ambiguous or reused")
        if handle is None or handle.reservation.attempt_token != attempt["attempt_token"]:
            return self.store.block_recovery(run_id, attempt["attempt_token"], "live child requires owner reconciliation before cancellation")
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
        self.store.block_recovery(run_id, attempt["attempt_token"], reason, release_writer=classification == "dead")
        return "RECOVERY_REQUIRED"
