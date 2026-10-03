"""Versioned macOS default-deny read boundary for untrusted Council participants."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import subprocess
import os

from .contracts import CapabilityUnavailable, ContractError

BOUNDARY_VERSION = 1


def _literal(path: Path) -> str:
    # JSON escaping safely quotes Seatbelt strings without shell interpolation.
    return json.dumps(str(path.resolve(strict=True)))


def profile(*, executable: Path, evidence: Path, scratch: Path | None = None,
            dependencies: tuple[Path, ...] = ()) -> str:
    executable = executable.resolve(strict=True)
    evidence = evidence.resolve(strict=True)
    roots = [evidence, *[p.resolve(strict=True) for p in dependencies]]
    system_roots = [Path("/usr/lib"), Path("/System/Library")]
    ancestors = {Path("/")}
    for root in [executable, *roots, *system_roots, *([scratch.resolve(strict=True)] if scratch else [])]:
        ancestors.update(root.parents)
    literals = " ".join(f"(literal {_literal(p)})" for p in sorted(ancestors, key=str))
    read_roots = " ".join(f"(subpath {_literal(p)})" for p in [*system_roots, *roots])
    result = ("(version 1)\n(deny default)\n(allow process-fork)\n(allow sysctl-read)\n"
              f"(allow process-exec (literal {_literal(executable)}))\n"
              f"(allow file-read* (literal {_literal(executable)}) {literals} {read_roots})\n")
    if scratch is not None:
        result += f"(allow file-read* file-write* (subpath {_literal(scratch)}))\n"
    return result


def freeze_boundary(*, executable: Path, evidence: Path, dependencies: tuple[Path, ...] = (),
                    native_codex: bool = False) -> dict:
    if platform.system() != "Darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        raise CapabilityUnavailable("Council requires the verified macOS Seatbelt read boundary")
    value = profile(executable=executable, evidence=evidence, dependencies=dependencies)
    if native_codex:
        # Codex probes absent managed config paths during bootstrap. Metadata
        # only, not managed-config bytes/MCP configuration, may be inspected.
        value += ('(allow file-read-metadata (literal "/etc") (literal "/private/etc") '
                  '(subpath "/etc/codex") (subpath "/private/etc/codex"))\n'
                  '(allow file-read* (literal "/dev/urandom"))\n'
                  '(allow mach-lookup (global-name "com.apple.cfprefsd.daemon") (global-name "com.apple.cfprefsd.agent"))\n'
                  f'(allow ipc-posix-shm-read-data (ipc-posix-name "apple.cfprefs.{os.getuid()}v1") (ipc-posix-name "apple.cfprefs.daemonv1"))\n'
                  '(allow network-outbound (remote tcp "*:443"))\n')
    return {"schema_version": BOUNDARY_VERSION, "platform": platform.system(),
            "sandbox_binary": "/usr/bin/sandbox-exec", "profile": value,
            "profile_sha256": hashlib.sha256(value.encode()).hexdigest(),
            "executable_sha256": hashlib.sha256(executable.resolve(strict=True).read_bytes()).hexdigest(),
            "dependency_roots": [str(p.resolve(strict=True)) for p in dependencies],
            "native_codex": native_codex, "system_read_roots": ["/usr/lib", "/System/Library"],
            "uid": os.getuid(),
            "network": "outbound_tcp_443" if native_codex else "denied"}


def command(boundary: dict, argv: list[str], *, scratch: Path | None = None) -> list[str]:
    if (boundary.get("schema_version") != BOUNDARY_VERSION or boundary.get("platform") != "Darwin"
            or boundary.get("uid") != os.getuid()
            or platform.system() != "Darwin" or boundary.get("sandbox_binary") != "/usr/bin/sandbox-exec"
            or hashlib.sha256(boundary.get("profile", "").encode()).hexdigest() != boundary.get("profile_sha256")
            or hashlib.sha256(Path(argv[0]).read_bytes()).hexdigest() != boundary.get("executable_sha256")):
        raise CapabilityUnavailable("frozen Council read boundary changed or is unavailable")
    value = boundary["profile"]
    if scratch is not None:
        # Provider-owned auth/session cache; never contains peer artifacts or the ledger.
        value += f"(allow file-read* file-write* (subpath {_literal(scratch)}))\n"
        for ancestor in scratch.resolve(strict=True).parents:
            value += f"(allow file-read* (literal {_literal(ancestor)}))\n"
    return [boundary["sandbox_binary"], "-p", value, *argv]


def probe(boundary: dict, *, executable: Path, own_file: Path, forbidden: tuple[Path, ...]) -> None:
    for path, allowed in [(own_file, True), *[(p, False) for p in forbidden]]:
        result = subprocess.run(command(boundary, [str(executable), str(path)]),
                                capture_output=True, timeout=3, check=False)
        if (allowed and result.returncode != 0) or (not allowed and result.returncode == 0):
            raise CapabilityUnavailable("Council filesystem read isolation probe failed")


def verify_read_boundary(boundary: dict, *, own_file: Path, forbidden: tuple[Path, ...]) -> None:
    """Probe the frozen file rules; the diagnostic cat executable alone is added."""
    value = boundary["profile"] + '(allow process-exec (literal "/bin/cat"))\n(allow file-read* (literal "/bin/cat") (literal "/bin"))\n'
    for path, allowed in [(own_file, True), *[(p, False) for p in forbidden]]:
        result = subprocess.run([boundary["sandbox_binary"], "-p", value, "/bin/cat", str(path.resolve(strict=True))],
                                capture_output=True, timeout=3, check=False)
        if (allowed and result.returncode != 0) or (not allowed and result.returncode == 0):
            raise CapabilityUnavailable("Council default-deny peer/runtime read probe failed")


def verify_native_bootstrap(boundary: dict, *, executable: Path, expected_version: str, evidence: Path) -> None:
    """Non-generating app-server bootstrap under the exact frozen boundary."""
    import tempfile
    from .codex_protocol import JsonLinePeer, initialize_request, receive_response
    from .diagnostics import _probe_output
    from .probe_process import capture_probe_identity, close_probe
    with tempfile.TemporaryDirectory(prefix="devsquad-council-bootstrap-") as temporary:
        scratch = Path(temporary).resolve(strict=True)
        environment = {"PATH": "/usr/bin:/bin", "CODEX_HOME": str(scratch), "HOME": str(scratch), "TMPDIR": str(scratch)}
        code, output = _probe_output(command(boundary, [str(executable), "--version"], scratch=scratch),
                                     project=evidence, environment=environment)
        if code != 0 or output.strip() != expected_version:
            raise CapabilityUnavailable("Council native binary cannot start inside frozen read isolation")
        process = subprocess.Popen(command(boundary, [str(executable), "--disable", "apps", "--disable", "plugins",
                "app-server", "--listen", "stdio://"], scratch=scratch), cwd=evidence, env=environment,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        start_identity = capture_probe_identity(process)
        try:
            if start_identity is None:
                raise CapabilityUnavailable("Council native bootstrap ownership identity is unavailable")
            peer = JsonLinePeer(process.stdout, process.stdin, max_frame_bytes=16 * 1024)
            peer.send(initialize_request(1))
            response = receive_response(peer, 1, timeout_seconds=3)
            if "error" in response or not isinstance(response.get("result"), dict):
                raise CapabilityUnavailable("Council native isolated bootstrap is unavailable")
        except (OSError, EOFError, TimeoutError) as exc:
            raise CapabilityUnavailable("Council native isolated bootstrap is unavailable") from exc
        finally:
            # The shared bounded helper is the sole cleanup authority. Failure
            # to prove ownership or absence remains unavailable, never ready.
            close_probe(process, start_identity=start_identity)


def verify_native_network(boundary: dict) -> None:
    """Fail before generation until exact-boundary backend connectivity is proven.

    Initialization and model/list can use local/fallback catalog data. They are
    deliberately not a network attestation. No flag or unsafe launch fallback
    can override this currently unavailable native capability.
    """
    raise CapabilityUnavailable(
        "native Council is unavailable: a genuine non-generating HTTPS backend response "
        "under the exact frozen sandbox boundary has not been verified"
    )
