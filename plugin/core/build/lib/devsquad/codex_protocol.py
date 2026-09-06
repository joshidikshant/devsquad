"""Typed JSON-RPC preparation/state normalization for Codex app-server.

This module does not spawn or supervise the server. M2 gives it a connected
stdio stream owned by the run supervisor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contracts import ContractError


def request(request_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def initialize_request(request_id: int = 1) -> dict[str, Any]:
    return request(request_id, "initialize", {"clientInfo": {"name": "devsquad", "version": "0.1.0"}, "capabilities": {"experimentalApi": True}})


def model_list_request(request_id: int, cursor: str | None = None, limit: int = 100) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    return request(request_id, "model/list", params)


def turn_start_request(request_id: int, *, thread_id: str, prompt: str, model: str | None, effort: str | None, cwd: str, sandbox: str) -> dict[str, Any]:
    params: dict[str, Any] = {"threadId": thread_id, "input": [{"type": "text", "text": prompt}], "cwd": cwd, "permissions": sandbox}
    if model:
        params["model"] = model
    if effort:
        params["effort"] = effort
    return request(request_id, "turn/start", params)


@dataclass
class NativeTurnState:
    thread_id: str | None = None
    turn_id: str | None = None
    terminal: bool = False
    interrupted_acknowledged: bool = False
    output: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def consume(self, message: dict[str, Any]) -> None:
        if not isinstance(message, dict):
            raise ContractError("native message must be an object")
        self.events.append(message)
        method = message.get("method", "")
        params = message.get("params") or message.get("result") or {}
        if method in {"thread/started", "thread/start/completed"}:
            self.thread_id = params.get("thread", {}).get("id") or params.get("threadId") or self.thread_id
        if method in {"turn/started", "turn/start/completed"}:
            self.turn_id = params.get("turn", {}).get("id") or params.get("turnId") or self.turn_id
        if method in {"item/agentMessage/delta", "turn/output/delta"}:
            self.output.append(params.get("delta", ""))
        if method in {"turn/interrupt/completed", "turn/interrupted/acknowledged"}:
            self.interrupted_acknowledged = True
        if method in {"turn/completed", "review/completed", "turn/failed", "turn/interrupted"}:
            self.terminal = True


def parse_model_page(response: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    if "error" in response:
        raise ContractError(f"model/list failed: {response['error']}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise ContractError("model/list missing result")
    models = result.get("data") or result.get("models")
    if not isinstance(models, list):
        raise ContractError("model/list is incomplete")
    cursor = result.get("nextCursor") or result.get("next_cursor")
    return models, cursor
