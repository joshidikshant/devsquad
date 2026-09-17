"""Drive one frozen read-only Codex app-server review inside the M2 worker."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
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
from .contracts import CapabilityUnavailable, ContractError, ProfileUnsupported
from .review_worker import run_review_and_checks
from .store import canonical_json
from .supervisor import BoundedDrain
from .workflows import (
    MAX_REVIEW_BYTES,
    build_review_prompt,
    decode_review_document,
    review_output_schema,
)


MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_PROTOCOL_EVENT_BYTES = 4 * 1024 * 1024
MAX_PROTOCOL_EVENTS = 10_000
MAX_SERVER_STDERR_BYTES = 256 * 1024
ADAPTER_FIELDS = {
    "schema_version", "harness", "transport", "binary", "binary_sha256",
    "harness_version", "model_provider", "output_schema_sha256",
}


def _adapter_manifest_path() -> Path:
    source = Path(__file__).resolve().parents[2] / "adapters" / "codex" / "adapter.json"
    if source.is_file():
        return source
    installed = Path(sys.prefix) / "share" / "devsquad" / "adapters" / "codex" / "adapter.json"
    if installed.is_file():
        return installed
    raise CapabilityUnavailable("Codex adapter manifest is unavailable")


def freeze_codex_reviewer(selected: dict[str, Any]) -> dict[str, Any]:
    """Resolve and verify the non-model Codex launch identity during preflight."""
    from .adapters import AdapterManifest, harness_version

    if not isinstance(selected, dict) or not isinstance(selected.get("profile"), dict):
        raise ContractError("frozen reviewer selection is invalid")
    profile = selected["profile"]
    if profile.get("harness") != "codex":
        raise CapabilityUnavailable(
            f"selected reviewer harness is not implemented for M3: {profile.get('harness')}"
        )
    if profile.get("permission_policy") != "read_only":
        raise ProfileUnsupported("Codex branch review requires read_only permission")
    if set(profile.get("required_tools", [])) - {"read"}:
        raise ProfileUnsupported("Codex branch review profile requests unsupported tools")
    effort = profile.get("effort")
    if (not isinstance(effort, dict) or effort.get("transport") != "native"
            or not isinstance(effort.get("value"), str) or not effort["value"]):
        raise ProfileUnsupported("Codex branch review requires an explicit native effort")
    if not isinstance(profile.get("model_id"), str) or not profile["model_id"]:
        raise ProfileUnsupported("Codex branch review requires an exact model id")

    manifest = AdapterManifest.load(_adapter_manifest_path())
    if manifest.name != "codex" or manifest.transport != "native_protocol":
        raise CapabilityUnavailable("installed Codex adapter is not native_protocol")
    binary_name = manifest.resolve_binary()
    if not binary_name:
        raise CapabilityUnavailable("Codex executable is unavailable")
    binary = Path(binary_name).resolve(strict=True)
    version = harness_version(str(binary))
    if not version:
        raise CapabilityUnavailable("Codex version could not be observed")
    if version not in manifest.verified_versions:
        raise ProfileUnsupported(f"unverified Codex app-server version: {version}")
    content = binary.read_bytes()
    schema_hash = hashlib.sha256(canonical_json(review_output_schema()).encode()).hexdigest()
    return {
        "schema_version": 1,
        "harness": "codex",
        "transport": "native_protocol",
        "binary": str(binary),
        "binary_sha256": hashlib.sha256(content).hexdigest(),
        "harness_version": version,
        "model_provider": manifest.model_provider or "openai",
        "output_schema_sha256": schema_hash,
    }


def _validated_adapter(snapshot: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    adapter = snapshot.get("review_adapter")
    try:
        selected = snapshot["routing"]["roles"]["reviewer"]["selected"]
        profile = selected["profile"]
    except (KeyError, TypeError) as exc:
        raise ContractError("frozen Codex reviewer selection is missing") from exc
    if not isinstance(adapter, dict) or set(adapter) != ADAPTER_FIELDS:
        raise ContractError("frozen Codex review adapter fields are invalid")
    if (adapter["schema_version"] != 1 or type(adapter["schema_version"]) is not int
            or adapter["harness"] != "codex"
            or adapter["transport"] != "native_protocol"
            or adapter["model_provider"] != "openai"):
        raise ContractError("frozen Codex review adapter identity is invalid")
    for field in ("binary", "binary_sha256", "harness_version", "output_schema_sha256"):
        if not isinstance(adapter[field], str) or not adapter[field]:
            raise ContractError("frozen Codex review adapter value is invalid")
    if (len(adapter["binary_sha256"]) != 64
            or len(adapter["output_schema_sha256"]) != 64):
        raise ContractError("frozen Codex review adapter hash is invalid")
    if (not isinstance(profile, dict) or profile.get("harness") != "codex"
            or profile.get("permission_policy") != "read_only"):
        raise ContractError("frozen profile is not a read-only Codex reviewer")
    schema_hash = hashlib.sha256(canonical_json(review_output_schema()).encode()).hexdigest()
    if schema_hash != adapter["output_schema_sha256"]:
        raise ContractError("frozen review output schema changed")
    return adapter, profile


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("native Codex review exceeded its deadline")
    return remaining


def _response_result(response: dict[str, Any], label: str) -> dict[str, Any]:
    if "error" in response:
        raise ContractError(f"{label} failed: {response['error']}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise ContractError(f"{label} returned no result")
    return result


def _usage(events: list[dict[str, Any]], thread_id: str, turn_id: str) -> dict[str, Any]:
    for event in reversed(events):
        if event.get("method") != "thread/tokenUsage/updated":
            continue
        params = event.get("params")
        if (not isinstance(params, dict) or params.get("threadId") != thread_id
                or params.get("turnId") not in {None, turn_id}):
            continue
        token_usage = params.get("tokenUsage")
        total = token_usage.get("total") if isinstance(token_usage, dict) else None
        if not isinstance(total, dict):
            continue
        values = [total.get(key) for key in ("inputTokens", "outputTokens", "totalTokens")]
        if all(type(value) is int and value >= 0 for value in values):
            return {
                "input_tokens": values[0],
                "output_tokens": values[1],
                "total_tokens": values[2],
                "source": "native_reported",
            }
    return {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "source": "unavailable",
    }


def _stop_server(
    process: subprocess.Popen[bytes],
    writer: io.TextIOWrapper | None,
    stderr: BoundedDrain | None,
) -> None:
    if writer is not None:
        try:
            writer.close()
        except OSError:
            pass
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    if stderr is not None:
        metadata = stderr.finish()
        if stderr.content:
            sys.stderr.buffer.write(bytes(stderr.content))
        if metadata["truncated"]:
            sys.stderr.write(
                f"\n[Codex stderr truncated; total_bytes={metadata['total_bytes']} "
                f"sha256={metadata['full_sha256']}]\n"
            )


def run(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ContractError("workflow snapshot must be an object")
    adapter, profile = _validated_adapter(snapshot)
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
            [str(binary), "--version"],
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CapabilityUnavailable(
            "frozen Codex version could not be re-observed"
        ) from exc
    if version.returncode != 0 or version.stdout.strip() != adapter["harness_version"]:
        raise CapabilityUnavailable("Codex version changed after preflight")

    effort = profile["effort"]["value"]
    model = profile["model_id"]
    workspace = snapshot["workspace"]
    review_root = Path(workspace["path"]).resolve(strict=True)
    argv = [
        str(binary),
        "-c", f'model="{model}"',
        "-c", f'model_reasoning_effort="{effort}"',
        "app-server", "--listen", "stdio://",
    ]
    process: subprocess.Popen[bytes] | None = None
    writer = None
    stderr = None
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
        process = subprocess.Popen(
            argv,
            cwd=review_root,
            env=os.environ.copy(),
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
        _response_result(
            receive_response(
                peer, 1, timeout_seconds=_remaining(deadline),
                on_notification=record,
            ),
            "Codex initialize",
        )
        peer.send(initialized_notification())
        models = discover_models(
            peer,
            first_request_id=10,
            timeout_seconds=_remaining(deadline),
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
            100,
            cwd=str(review_root),
            model=model,
            permission="read_only",
            ephemeral=True,
        ))
        thread_result = _response_result(
            receive_response(
                peer, 100, timeout_seconds=_remaining(deadline),
                on_notification=record,
            ),
            "Codex thread/start",
        )
        thread = thread_result.get("thread")
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        reported_version = thread.get("cliVersion") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise ContractError("Codex thread/start returned no thread id")
        if reported_version and f"codex-cli {reported_version}" != adapter["harness_version"]:
            raise ContractError("Codex thread reported a different harness version")
        expected_policy = {"type": "readOnly", "networkAccess": False}
        reported_cwd = thread_result.get("cwd")
        if not isinstance(reported_cwd, str) or not reported_cwd:
            raise ContractError("Codex thread returned an invalid working directory")
        if (thread_result.get("model") != model
                or thread_result.get("reasoningEffort") != effort
                or thread_result.get("modelProvider") != adapter["model_provider"]
                or thread_result.get("approvalPolicy") != "never"
                or thread_result.get("sandbox") != expected_policy
                or Path(reported_cwd).resolve() != review_root):
            raise ContractError("Codex thread did not preserve the frozen execution identity")

        prompt = build_review_prompt(snapshot["task"], workspace)
        peer.send(turn_start_request(
            101,
            thread_id=thread_id,
            prompt=prompt,
            model=model,
            effort=effort,
            cwd=str(review_root),
            permission="read_only",
            output_schema=review_output_schema(),
        ))
        turn_result = _response_result(
            receive_response(
                peer, 101, timeout_seconds=_remaining(deadline),
                on_notification=record,
            ),
            "Codex turn/start",
        )
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
            if output_bytes > MAX_REVIEW_BYTES:
                raise ContractError("native Codex review output exceeds its byte limit")
        if state.terminal_status != "completed":
            raise ContractError(
                f"native Codex review did not complete successfully: {state.terminal_status}"
            )
        review = decode_review_document(
            "".join(state.output).strip(), snapshot["task"], workspace,
        )
        usage = _usage(protocol_events, thread_id, turn_id)
    finally:
        if process is not None:
            _stop_server(process, writer, stderr)

    observed_identity = {
        "harness": "codex",
        "harness_version": adapter["harness_version"],
        "model_provider": adapter["model_provider"],
        "model_id": model,
        "effort": effort,
        "permission_policy": "read_only",
        "verification": "verified",
    }
    return run_review_and_checks(
        snapshot,
        review,
        observed_identity=observed_identity,
        native_ids={"thread_id": thread_id, "turn_id": turn_id},
        native_model_requests=None,
        usage=usage,
    )


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_SNAPSHOT_BYTES + 1)
    if len(payload) > MAX_SNAPSHOT_BYTES:
        raise ContractError("workflow snapshot exceeds its byte limit")
    try:
        snapshot = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("workflow snapshot is not valid UTF-8 JSON") from exc
    sys.stdout.write(canonical_json(run(snapshot)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
