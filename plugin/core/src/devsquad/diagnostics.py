"""Read-only readiness reporting shared by CLI and MCP surfaces."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import time
from typing import Any

from . import __version__
from .adapters import AdapterManifest
from .codex_protocol import (
    JsonLinePeer, initialize_request, initialized_notification,
    receive_response, request,
)
from .contracts import ContractError
from .integrations import (
    LocalIntegrationManager,
    load_integrations,
)
from .probe_process import (
    capture_probe_identity,
    close_probe as _close_probe,
    subscription_environment as _environment,
)

SOURCE_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = (
    SOURCE_ROOT
    if (SOURCE_ROOT / "adapters").is_dir()
    else Path(sys.prefix) / "share" / "devsquad"
)
PROBE_TIMEOUT_SECONDS = 3
AUTH_TIMEOUT_SECONDS = 5
MAX_PROBE_BYTES = 16 * 1024
VERSION_PATTERN = re.compile(
    r"(?:codex-cli )?\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?(?: \(Claude Code\))?"
)


def _probe_output(
    argv: list[str], *, project: Path, environment: dict[str, str],
) -> tuple[int, str]:
    """Bound time and bytes before decoding any provider-controlled output."""
    deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS
    process = subprocess.Popen(
        argv, cwd=project, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    start_identity = capture_probe_identity(process)
    try:
        assert process.stdout is not None
        chunks = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError("diagnostic probe timed out")
                chunk = os.read(process.stdout.fileno(), min(4096, MAX_PROBE_BYTES + 1 - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
                if len(chunks) > MAX_PROBE_BYTES:
                    raise ContractError("diagnostic probe exceeds byte bound")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("diagnostic probe timed out")
        return process.wait(timeout=remaining), chunks.decode("utf-8")
    finally:
        _close_probe(process, start_identity=start_identity)


def _resolve_adapter(
    manifest: AdapterManifest, *, project: Path, environment: dict[str, str],
) -> tuple[str | None, str | None]:
    candidates = tuple(dict.fromkeys(
        binary for name in manifest.binary_candidates
        if (binary := shutil.which(name, path=environment["PATH"])) is not None
    ))
    first = (None, None)
    for binary in candidates:
        try:
            code, output = _probe_output([binary, "--version"], project=project, environment=environment)
            version = output.strip() if code == 0 else None
            # A version banner can contain a login error or secrets. Only a
            # bounded canonical version is safe to retain in the public report.
            if version is not None and VERSION_PATTERN.fullmatch(version) is None:
                version = None
        except (ContractError, OSError, TimeoutError, UnicodeError, subprocess.TimeoutExpired):
            version = None
        if first[0] is None:
            first = (binary, version)
        if version in manifest.verified_versions:
            return binary, version
    return first


def _authentication(
    *, authenticated: bool | None = None, method: str | None = None,
    subscription_supported: bool = False, check: str | None = None,
    reason: str | None = None, next_action: str | None = None,
) -> dict[str, Any]:
    return {
        "status": (
            "authenticated" if authenticated is True
            else "unauthenticated" if authenticated is False else "unknown"
        ),
        "authenticated": authenticated,
        "method": method,
        "subscription_supported": subscription_supported,
        "check": check,
        "reason": reason,
        "next_action": next_action,
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate diagnostic field")
        value[key] = item
    return value


def _claude_auth(binary: str, *, project: Path, environment: dict[str, str]) -> dict[str, Any]:
    check = "claude auth status --json"
    try:
        code, output = _probe_output(
            [binary, "auth", "status", "--json"], project=project, environment=environment,
        )
        value = json.loads(output, object_pairs_hook=_unique_object)
        if not isinstance(value, dict) or type(value.get("loggedIn")) is not bool:
            raise ValueError("invalid authentication state")
        logged_in, method = value["loggedIn"], value.get("authMethod")
        if not isinstance(method, str) or method not in {"none", "claude.ai", "oauth_token", "api_key", "api_key_helper", "third_party"}:
            raise ValueError("unknown authentication method")
        if (code != (0 if logged_in else 1)
                or (logged_in and method == "none")
                or (not logged_in and method != "none")):
            raise ValueError("inconsistent authentication state")
    except (ContractError, OSError, TimeoutError, UnicodeError, ValueError, RecursionError, subprocess.TimeoutExpired):
        return _authentication(check=check, reason="auth_check_failed")
    subscription_supported = logged_in and method == "claude.ai"
    return _authentication(
        authenticated=logged_in, method=method, check=check,
        subscription_supported=subscription_supported,
        reason=(None if subscription_supported else "subscription_login_required"),
        next_action=None if subscription_supported else "claude auth login",
    )


def _codex_auth(binary: str, *, project: Path, environment: dict[str, str]) -> dict[str, Any]:
    check = "codex account/read"
    deadline = time.monotonic() + AUTH_TIMEOUT_SECONDS
    process = None
    start_identity = None
    try:
        process = subprocess.Popen(
            [binary, "app-server", "--listen", "stdio://"],
            cwd=project, env=environment, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            bufsize=1, start_new_session=True,
        )
        start_identity = capture_probe_identity(process)
        assert process.stdin is not None and process.stdout is not None
        peer = JsonLinePeer(process.stdout, process.stdin, max_frame_bytes=MAX_PROBE_BYTES)
        peer.send(initialize_request(1))
        initialized = receive_response(peer, 1, timeout_seconds=max(0.001, deadline - time.monotonic()))
        if "error" in initialized or not isinstance(initialized.get("result"), dict):
            raise ContractError("native initialization unavailable")
        peer.send(initialized_notification())
        peer.send(request(2, "account/read", {"refreshToken": False}))
        reply = receive_response(peer, 2, timeout_seconds=max(0.001, deadline - time.monotonic()))
        value = reply.get("result")
        if ("error" in reply or not isinstance(value, dict) or "account" not in value
                or type(value.get("requiresOpenaiAuth")) is not bool):
            raise ContractError("native authentication unavailable")
        account = value["account"]
        if account is None:
            return _authentication(
                authenticated=False if value["requiresOpenaiAuth"] else None,
                check=check, reason="subscription_login_required",
                next_action="codex login",
            )
        if (not isinstance(account, dict) or not isinstance(account.get("type"), str)
                or account["type"] not in {"chatgpt", "apiKey"}):
            raise ContractError("native authentication method unknown")
        method = account["type"]
        subscription_supported = method == "chatgpt" and value["requiresOpenaiAuth"]
        return _authentication(
            authenticated=True, method=method, check=check,
            subscription_supported=subscription_supported,
            reason=None if subscription_supported else "subscription_login_required",
            next_action=None if subscription_supported else "codex login",
        )
    except (ContractError, EOFError, OSError, TimeoutError, ValueError, RecursionError, subprocess.TimeoutExpired):
        return _authentication(check=check, reason="auth_check_failed")
    finally:
        if process is not None:
            _close_probe(process, start_identity=start_identity)


def _adapter_rows(*, project: Path, home: Path | None = None) -> list[dict[str, Any]]:
    environment = _environment(home)
    rows = []
    for path in sorted((CORE_ROOT / "adapters").glob("*/adapter.json")):
        manifest = AdapterManifest.load(path)
        binary, version = _resolve_adapter(manifest, project=project, environment=environment)
        supported = binary is not None and version in manifest.verified_versions
        status = (
            "unavailable" if not binary
            else "supported" if supported
            else "unverified"
        )
        if not binary:
            authentication = _authentication(reason="not_installed")
        elif manifest.name not in {"codex", "claude"}:
            authentication = _authentication(reason="auth_check_unsupported")
        elif not supported:
            authentication = _authentication(reason="unverified_version")
        else:
            probe = _codex_auth if manifest.name == "codex" else _claude_auth
            try:
                authentication = probe(binary, project=project, environment=environment)
            except (ContractError, OSError, subprocess.TimeoutExpired):
                authentication = _authentication(reason="auth_cleanup_unconfirmed")
        ready = supported and authentication["subscription_supported"]
        rows.append({
            "adapter": manifest.name,
            "transport": manifest.transport,
            "status": status,
            "binary": binary,
            "version": version,
            "manifest": str(path),
            "installed": binary is not None,
            "supported": supported,
            "authenticated": authentication["authenticated"],
            "authentication": authentication,
            "ready": ready,
            # Authentication and MCP registration prove neither worker
            # execution nor a compatible model/permission operation receipt.
            "operation_verified": None,
            "operation_verification": {
                "status": "unknown", "reason": "no_compatible_operation_proof",
            },
        })
    return rows


def build_doctor_report(
    *,
    project: Path,
    home: Path | None = None,
    squad_executable: Path | None = None,
    manager: LocalIntegrationManager | None = None,
) -> dict[str, Any]:
    """Report provider and installed-app readiness without modifying config."""

    adapters = _adapter_rows(project=project, home=home)
    integration_manager = manager or LocalIntegrationManager(
        project=project,
        home=home,
        squad_executable=squad_executable,
    )
    local_apps = []
    for template in load_integrations():
        row = integration_manager.inspect(template)
        local_apps.append({
            **row,
            "registered": row.get("status") == "matching",
            "operation_verified": None,
        })
    installed_apps = [row for row in local_apps if row["installed"]]
    adapter_ready = any(row["ready"] for row in adapters)
    by_adapter = {row["adapter"]: row for row in adapters}
    supported_workflows = {}
    for workflow, required in (
        ("branch-review", ("codex",)), ("issue-delivery", ("claude", "codex")),
    ):
        blocked = [name for name in required if not by_adapter.get(name, {}).get("ready")]
        supported_workflows[workflow] = {
            "supported": True, "ready": not blocked,
            "required_adapters": list(required), "blocked_adapters": blocked,
        }
    supported_workflows["council"] = {
        "supported": False, "ready": False, "reason": "not_implemented",
    }
    workflow_ready = any(row["ready"] for row in supported_workflows.values())
    local_apps_required = bool(installed_apps)
    local_apps_ready = (
        all(row["ready"] for row in installed_apps)
        if local_apps_required else True
    )
    return {
        "core_version": __version__,
        "ready": workflow_ready and local_apps_ready,
        "adapter_ready": adapter_ready,
        "supported_workflows": supported_workflows,
        "local_app_access": {
            "required": local_apps_required,
            "ready": local_apps_ready,
            "installed_count": len(installed_apps),
            "configured_count": sum(row["ready"] for row in installed_apps),
            "mcp_sdk_requirement": "mcp==2.2.0",
            "mcp_sdk_available": integration_manager.mcp_sdk_available,
            "mcp_sdk_supported": integration_manager.mcp_sdk_supported,
            "mcp_sdk_version": integration_manager.mcp_sdk_version,
            "squad_executable": (
                str(integration_manager.squad_executable)
                if integration_manager.squad_executable else None
            ),
            "launcher_error": integration_manager.launcher_error,
        },
        "adapters": adapters,
        "local_apps": local_apps,
    }
