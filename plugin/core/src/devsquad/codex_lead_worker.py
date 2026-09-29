"""Drive one frozen read-only Codex headless-lead turn inside the M2 worker."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any

from .catalog import normalize_models, verified_efforts
from .codex_protocol import (
    JsonLinePeer,
    NativeTurnState,
    discover_models,
    initialize_request,
    initialized_notification,
    receive_response,
    thread_start_request,
    turn_start_request,
)
from .codex_review_worker import (
    MAX_PROTOCOL_EVENT_BYTES,
    MAX_PROTOCOL_EVENTS,
    MAX_SERVER_STDERR_BYTES,
    _isolated_codex_environment,
    _remaining,
    _response_result,
    _stop_server,
    _usage,
    _validated_adapter,
    freeze_codex_role,
)
from .contracts import CapabilityUnavailable, ContractError, ProfileUnsupported
from .store import canonical_json
from .supervisor import BoundedDrain
from .workflows import (
    MAX_EVIDENCE_BYTES,
    MAX_LEAD_BYTES,
    build_lead_prompt,
    decode_headless_lead_choice,
    lead_output_schema,
    make_headless_lead_evidence,
)


def freeze_codex_lead(selected: dict[str, Any]) -> dict[str, Any]:
    return freeze_codex_role(
        selected, role="lead", output_schema=lead_output_schema(),
    )


def run(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ContractError("workflow snapshot must be an object")
    adapter, profile = _validated_adapter(
        snapshot,
        role="lead",
        adapter_key="lead_adapter",
        output_schema=lead_output_schema(),
    )
    handoff = snapshot.get("headless_handoff")
    if not isinstance(handoff, dict) or not isinstance(handoff.get("packet"), dict):
        raise ContractError("headless lead handoff is missing")
    binary = Path(adapter["binary"])
    try:
        resolved = binary.resolve(strict=True)
    except OSError as exc:
        raise CapabilityUnavailable("frozen Codex executable is missing") from exc
    if (resolved != binary
            or hashlib.sha256(binary.read_bytes()).hexdigest() != adapter["binary_sha256"]):
        raise CapabilityUnavailable("frozen Codex executable changed after preflight")
    try:
        version = subprocess.run(
            [str(binary), "--version"], text=True, capture_output=True,
            timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CapabilityUnavailable(
            "frozen Codex version could not be re-observed"
        ) from exc
    if version.returncode != 0 or version.stdout.strip() != adapter["harness_version"]:
        raise CapabilityUnavailable("Codex version changed after preflight")

    effort = profile["effort"]["value"]
    model = profile["model_id"]
    review_root = Path(snapshot["workspace"]["path"]).resolve(strict=True)
    argv = [
        str(binary),
        "-c", f'model="{model}"',
        "-c", f'model_reasoning_effort="{effort}"',
        "--disable", "apps",
        "--disable", "plugins",
        "--disable", "browser_use",
        "--disable", "computer_use",
        "--disable", "multi_agent",
        "--enable", "skip_host_skill_discovery",
        "app-server", "--listen", "stdio://",
    ]
    process: subprocess.Popen[bytes] | None = None
    writer: io.TextIOWrapper | None = None
    stderr: BoundedDrain | None = None
    codex_home: tempfile.TemporaryDirectory | None = None
    protocol_events: list[dict[str, Any]] = []
    protocol_bytes = 0
    deadline = time.monotonic() + snapshot["task"]["budget"]["wall_seconds"]

    def record(message: dict[str, Any]) -> None:
        nonlocal protocol_bytes
        encoded = canonical_json(message).encode()
        protocol_bytes += len(encoded)
        if (len(protocol_events) >= MAX_PROTOCOL_EVENTS
                or protocol_bytes > MAX_PROTOCOL_EVENT_BYTES):
            raise ContractError("native Codex protocol evidence exceeds its bound")
        protocol_events.append(message)

    try:
        codex_home, environment = _isolated_codex_environment(adapter["auth_file"])
        process = subprocess.Popen(
            argv,
            cwd=review_root,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=False,
            close_fds=True,
        )
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        writer = io.TextIOWrapper(process.stdin, encoding="utf-8", write_through=True)
        peer = JsonLinePeer(process.stdout, writer)
        stderr = BoundedDrain(process.stderr, MAX_SERVER_STDERR_BYTES)
        stderr.start()

        peer.send(initialize_request(1))
        _response_result(receive_response(
            peer, 1, timeout_seconds=_remaining(deadline), on_notification=record,
        ), "Codex initialize")
        peer.send(initialized_notification())
        models = discover_models(
            peer, first_request_id=10, timeout_seconds=_remaining(deadline),
        )
        catalog = {
            "complete": True,
            "harness": "codex",
            "harness_version": adapter["harness_version"],
            "models": normalize_models("codex", adapter["harness_version"], models),
        }
        if effort not in verified_efforts(
            catalog,
            harness="codex",
            version=adapter["harness_version"],
            model_id=model,
        ):
            raise ProfileUnsupported(
                f"unsupported or unverified effort {effort!r} for codex model {model!r}"
            )

        peer.send(thread_start_request(
            100, cwd=str(review_root), model=model, permission="read_only",
            ephemeral=True,
        ))
        thread_result = _response_result(receive_response(
            peer, 100, timeout_seconds=_remaining(deadline), on_notification=record,
        ), "Codex thread/start")
        thread = thread_result.get("thread")
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        reported_version = thread.get("cliVersion") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise ContractError("Codex thread/start returned no thread id")
        if reported_version and f"codex-cli {reported_version}" != adapter["harness_version"]:
            raise ContractError("Codex thread reported a different harness version")
        reported_cwd = thread_result.get("cwd")
        expected_policy = {"type": "readOnly", "networkAccess": False}
        if (not isinstance(reported_cwd, str) or not reported_cwd
                or thread_result.get("model") != model
                or thread_result.get("reasoningEffort") != effort
                or thread_result.get("modelProvider") != adapter["model_provider"]
                or thread_result.get("approvalPolicy") != "never"
                or thread_result.get("sandbox") != expected_policy
                or Path(reported_cwd).resolve() != review_root):
            raise ContractError("Codex thread did not preserve the frozen execution identity")

        prompt = build_lead_prompt(snapshot["task"], handoff["packet"])
        peer.send(turn_start_request(
            101,
            thread_id=thread_id,
            prompt=prompt,
            model=model,
            effort=effort,
            cwd=str(review_root),
            permission="read_only",
            output_schema=lead_output_schema(),
        ))
        turn_result = _response_result(receive_response(
            peer, 101, timeout_seconds=_remaining(deadline), on_notification=record,
        ), "Codex turn/start")
        turn = turn_result.get("turn")
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise ContractError("Codex turn/start returned no turn id")
        state = NativeTurnState(thread_id=thread_id, turn_id=turn_id)
        for event in protocol_events:
            state.consume(event)
        output_bytes = sum(len(part.encode()) for part in state.output)
        while not state.terminal:
            message = peer.receive(_remaining(deadline))
            if "id" in message and "method" in message:
                raise ContractError("native Codex requested an unsupported host action")
            record(message)
            prior = len(state.output)
            state.consume(message)
            output_bytes += sum(len(part.encode()) for part in state.output[prior:])
            if output_bytes > MAX_LEAD_BYTES:
                raise ContractError("native Codex lead output exceeds its byte limit")
        if state.terminal_status != "completed":
            detail = (
                canonical_json(state.error)[:2000]
                if state.error is not None else "no provider error was reported"
            )
            raise ContractError(
                "native Codex lead did not complete successfully: "
                f"{state.terminal_status}; {detail}"
            )
        lead_payload = "".join(state.output).strip()
        if not lead_payload:
            raise ContractError(
                "native Codex lead completed without output; protocol_summary="
                + canonical_json(state.output_diagnostics())
            )
        choice = decode_headless_lead_choice(lead_payload, handoff["packet"])
        usage = _usage(protocol_events, thread_id, turn_id)
    finally:
        if process is not None:
            _stop_server(process, writer, stderr)
        if codex_home is not None:
            codex_home.cleanup()

    observed_identity = {
        "harness": "codex",
        "harness_version": adapter["harness_version"],
        "model_provider": adapter["model_provider"],
        "model_id": model,
        "effort": effort,
        "permission_policy": "read_only",
        "verification": "verified",
    }
    return make_headless_lead_evidence(
        snapshot,
        handoff,
        choice,
        observed_identity=observed_identity,
        native_ids={"thread_id": thread_id, "turn_id": turn_id},
        native_model_requests=None,
        usage=usage,
    )


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_EVIDENCE_BYTES + 1)
    if len(payload) > MAX_EVIDENCE_BYTES:
        raise ContractError("workflow snapshot exceeds its byte limit")
    try:
        snapshot = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("workflow snapshot is not valid UTF-8 JSON") from exc
    sys.stdout.write(canonical_json(run(snapshot)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
