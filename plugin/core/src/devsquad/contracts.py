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


class CapabilityUnavailable(ContractError):
    code = "CAPABILITY_UNAVAILABLE"


class BudgetExhausted(ContractError):
    code = "BUDGET_EXHAUSTED"


class PolicyDenied(ContractError):
    code = "POLICY_DENIED"


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
        if not isinstance(self.permissions, str) or self.permissions not in {"read_only", "workspace_write"}:
            raise ContractError("identity permission is invalid")
        if not isinstance(self.verification, str) or self.verification not in {"verified", "unverified", "unavailable", "unknown"}:
            raise ContractError("identity verification is invalid")
        if not isinstance(self.tools, tuple) or not all(isinstance(v, str) for v in self.tools):
            raise ContractError("identity tools must be a string tuple")
        if len(set(self.tools)) != len(self.tools) or any(not v for v in self.tools):
            raise ContractError("identity tools must contain unique non-empty strings")
        for name in ("harness_version", "model_provider", "model_family", "model", "effort", "account_pool"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ContractError(f"identity {name} must be a non-empty string or null")


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
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ContractError("unsupported schema_version")
        if not isinstance(self.transport, str) or self.transport not in ("cli_exec", "native_protocol"):
            raise ContractError("unsupported transport")
        if not isinstance(self.adapter, str) or not self.adapter:
            raise ContractError("adapter must be non-empty")
        if not isinstance(self.argv, tuple) or not self.argv or not all(isinstance(v, str) and v for v in self.argv):
            raise ContractError("argv must be a non-empty string array")
        if type(self.timeout_seconds) is not int or self.timeout_seconds <= 0:
            raise ContractError("timeout_seconds must be positive")
        if not isinstance(self.cwd, str) or not self.cwd or not Path(self.cwd).is_absolute():
            raise ContractError("cwd must be absolute")
        if self.stdin_path is not None and (not isinstance(self.stdin_path, str) or not self.stdin_path):
            raise ContractError("stdin_path must be a non-empty string or null")
        if not isinstance(self.requested, ExecutionIdentity):
            raise ContractError("requested must be an execution identity")
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

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ContractError("unsupported schema_version")
        if not isinstance(self.execution_status, str) or self.execution_status not in {"succeeded", "failed", "timed_out", "interrupted", "denied", "malformed"}:
            raise ContractError("invalid execution_status")
        if not isinstance(self.artifact_status, str) or self.artifact_status not in {"present", "missing", "not_required", "unknown"}:
            raise ContractError("invalid artifact_status")
        if not isinstance(self.acceptance_status, str) or self.acceptance_status not in {"pending", "accepted", "rejected", "not_evaluated"}:
            raise ContractError("invalid acceptance_status")
        for name in ("error_code", "output"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ContractError(f"{name} must be a string or null")
        if not isinstance(self.requested, ExecutionIdentity) or (self.observed is not None and not isinstance(self.observed, ExecutionIdentity)):
            raise ContractError("requested/observed identity is invalid")
        if not isinstance(self.native_ids, dict) or not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in self.native_ids.items()):
            raise ContractError("native_ids must contain non-empty string pairs")
        if not isinstance(self.events, tuple) or not all(isinstance(v, dict) for v in self.events):
            raise ContractError("events must be an object tuple")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def envelope(*, data: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    if (data is None) == (error is None):
        raise ContractError("exactly one of data and error is required")
    return {"schema_version": SCHEMA_VERSION, "ok": error is None, "data": data, "error": error}


def error_payload(code: str, message: str, *, retryable: bool = False, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "retryable": retryable, "details": details or {}}


def validate_launch_payload(value: dict[str, Any]) -> None:
    if not isinstance(value, dict):
        raise ContractError("LaunchSpec must be an object")
    expected = {"schema_version", "adapter", "transport", "argv", "cwd", "stdin_path", "timeout_seconds", "requested", "environment"}
    if set(value) != expected:
        raise ContractError(f"LaunchSpec fields differ: {sorted(set(value) ^ expected)}")
    if not isinstance(value["argv"], (list, tuple)) or isinstance(value["argv"], (str, bytes)):
        raise ContractError("argv must be an array")
    if not isinstance(value["requested"], dict) or not isinstance(value["requested"].get("tools"), (list, tuple)):
        raise ContractError("requested identity is invalid")
    LaunchSpec(**{**value, "argv": tuple(value["argv"]), "requested": ExecutionIdentity(**{**value["requested"], "tools": tuple(value["requested"]["tools"])})})
