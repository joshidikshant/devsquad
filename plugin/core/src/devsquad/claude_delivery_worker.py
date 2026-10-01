"""Run one frozen Claude CLI implementation inside the durable writer fence."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from .contracts import CapabilityUnavailable, ContractError, ProfileUnsupported
from .claude_identity import (
    ClaudeResultError, failure_diagnostics, failure_envelope, native_result,
    observed_identity, strict_json,
)
from .store import canonical_json
from .workflows import build_implementation_prompt, make_implementation_evidence


MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_CLAUDE_OUTPUT_BYTES = 2 * 1024 * 1024
ADAPTER_FIELDS = {
    "schema_version", "harness", "transport", "binary", "binary_sha256",
    "harness_version", "model_provider", "permission_args", "error_patterns",
    "denied_pattern",
}


def _manifest_path() -> Path:
    source = Path(__file__).resolve().parents[2] / "adapters" / "claude" / "adapter.json"
    if source.is_file():
        return source
    installed = (
        Path(sys.prefix) / "share" / "devsquad" / "adapters" / "claude"
        / "adapter.json"
    )
    if installed.is_file():
        return installed
    raise CapabilityUnavailable("Claude adapter manifest is unavailable")


def freeze_claude_implementer(selected: dict[str, Any]) -> dict[str, Any]:
    """Resolve one exact subscription Claude writer during run preflight."""
    from .adapters import (
        AdapterManifest,
        DENIED_PATTERN,
        ERROR_PATTERNS,
        harness_version,
    )
    if not isinstance(selected, dict) or not isinstance(selected.get("profile"), dict):
        raise ContractError("frozen implementer selection is invalid")
    profile = selected["profile"]
    if profile.get("harness") != "claude":
        raise CapabilityUnavailable(
            f"selected implementer harness is not Claude: {profile.get('harness')}"
        )
    if profile.get("permission_policy") != "workspace_write":
        raise ProfileUnsupported("Claude implementer requires workspace_write")
    if set(profile.get("required_tools", [])) - {"read", "write"}:
        raise ProfileUnsupported("Claude implementer requests unsupported tools")
    effort = profile.get("effort")
    if (not isinstance(effort, dict) or effort.get("transport") != "native"
            or not isinstance(effort.get("value"), str) or not effort["value"]):
        raise ProfileUnsupported("Claude implementer requires an explicit effort")
    if not isinstance(profile.get("model_id"), str) or not profile["model_id"]:
        raise ProfileUnsupported("Claude implementer requires an exact model id")

    manifest = AdapterManifest.load(_manifest_path())
    binary_name = manifest.resolve_binary()
    if not binary_name:
        raise CapabilityUnavailable("Claude executable is unavailable")
    binary = Path(binary_name).resolve(strict=True)
    version = harness_version(str(binary))
    if not version:
        raise CapabilityUnavailable("Claude version could not be observed")
    if version not in manifest.verified_versions:
        raise ProfileUnsupported(f"unverified Claude CLI version: {version}")
    return {
        "schema_version": 1,
        "harness": "claude",
        "transport": "cli_exec",
        "binary": str(binary),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "harness_version": version,
        "model_provider": manifest.model_provider or "anthropic",
        "permission_args": list(manifest.permission_profiles["workspace_write"]),
        "error_patterns": {
            code: pattern.pattern for code, pattern in ERROR_PATTERNS
        },
        "denied_pattern": DENIED_PATTERN.pattern,
    }


def _validated(snapshot: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    adapter = snapshot.get("implementation_adapter")
    try:
        profile = snapshot["routing"]["roles"]["implementer"]["selected"]["profile"]
    except (KeyError, TypeError) as exc:
        raise ContractError("frozen Claude implementer selection is missing") from exc
    if not isinstance(adapter, dict) or set(adapter) != ADAPTER_FIELDS:
        raise ContractError("frozen Claude implementer adapter fields are invalid")
    if (adapter["schema_version"] != 1
            or adapter["harness"] != "claude"
            or adapter["transport"] != "cli_exec"
            or adapter["model_provider"] != "anthropic"):
        raise ContractError("frozen Claude implementer adapter identity is invalid")
    if (not isinstance(adapter["permission_args"], list)
            or adapter["permission_args"] != [
                "--permission-mode", "acceptEdits", "--tools",
                "Read,Glob,Grep,Edit,Write",
            ]
            or not isinstance(adapter["error_patterns"], dict)
            or set(adapter["error_patterns"]) != {"AUTH_ERROR", "RATE_LIMITED"}
            or not all(
                isinstance(value, str) and value
                for value in adapter["error_patterns"].values()
            )
            or not isinstance(adapter["denied_pattern"], str)
            or not adapter["denied_pattern"]):
        raise ContractError("frozen Claude implementer policy is invalid")
    if (not isinstance(profile, dict) or profile.get("harness") != "claude"
            or profile.get("permission_policy") != "workspace_write"):
        raise ContractError("frozen profile is not a Claude implementation writer")
    return adapter, profile


def run(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ContractError("delivery snapshot must be an object")
    adapter, profile = _validated(snapshot)
    binary = Path(adapter["binary"])
    try:
        resolved = binary.resolve(strict=True)
    except OSError as exc:
        raise CapabilityUnavailable("frozen Claude executable is missing") from exc
    if (resolved != binary
            or hashlib.sha256(binary.read_bytes()).hexdigest()
            != adapter["binary_sha256"]):
        raise CapabilityUnavailable("frozen Claude executable changed after preflight")
    try:
        version = subprocess.run(
            [str(binary), "--version"], text=True, capture_output=True,
            timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise CapabilityUnavailable("Claude version could not be re-observed") from exc
    if version.returncode != 0 or version.stdout.strip() != adapter["harness_version"]:
        raise CapabilityUnavailable("Claude version changed after preflight")

    workspace = Path(snapshot["delivery_workspace"]["path"]).resolve(strict=True)
    effort = profile["effort"]["value"]
    model = profile["model_id"]
    prompt = build_implementation_prompt(
        snapshot["task"], snapshot["delivery_workspace"],
        snapshot.get("revision_request"),
    )
    argv = [
        str(binary), "--print", "--output-format", "json", "--safe-mode",
        "--disable-slash-commands", "--no-session-persistence",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--no-chrome", "--model", model, "--effort", effort,
        *adapter["permission_args"], prompt,
    ]
    timeout_seconds = snapshot["task"]["budget"]["wall_seconds"]
    try:
        completed = subprocess.run(
            argv,
            cwd=workspace,
            env={**os.environ, "DEVSQUAD_WORKER": "1"},
            text=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        completed = subprocess.CompletedProcess(
            argv, 124, exc.stdout or "", exc.stderr or "",
        )
        timed_out = True
    # Preserve exact native bytes, including invalid UTF-8 and CRLF. Only
    # stderr classification uses replacement decoding; stdout is parsed strictly.
    stdout = completed.stdout
    stderr = completed.stderr.decode("utf-8", "replace") if isinstance(
        completed.stderr, bytes
    ) else completed.stderr
    if (len(stdout.encode() if isinstance(stdout, str) else stdout) > MAX_CLAUDE_OUTPUT_BYTES
            or len(stderr.encode()) > MAX_CLAUDE_OUTPUT_BYTES):
        raise ClaudeResultError("CLI_ERROR", failure_diagnostics(
            stdout, None, "output_limit",
        ))
    import re
    error_code = next((
        code for code, pattern in adapter["error_patterns"].items()
        if re.search(pattern, stderr, re.IGNORECASE)
    ), None)
    if timed_out:
        error_code = "TIMEOUT"
    elif completed.returncode != 0 and error_code is None:
        error_code = "CLI_ERROR"
    provider_document = None
    try:
        provider_document = strict_json(stdout)
        summary, native = native_result(provider_document)
        observed = observed_identity(native, adapter, profile)
    except ContractError as exc:
        safe_document = provider_document if isinstance(provider_document, dict) else {}
        provider_text = str(
            safe_document.get("result") or safe_document.get("error") or ""
        )
        error_code = error_code or next((
            code for code, pattern in adapter["error_patterns"].items()
            if re.search(pattern, provider_text, re.IGNORECASE)
        ), None)
        if error_code is None and re.search(
            adapter["denied_pattern"], provider_text, re.IGNORECASE,
        ):
            error_code = "CLI_ERROR"
        raise ClaudeResultError(error_code or "CLI_ERROR", failure_diagnostics(
            stdout, provider_document, "native_result_invalid",
        )) from exc
    if error_code is not None:
        raise ClaudeResultError(error_code, failure_diagnostics(
            stdout, provider_document, "execution_failed",
        ))
    return make_implementation_evidence(
        snapshot,
        summary,
        observed_identity=observed,
        native_ids={"session_id": native["session_id"]},
        native_model_requests=None,
        usage=native["usage"],
    )


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_SNAPSHOT_BYTES + 1)
    if len(payload) > MAX_SNAPSHOT_BYTES:
        raise ContractError("delivery snapshot exceeds its byte limit")
    try:
        snapshot = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("delivery snapshot is not valid UTF-8 JSON") from exc
    try:
        result = run(snapshot)
    except ClaudeResultError as exc:
        selected = snapshot["routing"]["roles"]["implementer"]["selected"]
        sys.stdout.write(canonical_json(failure_envelope(
            exc, selected["profile_sha256"],
        )) + "\n")
        sys.stderr.write(str(exc) + "\n")
        return 1
    sys.stdout.write(canonical_json(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
