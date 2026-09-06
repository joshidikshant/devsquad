"""Strict M1 contracts for prepared invocations and normalized results."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = 1
Transport = Literal["cli_exec", "native_protocol"]
Verification = Literal["verified", "unverified", "unavailable", "unknown"]


class ContractError(ValueError):
    """Raised before launch when an invocation contract is unsupported."""

    code = "INPUT_INVALID"


class ProfileUnsupported(ContractError):
    code = "PROFILE_UNSUPPORTED"


@dataclass(frozen=True)
class ExecutionIdentity:
    harness: str
    harness_version: str | None
    model_provider: str | None
    model_family: str | None
    model: str | None
    effort: str | None
    tools: tuple[str, ...] = ()
    permissions: str = "read_only"
    account_pool: str | None = None
    verification: Verification = "unknown"

    def __post_init__(self) -> None:
        if not isinstance(self.harness, str) or not self.harness:
            raise ContractError("identity harness must be non-empty")
        if self.permissions not in {"read_only", "workspace_write"}:
            raise ContractError("identity permission is invalid")
        if self.verification not in {"verified", "unverified", "unavailable", "unknown"}:
            raise ContractError("identity verification is invalid")
        if not isinstance(self.tools, tuple) or not all(isinstance(v, str) for v in self.tools):
            raise ContractError("identity tools must be a string tuple")


@dataclass(frozen=True)
class LaunchSpec:
    """A launch description. M2 owns spawning, timeout, cancellation and reaping."""

    schema_version: int
    adapter: str
    transport: Transport
    argv: tuple[str, ...]
    cwd: str
    stdin_path: str | None
    timeout_seconds: int
    requested: ExecutionIdentity
    environment: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ContractError("unsupported schema_version")
        if self.transport not in ("cli_exec", "native_protocol"):
            raise ContractError("unsupported transport")
        if not self.argv or not all(isinstance(v, str) and v for v in self.argv):
            raise ContractError("argv must be a non-empty string array")
        if self.timeout_seconds <= 0:
            raise ContractError("timeout_seconds must be positive")
        if not Path(self.cwd).is_absolute():
            raise ContractError("cwd must be absolute")
        allowed_env = {"DEVSQUAD_WORKER", "DEVSQUAD_RUN_ID", "DEVSQUAD_ATTEMPT_ID", "DEVSQUAD_DELEGATION_DEPTH"}
        if not isinstance(self.environment, dict) or set(self.environment) - allowed_env or not all(isinstance(k, str) and isinstance(v, str) for k, v in self.environment.items()):
            raise ContractError("environment contains non-allowlisted or non-string values")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NormalizedResult:
    """Execution evidence without conflating artifacts or acceptance."""

    schema_version: int
    execution_status: Literal[
        "succeeded", "failed", "timed_out", "interrupted", "denied", "malformed"
    ]
    error_code: str | None
    output: str | None
    artifact_status: Literal["present", "missing", "not_required", "unknown"]
    acceptance_status: Literal["pending", "accepted", "rejected", "not_evaluated"]
    requested: ExecutionIdentity
    observed: ExecutionIdentity | None
    native_ids: dict[str, str] = field(default_factory=dict)
    events: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def envelope(*, data: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    if (data is None) == (error is None):
        raise ContractError("exactly one of data and error is required")
    return {"schema_version": SCHEMA_VERSION, "ok": error is None, "data": data, "error": error}


def error_payload(code: str, message: str, *, retryable: bool = False, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "retryable": retryable, "details": details or {}}


def validate_launch_payload(value: dict[str, Any]) -> None:
    expected = {"schema_version", "adapter", "transport", "argv", "cwd", "stdin_path", "timeout_seconds", "requested", "environment"}
    if set(value) != expected:
        raise ContractError(f"LaunchSpec fields differ: {sorted(set(value) ^ expected)}")
    LaunchSpec(**{**value, "argv": tuple(value["argv"]), "requested": ExecutionIdentity(**{**value["requested"], "tools": tuple(value["requested"]["tools"])})})
