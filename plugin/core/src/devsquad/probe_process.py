"""Bounded ownership and cleanup for nongenerating native provider probes."""

from __future__ import annotations

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


def close_probe(process: subprocess.Popen[Any], *, start_identity: str | None) -> None:
    """Stop the owned group, confirm no live members, reap and close streams.

    The process must have been spawned by the caller with a new session and
    its identity captured before protocol use. Inspection/ownership failures
    raise rather than pretending cleanup or readiness was confirmed.
    """
    deadline = time.monotonic() + PROBE_CLEANUP_SECONDS
    def owned_group_exists() -> bool:
        if not _probe_group_exists(process.pid, deadline=deadline):
            return False
        observed = process_start_identity(process.pid)
        if observed is not None and observed != start_identity:
            raise ContractError("diagnostic process identity changed before cleanup")
        if observed is None and start_identity is None and process.returncode is not None:
            raise ContractError("diagnostic process ownership unavailable")
        return True

    def signal_group(value: int) -> None:
        # Recheck immediately before each signal. A recycled leader PID is
        # never authority to signal a new group.
        if owned_group_exists():
            try:
                os.killpg(process.pid, value)
            except ProcessLookupError:
                pass

    try:
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
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
