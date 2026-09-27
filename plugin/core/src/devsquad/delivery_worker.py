"""Offline bounded implementation worker used for delivery fault injection."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import sys
import time
from typing import Any

from .contracts import ContractError
from .store import canonical_json
from .workflows import make_implementation_evidence


MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_FIXTURE_WRITES = 100
MAX_FIXTURE_CONTENT_BYTES = 1024 * 1024


def _relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise ContractError("implementation fixture path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ContractError("implementation fixture path must be repository-relative")
    return value


def run(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ContractError("delivery snapshot must be an object")
    fixture = snapshot.get("internal_implementation_fixture")
    if isinstance(fixture, dict) and set(fixture) == {"iterations"}:
        fixtures = fixture["iterations"]
        index = len(snapshot.get("delivery_iterations", []))
        if (not isinstance(fixtures, list) or not fixtures
                or index >= len(fixtures)):
            raise ContractError("offline implementation fixture iteration is missing")
        fixture = fixtures[index]
    if not isinstance(fixture, dict) or set(fixture) != {"writes", "delay_seconds"}:
        raise ContractError("offline implementation fixture is incomplete")
    writes, delay = fixture["writes"], fixture["delay_seconds"]
    if (not isinstance(writes, list) or not writes
            or len(writes) > MAX_FIXTURE_WRITES):
        raise ContractError("implementation fixture writes must be a bounded array")
    if not isinstance(delay, (int, float)) or isinstance(delay, bool) or not 0 <= delay <= 60:
        raise ContractError("implementation fixture delay is invalid")
    workspace = Path(snapshot["delivery_workspace"]["path"]).resolve(strict=True)
    if delay:
        time.sleep(delay)
    for item in writes:
        if not isinstance(item, dict) or set(item) != {"path", "content"}:
            raise ContractError("implementation fixture write fields are invalid")
        relative = _relative_path(item["path"])
        content = item["content"]
        if (not isinstance(content, str)
                or len(content.encode()) > MAX_FIXTURE_CONTENT_BYTES):
            raise ContractError("implementation fixture content is invalid")
        destination = workspace / relative
        resolved = destination.resolve(strict=False)
        if resolved != workspace and workspace not in resolved.parents:
            raise ContractError("implementation fixture path escapes its workspace")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
    return make_implementation_evidence(
        snapshot, "Applied the bounded offline implementation fixture.",
    )


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_SNAPSHOT_BYTES + 1)
    if len(payload) > MAX_SNAPSHOT_BYTES:
        raise ContractError("delivery snapshot exceeds its byte limit")
    try:
        snapshot = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("delivery snapshot is not valid UTF-8 JSON") from exc
    sys.stdout.write(canonical_json(run(snapshot)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
