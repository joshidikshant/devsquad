"""Manifest-driven M1 adapter preparation and output classification."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import ContractError, ExecutionIdentity, LaunchSpec, NormalizedResult, ProfileUnsupported, SCHEMA_VERSION
from .catalog import verified_efforts

ERROR_PATTERNS = (
    ("AUTH_ERROR", re.compile(r"auth|unauthorized|ineligible|\b401\b|\b403\b", re.I)),
    ("RATE_LIMITED", re.compile(r"rate.?limit|quota|resource.?exhausted|too many requests|\b429\b", re.I)),
)


@dataclass(frozen=True)
class AdapterManifest:
    name: str
    transport: str
    binary_candidates: tuple[str, ...]
    model_provider: str | None
    efforts_by_model: dict[str, tuple[str, ...]]
    permission_profiles: dict[str, tuple[str, ...]]
    output_format: str
    verified_versions: tuple[str, ...]

    @classmethod
    def load(cls, path: Path) -> "AdapterManifest":
        raw = json.loads(path.read_text())
        required = {"schema_version", "name", "transport", "binary_candidates", "capabilities"}
        missing = required - raw.keys()
        if missing or raw["schema_version"] != 1:
            raise ContractError(f"invalid adapter manifest: missing={sorted(missing)}")
        capabilities = raw["capabilities"]
        return cls(
            name=raw["name"], transport=raw["transport"],
            binary_candidates=tuple(raw["binary_candidates"]),
            model_provider=raw.get("model_provider"),
            efforts_by_model={k: tuple(v) for k, v in capabilities.get("efforts_by_model", {}).items()},
            permission_profiles={k: tuple(v) for k, v in raw.get("permission_profiles", {}).items()},
            output_format=raw.get("output_format", "text"),
            verified_versions=tuple(raw.get("verified_harness_versions", [])),
        )

    def resolve_binary(self) -> str | None:
        return next((p for name in self.binary_candidates if (p := shutil.which(name))), None)

    def with_model_efforts(self, mapping: dict[str, tuple[str, ...]]) -> "AdapterManifest":
        return AdapterManifest(self.name, self.transport, self.binary_candidates, self.model_provider, mapping, self.permission_profiles, self.output_format, self.verified_versions)


def _permission_args(manifest: AdapterManifest, permission: str) -> tuple[str, ...]:
    try:
        return manifest.permission_profiles[permission]
    except KeyError as exc:
        raise ContractError(f"unsupported permission profile: {permission}") from exc


def prepare_cli(
    manifest: AdapterManifest, *, prompt: str, cwd: str, model: str | None,
    effort: str | None, permission: str, timeout_seconds: int, stdin_path: str | None = None,
) -> LaunchSpec:
    binary = manifest.resolve_binary()
    if not binary:
        raise ContractError(f"adapter unavailable: {manifest.name}")
    if effort is not None:
        supported = manifest.efforts_by_model.get(model or "")
        if supported is None or effort not in supported:
            raise ProfileUnsupported(f"unsupported or unverified effort {effort!r} for {manifest.name} model {model!r}")
    args: list[str]
    if manifest.name == "codex":
        sandbox = "read-only" if permission == "read_only" else "workspace-write"
        args = [binary, "exec", "--json", "--cd", cwd, "--sandbox", sandbox]
        if model:
            args += ["--model", model]
        if effort:
            args += ["-c", f'model_reasoning_effort="{effort}"']
        args += [prompt]
    elif manifest.name == "antigravity":
        args = [binary, "--print", prompt, "--output-format", "json", "--mode", "plan" if permission == "read_only" else "accept-edits"]
        if model:
            args += ["--model", model]
        if effort:
            args += ["--effort", effort]
    elif manifest.name == "grok":
        args = [binary, "--single", prompt, "--output-format", "json", "--cwd", cwd, "--permission-mode", "plan" if permission == "read_only" else "acceptEdits", "--no-subagents"]
        if model:
            args += ["--model", model]
        if effort:
            args += ["--reasoning-effort", effort]
    else:
        raise ContractError(f"no argv builder for adapter: {manifest.name}")
    args.extend(_permission_args(manifest, permission))
    requested = ExecutionIdentity(
        harness=manifest.name, harness_version=None, model_provider=manifest.model_provider,
        model_family=None, model=model, effort=effort, permissions=permission,
        verification="unverified",
    )
    return LaunchSpec(SCHEMA_VERSION, manifest.name, "cli_exec", tuple(args), str(Path(cwd).resolve()), stdin_path, timeout_seconds, requested, {"DEVSQUAD_WORKER": "1"})


def prepare_native_codex(manifest: AdapterManifest, *, cwd: str, model: str, effort: str, permission: str, timeout_seconds: int, harness_version_value: str) -> LaunchSpec:
    """Prepare the supervisor-owned app-server child without spawning it."""
    if manifest.name != "codex" or manifest.transport != "native_protocol":
        raise ContractError("native Codex manifest required")
    binary = manifest.resolve_binary()
    if not binary:
        raise ContractError("adapter unavailable: codex")
    if harness_version_value not in manifest.verified_versions:
        raise ProfileUnsupported(f"unverified Codex app-server version: {harness_version_value}")
    supported = manifest.efforts_by_model.get(model)
    if supported is None or effort not in supported:
        raise ProfileUnsupported(f"unsupported or unverified effort {effort!r} for codex model {model!r}")
    _permission_args(manifest, permission)
    requested = ExecutionIdentity("codex", harness_version_value, "openai", None, model, effort, (), permission, None, "verified")
    return LaunchSpec(SCHEMA_VERSION, "codex", "native_protocol", (binary, "app-server", "--listen", "stdio://"), str(Path(cwd).resolve()), None, timeout_seconds, requested, {"DEVSQUAD_WORKER": "1"})


def prepare_native_codex_from_catalog(manifest: AdapterManifest, snapshot: dict[str, Any], *, cwd: str, model: str, effort: str, permission: str, timeout_seconds: int, harness_version_value: str) -> LaunchSpec:
    efforts = verified_efforts(snapshot, harness="codex", version=harness_version_value, model_id=model)
    return prepare_native_codex(manifest.with_model_efforts({model: efforts}), cwd=cwd, model=model, effort=effort, permission=permission, timeout_seconds=timeout_seconds, harness_version_value=harness_version_value)


def _provider_records(adapter: str, stdout: str) -> tuple[list[dict[str, Any]], bool, bool, str | None]:
    records: list[dict[str, Any]] = []
    deliverable = False
    error_text = None
    terminal = False
    try:
        document = json.loads(stdout)
        source = document if isinstance(document, list) else [document]
    except json.JSONDecodeError:
        source = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    for item in source:
        if not isinstance(item, dict):
            raise json.JSONDecodeError("record is not an object", stdout, 0)
        records.append(item)
        if item.get("is_error") is True or item.get("error"):
            error_text = str(item.get("error") or item.get("result") or item.get("message"))
        kind = item.get("type")
        if kind in {"result", "turn.completed"}:
            terminal = True
        if adapter == "codex" and kind == "item.completed":
            native_item = item.get("item") or {}
            if native_item.get("type") in {"agent_message", "agentMessage"}:
                payload = native_item.get("text") or native_item.get("content")
                deliverable = isinstance(payload, str) and bool(payload.strip())
        if kind in {"result", "assistant_message", "turn.completed"} and item.get("is_error") is not True:
            payload = item.get("result") or item.get("message") or item.get("text")
            if isinstance(payload, str) and payload.strip():
                deliverable = True
    return records, deliverable, terminal, error_text


def classify_cli(spec: LaunchSpec, *, returncode: int, stdout: str, stderr: str, timed_out: bool = False) -> NormalizedResult:
    code = next((code for code, pattern in ERROR_PATTERNS if pattern.search(stderr)), None)
    status = "succeeded"
    if code == "AUTH_ERROR" or code == "RATE_LIMITED":
        status = "failed"
    elif timed_out or returncode in (124, 137, 143):
        status, code = "timed_out", "TIMEOUT"
    elif returncode != 0:
        status, code = "failed", "CLI_ERROR"
    elif not stdout.strip():
        status, code = "malformed", "CLI_ERROR"
    elif spec.adapter in {"codex", "antigravity", "grok"}:
        try:
            _, deliverable, terminal, provider_error = _provider_records(spec.adapter, stdout)
            if provider_error:
                code = next((candidate for candidate, pattern in ERROR_PATTERNS if pattern.search(provider_error)), "CLI_ERROR")
                status = "denied" if re.search(r"permission denied|tool (?:use )?denied|not allowed", provider_error, re.I) else "failed"
            elif not deliverable or not terminal:
                status, code = "malformed", "CLI_ERROR"
        except json.JSONDecodeError:
            status, code = "malformed", "CLI_ERROR"
    return NormalizedResult(SCHEMA_VERSION, status, code, stdout if stdout else None, "unknown", "not_evaluated", spec.requested, None)


def harness_version(binary: str) -> str | None:
    try:
        return subprocess.run([binary, "--version"], text=True, capture_output=True, timeout=3, check=False).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None
