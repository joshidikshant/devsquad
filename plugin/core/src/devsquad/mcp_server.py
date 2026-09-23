"""Optional local MCP transport for the durable DevSquad service.

This module must remain importable without the MCP SDK.  Ordinary CLI and
worker processes never pay for or depend on the optional transport package.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from . import __version__
from .contracts import ContractError, PolicyDenied, envelope, error_payload
from .diagnostics import build_doctor_report
from .service import Service
from .store import ConflictError

MCP_SDK_REQUIREMENT = "mcp==2.2.0"
MAX_ARTIFACT_PREVIEW_BYTES = 16 * 1024


class MCPDependencyUnavailable(RuntimeError):
    """Raised when the explicitly requested MCP transport is not installed."""


def _server_type() -> Any:
    try:
        from mcp.server import MCPServer
    except ModuleNotFoundError as exc:
        if exc.name != "mcp":
            raise
        raise MCPDependencyUnavailable(
            "DevSquad MCP support is not installed; install the optional "
            f"dependency with: python3 -m pip install 'devsquad-core[mcp]' "
            f"(requires {MCP_SDK_REQUIREMENT})"
        ) from exc
    return MCPServer


class MCPBridge:
    """Strict MCP-facing application functions over one saved runtime."""

    def __init__(
        self,
        runtime: Path,
        service: Service | None = None,
        *,
        caller_surface: str | None = None,
        caller_session_ref: str | None = None,
        environment: Mapping[str, str] | None = None,
        project: Path | None = None,
    ):
        for label, value in (
            ("caller_surface", caller_surface),
            ("caller_session_ref", caller_session_ref),
        ):
            if value is not None and (not isinstance(value, str) or not value):
                raise ContractError(f"{label} must be a non-empty string or null")
        self.runtime = runtime.resolve()
        self.service = service or Service(runtime)
        self.caller_surface = caller_surface
        self.caller_session_ref = caller_session_ref
        self.environment = os.environ if environment is None else environment
        self.project = (project or Path.cwd()).resolve()

    @staticmethod
    def _response(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return envelope(data=operation())
        except ContractError as exc:
            return envelope(error=error_payload(exc.code, str(exc)))
        except Exception as exc:
            return envelope(error=error_payload("INTERNAL_ERROR", str(exc)))

    def _mutation_response(
        self, operation: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        def guarded() -> dict[str, Any]:
            worker = self.environment.get("DEVSQUAD_WORKER", "0")
            depth = self.environment.get("DEVSQUAD_DELEGATION_DEPTH", "0")
            if worker not in {"", "0"} or depth not in {"", "0"}:
                raise PolicyDenied(
                    "DevSquad worker sessions cannot start or mutate team workflows"
                )
            return operation()

        return self._response(guarded)

    def _task_with_bound_origin(self, task: dict[str, Any]) -> dict[str, Any]:
        if self.caller_surface is None and self.caller_session_ref is None:
            return task
        bound = copy.deepcopy(task)
        if not isinstance(bound, dict):
            raise ContractError("task must be an object")
        saved_origin = bound.get("origin", {})
        if not isinstance(saved_origin, dict):
            raise ContractError("task origin must be an object")
        origin = dict(saved_origin)
        if self.caller_surface is not None:
            origin["surface"] = self.caller_surface
        if self.caller_session_ref is not None:
            origin["session_ref"] = self.caller_session_ref
        bound["origin"] = origin
        return bound

    def start(
        self,
        task: dict[str, Any],
        idempotency_key: str,
        supersedes_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Validate and save a run, returning without observing its worker."""

        return self._mutation_response(
            lambda: self.service.start(
                self._task_with_bound_origin(task),
                idempotency_key,
                supersedes_run_id,
            )
        )

    def doctor(self) -> dict[str, Any]:
        """Inspect local provider and application readiness without mutation."""

        return self._response(lambda: build_doctor_report(project=self.project))

    def status(self, run_id: str) -> dict[str, Any]:
        """Inspect the current projection for a saved run."""

        return self._response(lambda: self.service.status(run_id))

    def events(
        self, run_id: str, after: int = 0, limit: int = 100,
    ) -> dict[str, Any]:
        """Read one bounded event page using its durable integer cursor."""

        return self._response(lambda: self.service.events(run_id, after, limit))

    def result(
        self, run_id: str, preview_bytes: int = 4096,
    ) -> dict[str, Any]:
        """Return receipt references and bounded UTF-8 artifact previews."""

        def operation() -> dict[str, Any]:
            if (type(preview_bytes) is not int
                    or not 0 <= preview_bytes <= MAX_ARTIFACT_PREVIEW_BYTES):
                raise ContractError(
                    "preview_bytes must be between 0 and "
                    f"{MAX_ARTIFACT_PREVIEW_BYTES}"
                )
            result = self.service.result(run_id)
            if not result.get("ready") or preview_bytes == 0:
                return result
            remaining = preview_bytes
            artifact_root = (self.runtime / "artifacts").resolve(strict=True)
            artifacts = []
            for saved in result.get("artifacts", []):
                artifact = dict(saved)
                artifact["preview_text"] = None
                artifact["preview_truncated"] = artifact.get("byte_size", 0) > 0
                if remaining > 0:
                    path = Path(artifact["path"]).resolve(strict=True)
                    try:
                        path.relative_to(artifact_root)
                    except ValueError as exc:
                        raise ConflictError(
                            "result artifact path escapes the saved runtime"
                        ) from exc
                    with path.open("rb") as stream:
                        raw = stream.read(remaining + 1)
                    consumed = min(len(raw), remaining)
                    try:
                        artifact["preview_text"] = raw[:consumed].decode("utf-8")
                    except UnicodeDecodeError:
                        artifact["preview_text"] = None
                    artifact["preview_truncated"] = len(raw) > consumed
                    remaining -= consumed
                artifacts.append(artifact)
            return {**result, "artifacts": artifacts, "preview_bytes": preview_bytes}

        return self._response(operation)

    def cancel(self, run_id: str) -> dict[str, Any]:
        """Persist cancellation intent without observing worker completion."""

        return self._mutation_response(lambda: self.service.cancel(run_id))

    def resume(
        self, run_id: str, recovery: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Reconcile and safely resume a saved run."""

        return self._mutation_response(lambda: self.service.resume(run_id, recovery))

    def handoff_claim(
        self,
        run_id: str,
        expected_version: int,
        owner: str,
        prior_claim: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Claim or renew one fenced host handoff."""

        return self._mutation_response(
            lambda: self.service.handoff_claim(
                run_id, expected_version, owner, prior_claim,
            )
        )

    def handoff_complete(
        self,
        run_id: str,
        claim: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        """Submit a decision against a current fenced host claim."""

        return self._mutation_response(
            lambda: self.service.handoff_complete(run_id, claim, decision)
        )


def build_server(
    runtime: Path,
    service: Service | None = None,
    *,
    caller_surface: str | None = None,
    caller_session_ref: str | None = None,
    environment: Mapping[str, str] | None = None,
    project: Path | None = None,
) -> Any:
    """Build the local stdio server without starting it."""

    server_type = _server_type()
    from mcp.types import ToolAnnotations

    read_only = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    durable_write = ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    state_change = ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
    destructive_change = ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    )
    bridge = MCPBridge(
        runtime,
        service,
        caller_surface=caller_surface,
        caller_session_ref=caller_session_ref,
        environment=environment,
        project=project,
    )
    server = server_type(
        "DevSquad",
        version=__version__,
        instructions=(
            "Operate on durable local DevSquad runs. Submit a task once with "
            "an idempotency key, then inspect status/events/result by run ID."
        ),
    )

    @server.tool(name="squad_doctor", annotations=read_only)
    def squad_doctor() -> dict[str, Any]:
        """Inspect versions, capabilities and local host registration drift."""

        return bridge.doctor()

    @server.tool(name="squad_start", annotations=durable_write)
    def squad_start(
        task: dict[str, Any],
        idempotency_key: str,
        supersedes_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Validate, snapshot and persist one durable DevSquad run."""

        return bridge.start(task, idempotency_key, supersedes_run_id)

    @server.tool(name="squad_status", annotations=read_only)
    def squad_status(run_id: str) -> dict[str, Any]:
        """Inspect state, version, active work and the next action."""

        return bridge.status(run_id)

    @server.tool(name="squad_events", annotations=read_only)
    def squad_events(
        run_id: str, after: int = 0, limit: int = 100,
    ) -> dict[str, Any]:
        """Read a bounded durable event page after an integer cursor."""

        return bridge.events(run_id, after, limit)

    @server.tool(name="squad_result", annotations=read_only)
    def squad_result(
        run_id: str, preview_bytes: int = 4096,
    ) -> dict[str, Any]:
        """Read result references and capped local artifact previews."""

        return bridge.result(run_id, preview_bytes)

    @server.tool(name="squad_cancel", annotations=destructive_change)
    def squad_cancel(run_id: str) -> dict[str, Any]:
        """Persist cancellation intent for a saved run."""

        return bridge.cancel(run_id)

    @server.tool(name="squad_resume", annotations=state_change)
    def squad_resume(
        run_id: str, recovery: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Reconcile ownership and safely resume a saved run."""

        return bridge.resume(run_id, recovery)

    @server.tool(name="squad_handoff_claim", annotations=state_change)
    def squad_handoff_claim(
        run_id: str,
        expected_version: int,
        owner: str,
        prior_claim: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Obtain or renew a fenced host claim and its input packet."""

        return bridge.handoff_claim(run_id, expected_version, owner, prior_claim)

    @server.tool(name="squad_handoff_complete", annotations=destructive_change)
    def squad_handoff_complete(
        run_id: str,
        claim: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        """Submit a host disposition against a current fenced claim."""

        return bridge.handoff_complete(run_id, claim, decision)

    return server


def serve_stdio(
    runtime: Path,
    *,
    caller_surface: str | None = None,
    caller_session_ref: str | None = None,
) -> None:
    """Run the local MCP server; stdout is owned exclusively by the SDK."""

    build_server(
        runtime,
        caller_surface=caller_surface,
        caller_session_ref=caller_session_ref,
    ).run()
