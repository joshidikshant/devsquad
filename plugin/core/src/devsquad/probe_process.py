"""Bounded ownership and cleanup for nongenerating native provider probes."""

from __future__ import annotations

from contextlib import contextmanager
import getpass
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any

from .contracts import ContractError
from .supervisor import process_start_identity

PROBE_CLEANUP_SECONDS = 2
PROBE_TERM_GRACE_SECONDS = 0.25
_POPEN_TYPE = subprocess.Popen


class _ProbeOwnershipUnavailable(ContractError):
    pass


def subscription_environment(home: Path | None = None) -> dict[str, str]:
    """Use saved subscription login without ambient keys/provider overrides."""
    return {
        "HOME": str(home if home is not None else Path.home()),
        "USER": os.environ.get("USER") or getpass.getuser(),
        "PATH": os.environ.get("PATH", ""),
    }


def capture_probe_identity(process: subprocess.Popen[Any]) -> str | None:
    """Capture immediately after spawning with start_new_session=True."""
    return process_start_identity(process.pid)


def _probe_group_exists(pgid: int, *, deadline: float) -> bool:
    """The supervisor's live-member rule with a diagnostic time bound."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ContractError("diagnostic cleanup deadline expired")
    try:
        inventory = subprocess.run(
            ["/bin/ps", "-axo", "pgid=,stat="], text=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=subscription_environment(), timeout=min(0.5, remaining), check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContractError("diagnostic process inventory unavailable") from exc
    if inventory.returncode != 0 or len(inventory.stdout) > 1024 * 1024:
        raise ContractError("diagnostic process inventory unavailable")
    parsed = 0
    for line in inventory.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2 or not fields[0].isdigit():
            raise ContractError("diagnostic process inventory malformed")
        parsed += 1
        if int(fields[0]) == pgid and not fields[1].startswith("Z"):
            return True
    if parsed == 0:
        raise ContractError("diagnostic process inventory empty")
    return False


@contextmanager
def _reap_guard(process: subprocess.Popen[Any], *, deadline: float, locked: bool = False):
    if locked or not isinstance(process, _POPEN_TYPE):
        yield
        return
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not process._waitpid_lock.acquire(timeout=remaining):
        raise ContractError("diagnostic cleanup deadline expired")
    try:
        yield
    finally:
        process._waitpid_lock.release()


def _retained_child_anchor(
    process: subprocess.Popen[Any], *, deadline: float, locked: bool = False,
) -> bool:
    """Verify an unreaped child without releasing its PID for reuse.

    Callers exclusively reap this Popen through wait/poll, never a separate
    os.waitpid/SIGCHLD reaper. Under its reap lock, waitid(WNOWAIT) or an exact
    kernel zombie-child row retains the PID through any final group signal.
    A live ps row or Popen.returncode alone is not this authority.
    """
    if not isinstance(process, _POPEN_TYPE):
        return False
    with _reap_guard(process, deadline=deadline, locked=locked):
        if process.returncode is not None:
            return False
        waitid = getattr(os, "waitid", None)
        if callable(waitid):
            try:
                result = waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except (OSError, AttributeError):
                return False
            return result is None or result.si_pid == process.pid
        # Python 3.12 on macOS lacks waitid. Only its unreaped zombie child
        # in our original new-session group can substitute; never a live row.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ContractError("diagnostic cleanup deadline expired")
        try:
            result = subprocess.run(
                ["/bin/ps", "-p", str(process.pid), "-o", "pid=,ppid=,pgid=,stat="],
                text=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=subscription_environment(), timeout=min(0.5, remaining), check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode != 0 or len(result.stdout) > 1024:
            return False
        rows = result.stdout.splitlines()
        if len(rows) != 1:
            return False
        fields = rows[0].split()
        return (len(fields) == 4 and all(field.isascii() and field.isdigit() for field in fields[:3])
                and tuple(map(int, fields[:3])) == (process.pid, os.getpid(), process.pid)
                and fields[3].startswith("Z"))


def _close_direct_child(process: subprocess.Popen[Any], *, deadline: float) -> None:
    """Stop only a kernel-confirmed live child, never its unowned group.

    WNOHANG verifies child authority under Popen's reap lock before each
    direct signal. An exited child is reaped, not signaled. No waitid or
    platform-specific ABI is needed for this fail-closed fallback.
    """
    if not isinstance(process, _POPEN_TYPE):
        raise ContractError("diagnostic direct-child ownership is unavailable")

    def child_live(value: int | None = None) -> bool:
        if time.monotonic() >= deadline:
            raise ContractError("diagnostic cleanup deadline expired")
        with _reap_guard(process, deadline=deadline):
            if process.returncode is not None:
                return False
            try:
                waited, status = os.waitpid(process.pid, os.WNOHANG)
            except OSError as exc:
                raise ContractError("diagnostic direct-child ownership is unavailable") from exc
            if waited == process.pid:
                process._handle_exitstatus(status)
                return False
            if waited != 0:
                raise ContractError("diagnostic direct-child ownership is unavailable")
            if value is not None:
                try:
                    os.kill(process.pid, value)
                except ProcessLookupError:
                    pass
            return True

    if child_live(signal.SIGTERM):
        grace = min(deadline, time.monotonic() + PROBE_TERM_GRACE_SECONDS)
        while child_live() and time.monotonic() < grace:
            time.sleep(min(0.02, max(0, grace - time.monotonic())))
        if child_live(signal.SIGKILL):
            while child_live():
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))


def close_probe(process: subprocess.Popen[Any], *, start_identity: str | None) -> None:
    """Stop the owned group, confirm no live members, reap and close streams.

    The process must have been spawned by the caller with a new session and
    its identity captured before protocol use. Callers retain exclusive Popen
    reaping until this function; no separate waitpid/SIGCHLD reaper may release
    the child PID. Inspection/ownership failures
    raise rather than pretending cleanup or readiness was confirmed. Missing
    captured identity permits no group signals: only independently verified
    direct-child stop/reap, followed by group absence or an unconfirmed error.
    """
    deadline = time.monotonic() + PROBE_CLEANUP_SECONDS
    def owned_group_exists(*, locked: bool = False) -> bool:
        if not _probe_group_exists(process.pid, deadline=deadline):
            return False
        observed = process_start_identity(process.pid)
        if observed is not None and observed != start_identity:
            raise ContractError("diagnostic process identity changed before cleanup")
        if observed is None and not _retained_child_anchor(process, deadline=deadline, locked=locked):
            raise _ProbeOwnershipUnavailable("diagnostic process ownership unavailable")
        return True

    def signal_group(value: int) -> None:
        # Recheck immediately before each signal. A recycled leader PID is
        # never authority to signal a new group.
        # The lock retains any unreaped child anchor through the signal, even
        # if another caller tries Popen.wait/poll during cleanup.
        with _reap_guard(process, deadline=deadline):
            if owned_group_exists(locked=True):
                try:
                    os.killpg(process.pid, value)
                except ProcessLookupError:
                    pass

    def close_without_group_authority() -> None:
        _close_direct_child(process, deadline=deadline)
        if _probe_group_exists(process.pid, deadline=deadline):
            raise ContractError("diagnostic process group cleanup unconfirmed")

    try:
        if start_identity is None:
            close_without_group_authority()
            return
        if owned_group_exists():
            signal_group(signal.SIGTERM)
            grace = min(deadline, time.monotonic() + PROBE_TERM_GRACE_SECONDS)
            # Keep an exited direct child unreaped while descendants survive;
            # its PID remains an ownership anchor through escalation.
            while owned_group_exists() and time.monotonic() < grace:
                time.sleep(min(0.02, max(0, grace - time.monotonic())))
            if owned_group_exists():
                signal_group(signal.SIGKILL)
                while owned_group_exists():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ContractError("diagnostic process group survived cleanup")
                    time.sleep(min(0.02, remaining))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ContractError("diagnostic cleanup deadline expired")
        process.wait(timeout=remaining)
    except _ProbeOwnershipUnavailable:
        close_without_group_authority()
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
