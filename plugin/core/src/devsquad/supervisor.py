"""Bounded process-group supervision for persisted M2 attempts."""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
import hashlib
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Any, BinaryIO
from pathlib import Path
import json

from .contracts import ContractError, LaunchSpec
from .store import AttemptReservation, ConflictError, Store, canonical_json
from .workflows import decode_branch_review_evidence


def _open_stdin_artifact(path: str) -> BinaryIO:
    candidate = Path(path)
    if candidate.is_symlink():
        raise ContractError("stdin artifact must be a regular non-symlink file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(candidate, flags)
    except OSError as exc:
        raise ContractError("stdin artifact must be a readable regular non-symlink file") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ContractError("stdin artifact must be a regular non-symlink file")
        return os.fdopen(descriptor, "rb")
    except Exception:
        os.close(descriptor)
        raise


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
    result = subprocess.run(["/bin/ps", "-axo", "pgid=,stat="], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError("process-group inventory failed")
    parsed = 0
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 2 or not fields[0].isdigit():
            raise RuntimeError("process-group inventory was malformed")
        parsed += 1
        if int(fields[0]) == pgid and not fields[1].startswith("Z"):
            return True
    if parsed == 0:
        raise RuntimeError("process-group inventory was empty")
    return False


def _read_child_identity(path: Path) -> tuple[int, int, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("child identity record is not a regular file")
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or set(value) != {"pid", "pgid", "process_start_id"}:
        raise ValueError("child identity record fields are invalid")
    if (type(value["pid"]) is not int or value["pid"] <= 0
            or type(value["pgid"]) is not int or value["pgid"] <= 0
            or not isinstance(value["process_start_id"], str)
            or not value["process_start_id"]):
        raise ValueError("child identity record values are invalid")
    return value["pid"], value["pgid"], value["process_start_id"]


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


@dataclass
class DurableAttempt:
    reservation: AttemptReservation
    process: subprocess.Popen[bytes]
    paths: dict[str, str]


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
                stdin_stream = _open_stdin_artifact(spec.stdin_path)
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

    def launch_durable(self, run_id: str, expected_version: int, spec: LaunchSpec, owner_id: str, package_digest: str) -> DurableAttempt:
        reservation = self.store.reserve_attempt(run_id, expected_version, owner_id, package_digest)
        directory = self.store.artifacts / run_id / f".{reservation.attempt_id}.spool"
        directory.mkdir(parents=True, exist_ok=False)
        paths = {name: str(directory / filename) for name, filename in {
            "stdout_spool":"stdout.capture", "stderr_spool":"stderr.capture", "stdout_meta":"stdout.meta.json", "stderr_meta":"stderr.meta.json", "exit_record":"exit.json", "child_record":"child.json"}.items()}
        gate_read, gate_write = os.pipe()
        process = None
        stdin_stream = None
        identity_committed = False
        gate_released = False
        try:
            if spec.stdin_path is not None:
                stdin_stream = _open_stdin_artifact(spec.stdin_path)
            command=[sys.executable,"-P","-m","devsquad.attempt_runner","--gate-fd",str(gate_read),
                "--database",str(self.store.database),"--artifacts",str(self.store.artifacts),
                "--run-id",run_id,"--attempt-token",reservation.attempt_token,
                "--supervisor-token",str(reservation.supervisor_token),"--stdout",paths["stdout_spool"],
                "--stderr",paths["stderr_spool"],"--exit-record",paths["exit_record"],
                "--child-record",paths["child_record"],"--limit",str(self.output_limit),
                "--timeout",str(spec.timeout_seconds),"--grace",str(self.grace_seconds),"--",*spec.argv]
            if stdin_stream is not None:
                command[4:4] = ["--stdin-fd", str(stdin_stream.fileno())]
            environment=os.environ.copy(); environment.update(spec.environment)
            passed_fds=(gate_read,) if stdin_stream is None else (gate_read,stdin_stream.fileno())
            process=subprocess.Popen(command,cwd=spec.cwd,env=environment,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,pass_fds=passed_fds,start_new_session=True)
            if stdin_stream is not None:
                stdin_stream.close(); stdin_stream=None
            os.close(gate_read)
            started=process_start_identity(process.pid)
            if started is None: raise RuntimeError("gated child has no strong process identity")
            self.store.mark_attempt_running(reservation,process.pid,process.pid,started,paths)
            identity_committed = True
            self._release_runner_gate(gate_write)
            gate_released = True
            os.close(gate_write)
            return DurableAttempt(reservation,process,paths)
        except Exception:
            if stdin_stream is not None:
                stdin_stream.close()
            try: os.close(gate_write)
            except OSError: pass
            for fd in (gate_read,):
                try: os.close(fd)
                except OSError: pass
            if process and process.poll() is None:
                try: os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait(timeout=2)
            if not identity_committed:
                self.store.fail_launch(reservation,"durable gated launch failed")
            elif not gate_released:
                self.store.recover_unstarted_attempt(
                    run_id, reservation.attempt_token,
                    "runner gate could not be released",
                )
            else:
                self.store.block_recovery(
                    run_id, reservation.attempt_token,
                    "coordinator failed after releasing the runner gate",
                )
            raise

    @staticmethod
    def _release_runner_gate(gate_write: int) -> None:
        os.write(gate_write, b"1")

    def wait_durable(self, handle: DurableAttempt, timeout_seconds: float) -> int:
        deadline=time.monotonic()+timeout_seconds + self.grace_seconds + 5
        while handle.process.poll() is None and time.monotonic()<deadline:
            time.sleep(.1)
        if handle.process.poll() is None:
            self.store.request_cancel(handle.reservation.run_id)
            handle.process.wait(timeout=self.grace_seconds+5)
        returncode=handle.process.wait(timeout=2)
        self.import_durable(handle.reservation.run_id)
        return returncode

    def _commit_review_handoff(
        self,
        run_id: str,
        attempt: dict[str, Any],
        stream_artifacts: list[dict[str, Any]],
        metadata: dict[str, Any],
        snapshot: dict[str, Any],
        stdout: bytes,
    ) -> str:
        evidence = decode_branch_review_evidence(stdout, snapshot)
        suffix = attempt["id"]
        documents = {
            f"review-{suffix}.json": evidence["review"],
            f"checks-{suffix}.json": {
                "schema_version": 1,
                "candidate_sha256": evidence["candidate_sha256"],
                "target_oid": evidence["target_oid"],
                "results": evidence["checks"],
            },
            f"evaluation-{suffix}.json": evidence["evaluation"],
            f"review-attempt-{suffix}.json": evidence["attempt"],
        }
        artifacts = list(stream_artifacts)
        evidence_references = []
        for name, document in documents.items():
            content = (canonical_json(document) + "\n").encode()
            path, digest, size = self.store.finalize_artifact(run_id, name, content)
            artifacts.append({
                "name": name,
                "path": path,
                "sha256": digest,
                "byte_size": size,
            })
            evidence_references.append({"name": name, "sha256": digest})
        packet = {
            "schema_version": 1,
            "workflow": "branch-review",
            "candidate_sha256": evidence["candidate_sha256"],
            "base_oid": evidence["base_oid"],
            "target_oid": evidence["target_oid"],
            "review": evidence["review"],
            "checks": evidence["checks"],
            "evaluation": evidence["evaluation"],
            "attempt_id": attempt["id"],
            "attempt": evidence["attempt"],
            "artifacts": evidence_references,
            "instructions": (
                "Inspect the bound review and check evidence, then submit exactly one "
                "accept, revise, or reject disposition."
            ),
        }
        return self.store.commit_durable_handoff(
            run_id,
            attempt["attempt_token"],
            artifacts,
            metadata,
            packet,
        )

    def import_durable(self, run_id: str) -> str:
        attempt=self.store.attempt(run_id)
        if not attempt or attempt["status"] not in {"running","cancelling"}:
            return "already_finalized"
        identity=inspect_process(attempt["pid"],attempt["pgid"],attempt["process_start_id"])
        if identity=="live": return "live"
        receipt_path=Path(attempt["exit_record"] or "")
        if identity=="ambiguous" and not receipt_path.is_file():
            self.store.block_recovery(run_id,attempt["attempt_token"],"attempt runner identity is ambiguous")
            return "ownership_ambiguous"
        if not receipt_path.is_file():
            child_path=Path(attempt["child_record"] or "")
            if child_path.exists() or child_path.is_symlink():
                try:
                    child = _read_child_identity(child_path)
                    if inspect_process(*child)!="dead":
                        self.store.block_recovery(run_id,attempt["attempt_token"],"runner died while its child may still be live")
                        return "ownership_ambiguous"
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    self.store.block_recovery(run_id,attempt["attempt_token"],"child identity record is invalid")
                    return "ownership_ambiguous"
                self.store.block_recovery(run_id,attempt["attempt_token"],"runner and child died without an exit receipt",release_writer=True)
                return "recovery_required"
            try:
                _, disposition = self.store.recover_unstarted_attempt(
                    run_id,
                    attempt["attempt_token"],
                    "runner died before publishing the gated child identity",
                )
                return disposition
            except ConflictError:
                current = self.store.attempt(run_id)
                run = self.store.run(run_id)
                if (current and current["attempt_token"] == attempt["attempt_token"]
                        and current["status"] == "recovery_required"
                        and run["state"] == "queued"):
                    return "requeued"
                if run["state"] in {"succeeded", "failed", "cancelled"}:
                    return run["state"]
                raise
        try:
            receipt=json.loads(receipt_path.read_text())
            if (type(receipt.get("returncode")) is not int
                    or type(receipt.get("cancelled")) is not bool
                    or type(receipt.get("timed_out")) is not bool
                    or (receipt["cancelled"] and receipt["timed_out"])):
                raise ValueError("invalid receipt")
            metadata={name:receipt[name] for name in ("stdout","stderr")}
            artifacts=[]
            captures={}
            for stream,column in (("stdout","stdout_spool"),("stderr","stderr_spool")):
                data=Path(attempt[column]).read_bytes(); meta=metadata[stream]
                if hashlib.sha256(data).hexdigest()!=meta["captured_sha256"] or len(data)!=meta["captured_bytes"]:
                    raise ValueError("capture hash mismatch")
                captures[stream]=data
                logical=f"{attempt['id']}.{stream}"
                path,digest,size=self.store.finalize_artifact(run_id,logical,data)
                artifacts.append({"name":logical,"path":path,"sha256":digest,"byte_size":size})
            snapshot=json.loads(self.store.run(run_id)["mutable_snapshot"])
            workflow_review = (
                "internal_review_fixture" in snapshot or "review_adapter" in snapshot
            )
            semantic_error=None
            if (workflow_review and not receipt["cancelled"]
                    and not receipt["timed_out"] and receipt["returncode"]==0):
                try:
                    return self._commit_review_handoff(
                        run_id, attempt, artifacts, metadata, snapshot, captures["stdout"],
                    )
                except ContractError as exc:
                    semantic_error=str(exc)
                    receipt["error"]="WORKFLOW_OUTPUT_INVALID"
                    receipt["message"]=semantic_error
            receipt_bytes=canonical_json(receipt).encode()
            path,digest,size=self.store.finalize_artifact(run_id,"result-receipt.json",receipt_bytes)
            artifacts.append({"name":"result-receipt.json","path":path,"sha256":digest,"byte_size":size})
            terminal="cancelled" if receipt["cancelled"] else ("failed" if receipt["timed_out"] or receipt["returncode"]!=0 or semantic_error else "succeeded")
            payload={"returncode":receipt["returncode"],"receipt":"result-receipt.json"}
            if receipt["timed_out"]: payload["error"]="TIMEOUT"
            if semantic_error:
                payload["error"]="WORKFLOW_OUTPUT_INVALID"
                payload["message"]=semantic_error
            return self.store.commit_durable_import(
                run_id,attempt["attempt_token"],artifacts,metadata,terminal,payload,
            )
        except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError):
            try:
                self.store.block_recovery(run_id,attempt["attempt_token"],"durable receipt or capture is invalid")
                return "ownership_ambiguous"
            except ConflictError:
                current=self.store.attempt(run_id); run=self.store.run(run_id)
                if current and current["attempt_token"]==attempt["attempt_token"] and current["status"]=="finished" and run["state"] in {"succeeded","failed","cancelled"}:
                    return run["state"]
                raise

    def _terminate_durable(self, handle: DurableAttempt) -> None:
        attempt=self.store.active_attempt(handle.reservation.run_id)
        if not attempt or inspect_process(attempt["pid"],attempt["pgid"],attempt["process_start_id"])!="live":
            raise ConflictError("durable process identity is unsafe to signal")
        os.killpg(attempt["pgid"],signal.SIGTERM)
        deadline=time.monotonic()+self.grace_seconds
        while handle.process.poll() is None and time.monotonic()<deadline: time.sleep(.05)
        if handle.process.poll() is None: os.killpg(attempt["pgid"],signal.SIGKILL)

    def cancel_orphan(self, run_id: str) -> int:
        """Cancel a blocked worker whose durable runner is confirmed dead."""
        attempt = self.store.attempt(run_id)
        if (not attempt or attempt["status"] not in {
                "ownership_ambiguous", "recovery_required", "cancelling",
        }):
            raise ConflictError("run has no orphaned attempt to cancel")
        runner_identity = (
            attempt["pid"], attempt["pgid"], attempt["process_start_id"],
        )
        if all(value is None for value in runner_identity):
            runner = "dead"
        elif (type(runner_identity[0]) is int and runner_identity[0] > 0
                and type(runner_identity[1]) is int and runner_identity[1] > 0
                and isinstance(runner_identity[2], str) and runner_identity[2]):
            runner = inspect_process(*runner_identity)
        else:
            raise ConflictError("orphan runner identity record is invalid")
        if runner != "dead":
            raise ConflictError("orphan runner identity is not confirmed dead")

        child = None
        classification = "dead"
        child_record = attempt["child_record"]
        if child_record:
            child_path = Path(child_record)
        else:
            child_path = None
        if child_path is not None and (child_path.exists() or child_path.is_symlink()):
            try:
                child = _read_child_identity(child_path)
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise ConflictError("orphan child identity record is invalid") from exc
            classification = inspect_process(*child)
        if classification == "ambiguous":
            raise ConflictError("orphan child identity is ambiguous or reused")

        self.store.request_recovery_cancel(run_id, attempt["attempt_token"])
        if child is not None and classification == "live":
            classification = inspect_process(*child)
            if classification == "ambiguous":
                raise ConflictError("orphan child identity changed before cancellation")
            if classification == "live":
                try:
                    os.killpg(child[1], signal.SIGTERM)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + self.grace_seconds
                while _live_group_exists(child[1]) and time.monotonic() < deadline:
                    time.sleep(0.05)
                if _live_group_exists(child[1]):
                    try:
                        os.killpg(child[1], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    deadline = time.monotonic() + max(self.grace_seconds, 2.0)
                    while _live_group_exists(child[1]) and time.monotonic() < deadline:
                        time.sleep(0.05)
                if _live_group_exists(child[1]):
                    raise ConflictError("orphan child survived bounded cancellation")
        return self.store.finish_recovery_cancel(
            run_id, attempt["attempt_token"], "orphaned worker cleanup confirmed",
        )

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
            try:
                self._terminate(handle)
            except ConflictError:
                self.store.block_recovery(handle.reservation.run_id, handle.reservation.attempt_token, "timeout raced with process identity change")
                raise
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
        try:
            self._terminate(handle)
        except ConflictError:
            return self.store.block_recovery(run_id, attempt["attempt_token"], "cancellation raced with process identity change")
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
