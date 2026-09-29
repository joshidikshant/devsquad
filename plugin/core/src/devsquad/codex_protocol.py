"""Typed JSON-RPC preparation/state normalization for Codex app-server.

This module does not spawn or supervise the server. M2 gives it a connected
stdio stream owned by the run supervisor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import selectors
import time
from typing import Any

from .contracts import ContractError


class JsonLinePeer:
    """Protocol codec over supervisor-owned streams; does not own the process."""
    def __init__(self, reader: Any, writer: Any, *, max_frame_bytes: int = 4 * 1024 * 1024):
        self.reader, self.writer = reader, writer
        self._fd = reader.fileno()
        self._buffer = bytearray()
        self._max_frame_bytes = max_frame_bytes

    def send(self, message: dict[str, Any]) -> None:
        self.writer.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.writer.flush()

    def receive(self, timeout_seconds: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while b"\n" not in self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("native protocol response timed out")
            selector = selectors.DefaultSelector()
            try:
                selector.register(self._fd, selectors.EVENT_READ)
                if not selector.select(remaining):
                    raise TimeoutError("native protocol response timed out")
            finally:
                selector.close()
            chunk = os.read(self._fd, 65536)
            if not chunk:
                raise EOFError("native protocol disconnected")
            self._buffer.extend(chunk)
            if len(self._buffer) > self._max_frame_bytes:
                raise ContractError("native protocol frame exceeds byte bound")
        raw, _, remainder = self._buffer.partition(b"\n")
        self._buffer = bytearray(remainder)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("native protocol returned malformed JSON") from exc
        if not isinstance(value, dict):
            raise ContractError("native protocol message must be an object")
        return value


def request(request_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": request_id, "method": method, "params": params or {}}


def initialize_request(request_id: int = 1) -> dict[str, Any]:
    return request(request_id, "initialize", {"clientInfo": {"name": "devsquad", "version": "0.1.0"}, "capabilities": {"experimentalApi": True}})


def initialized_notification() -> dict[str, Any]:
    return {"method": "initialized", "params": {}}


def thread_start_request(
    request_id: int,
    *,
    cwd: str,
    model: str,
    permission: str,
    ephemeral: bool = False,
) -> dict[str, Any]:
    sandbox = {"read_only": "read-only", "workspace_write": "workspace-write"}.get(permission)
    if sandbox is None:
        raise ContractError(f"unsupported native permission: {permission}")
    if type(ephemeral) is not bool:
        raise ContractError("native thread ephemeral flag must be boolean")
    return request(request_id, "thread/start", {"cwd": cwd, "model": model, "sandbox": sandbox, "approvalPolicy": "never", "ephemeral": ephemeral})


def model_list_request(request_id: int, cursor: str | None = None, limit: int = 100) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    return request(request_id, "model/list", params)


def turn_start_request(request_id: int, *, thread_id: str, prompt: str, model: str | None, effort: str | None, cwd: str, permission: str, output_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = {"read_only": {"type": "readOnly", "networkAccess": False}, "workspace_write": {"type": "workspaceWrite", "writableRoots": [cwd], "networkAccess": False}}.get(permission)
    if policy is None:
        raise ContractError(f"unsupported native permission: {permission}")
    params: dict[str, Any] = {"threadId": thread_id, "input": [{"type": "text", "text": prompt}], "cwd": cwd, "sandboxPolicy": policy, "approvalPolicy": "never"}
    if model:
        params["model"] = model
    if effort:
        params["effort"] = effort
    if output_schema is not None:
        params["outputSchema"] = output_schema
    return request(request_id, "turn/start", params)


def review_start_request(request_id: int, *, thread_id: str, target: dict[str, Any]) -> dict[str, Any]:
    allowed = {"uncommittedChanges", "baseBranch", "commit", "custom"}
    if target.get("type") not in allowed:
        raise ContractError("unsupported review target")
    return request(request_id, "review/start", {"threadId": thread_id, "target": target, "delivery": "inline"})


def turn_interrupt_request(request_id: int, *, thread_id: str, turn_id: str) -> dict[str, Any]:
    return request(request_id, "turn/interrupt", {"threadId": thread_id, "turnId": turn_id})


@dataclass
class NativeTurnState:
    thread_id: str | None = None
    turn_id: str | None = None
    terminal: bool = False
    interrupted_acknowledged: bool = False
    output: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    terminal_status: str | None = None
    error: dict[str, Any] | None = None

    def consume(self, message: dict[str, Any]) -> None:
        if not isinstance(message, dict):
            raise ContractError("native message must be an object")
        self.events.append(message)
        method = message.get("method", "")
        params = message.get("params", message.get("result", {}))
        if method in {"thread/started", "thread/start/completed", "turn/started", "item/agentMessage/delta", "turn/output/delta", "item/completed", "turn/completed", "error"} and not isinstance(params, dict):
            raise ContractError("native event params must be an object")
        if not isinstance(params, dict):
            return
        if method in {"thread/started", "thread/start/completed"}:
            thread = params.get("thread", {})
            if not isinstance(thread, dict):
                raise ContractError("native thread must be an object")
            candidate = thread.get("id") or params.get("threadId")
            if candidate is not None and not isinstance(candidate, str):
                raise ContractError("native thread id must be a string")
            if self.thread_id and candidate and candidate != self.thread_id:
                return
            self.thread_id = candidate or self.thread_id
        message_thread = params.get("threadId")
        turn = params.get("turn") or {}
        if not isinstance(turn, dict):
            raise ContractError("native turn must be an object")
        message_turn = turn.get("id") or params.get("turnId")
        if message_thread is not None and not isinstance(message_thread, str):
            raise ContractError("native thread id must be a string")
        if message_turn is not None and not isinstance(message_turn, str):
            raise ContractError("native turn id must be a string")
        if self.thread_id and message_thread and message_thread != self.thread_id:
            return
        if self.turn_id and message_turn and message_turn != self.turn_id:
            return
        if method == "turn/started":
            self.thread_id = message_thread or self.thread_id
            self.turn_id = message_turn or self.turn_id
        if method in {"item/agentMessage/delta", "turn/output/delta"}:
            delta = params.get("delta", "")
            if not isinstance(delta, str):
                raise ContractError("native output delta must be a string")
            if self.thread_id and self.turn_id and message_thread == self.thread_id and message_turn == self.turn_id:
                self.output.append(delta)
        if (method == "item/completed" and not "".join(self.output).strip()
                and self.thread_id and self.turn_id
                and message_thread == self.thread_id and message_turn == self.turn_id):
            item = params.get("item")
            if not isinstance(item, dict):
                raise ContractError("native completed item must be an object")
            if item.get("type") in {"agentMessage", "agent_message"}:
                content = item.get("text", item.get("content"))
                if not isinstance(content, str):
                    raise ContractError("native completed agent message must contain text")
                self.output.append(content)
        if method == "turn/completed" and self.thread_id and self.turn_id and message_thread == self.thread_id and message_turn == self.turn_id:
            if not "".join(self.output).strip():
                items = turn.get("items")
                if items is not None:
                    if not isinstance(items, list):
                        raise ContractError("native terminal turn items must be an array")
                    completed_output = None
                    for item in items:
                        if not isinstance(item, dict):
                            raise ContractError("native terminal turn item must be an object")
                        if item.get("type") in {"agentMessage", "agent_message"}:
                            content = item.get("text", item.get("content"))
                            if not isinstance(content, str):
                                raise ContractError(
                                    "native terminal agent message must contain text"
                                )
                            if content.strip():
                                completed_output = content
                    if completed_output is not None:
                        self.output.append(completed_output)
            self.terminal = True
            self.terminal_status = turn.get("status")
            turn_error = turn.get("error")
            if turn_error is not None:
                if not isinstance(turn_error, dict):
                    raise ContractError("native terminal turn error must be an object")
                self.error = turn_error
        if method == "error" and self.thread_id and self.turn_id and message_thread == self.thread_id and message_turn == self.turn_id and not params.get("willRetry", False):
            self.terminal = True
            self.terminal_status = "failed"
            self.error = params.get("error")

    def acknowledge_interrupt(self, response: dict[str, Any]) -> None:
        if "error" in response:
            raise ContractError(f"turn/interrupt failed: {response['error']}")
        self.interrupted_acknowledged = True

    def disconnected(self) -> None:
        if not self.terminal:
            self.terminal_status = "transport_disconnected"


def parse_model_page(response: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    if "error" in response:
        raise ContractError(f"model/list failed: {response['error']}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise ContractError("model/list missing result")
    models = result["data"] if "data" in result else result.get("models")
    if not isinstance(models, list):
        raise ContractError("model/list is incomplete")
    cursor = result.get("nextCursor") or result.get("next_cursor")
    return models, cursor


def collect_model_pages(fetch_page: Any, *, max_pages: int = 100) -> list[dict[str, Any]]:
    cursor = None
    seen: set[str] = set()
    all_models: list[dict[str, Any]] = []
    for _ in range(max_pages):
        models, next_cursor = parse_model_page(fetch_page(cursor))
        all_models.extend(models)
        if next_cursor is None:
            return all_models
        if next_cursor in seen:
            raise ContractError("model/list repeated pagination cursor")
        seen.add(next_cursor)
        cursor = next_cursor
    raise ContractError("model/list exceeded page bound")


def receive_response(peer: JsonLinePeer, request_id: int, *, timeout_seconds: float, on_notification: Any | None = None) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"native request {request_id} timed out")
        message = peer.receive(remaining)
        if message.get("id") == request_id:
            return message
        if "method" in message and "id" not in message:
            if on_notification:
                on_notification(message)
            continue
        raise ContractError(f"unexpected native response while awaiting request {request_id}")


def discover_models(peer: JsonLinePeer, *, first_request_id: int = 10, timeout_seconds: float = 5, max_pages: int = 100) -> list[dict[str, Any]]:
    """Collect a complete native snapshot from an already initialized peer."""
    request_id, cursor = first_request_id, None
    responses: list[dict[str, Any]] = []
    for _ in range(max_pages):
        peer.send(model_list_request(request_id, cursor))
        response = receive_response(peer, request_id, timeout_seconds=timeout_seconds)
        responses.append(response)
        _, cursor = parse_model_page(response)
        if cursor is None:
            return collect_model_pages(lambda ignored: responses.pop(0), max_pages=len(responses))
        request_id += 1
    raise ContractError("model/list exceeded page bound")
