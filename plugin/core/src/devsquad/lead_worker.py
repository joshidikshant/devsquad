"""Run one frozen offline headless-lead fixture for branch-review tests."""

from __future__ import annotations

import json
import sys
from typing import Any

from .contracts import ContractError
from .store import canonical_json
from .workflows import MAX_EVIDENCE_BYTES, make_headless_lead_evidence


def run(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ContractError("workflow snapshot must be an object")
    fixture = snapshot.get("internal_lead_fixture")
    handoff = snapshot.get("headless_handoff")
    if not isinstance(fixture, dict) or not isinstance(handoff, dict):
        raise ContractError("offline headless lead snapshot is incomplete")
    choice = {
        "schema_version": 1,
        "candidate_sha256": handoff["packet"]["candidate_sha256"],
        **fixture,
    }
    return make_headless_lead_evidence(snapshot, handoff, choice)


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
