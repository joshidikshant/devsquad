"""One Council role, executed inside the existing gated durable attempt."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

from .contracts import ContractError
from .council import MAX_PACKET_BYTES, PROMPT_VERSION, digest, sanitized_proposals, output_schema, role_prompt, prompt_digest
from .council_runtime import selected_for
from .store import canonical_json


def role_packet(snapshot: dict, role: str) -> dict:
    packet = {"brief": snapshot["council_brief"]}
    state = snapshot["council_state"]
    if role in {"critic", "lead"}:
        if not {"proposer_a", "proposer_b"}.issubset(state["documents"]):
            raise ContractError("Council proposal barrier has not completed")
        packet["proposals"] = sanitized_proposals(snapshot)
    if role == "lead":
        packet["critique"] = state["documents"]["critic"]["document"]
        packet["checks"] = state["documents"]["critic"]["checks"]
    return packet


def run(snapshot: dict) -> dict:
    role = snapshot["council_role"]
    selected = selected_for(snapshot, role, snapshot["council_profile_index"])
    packet = role_packet(snapshot, role)
    directory = Path(snapshot["council_directories"][role])
    # The role directory contains only its frozen/sanitized packet. Coordinator
    # logs, ledger, raw provenance and all peer directories are outside the jail.
    packet_path = directory / "evidence.json"
    encoded = canonical_json(packet).encode()
    if len(encoded) > MAX_PACKET_BYTES:
        raise ContractError("Council role packet exceeds its byte cap")
    if packet_path.exists() and packet_path.read_bytes() != encoded:
        raise ContractError("Council role packet changed after finalization")
    packet_path.write_bytes(encoded)
    fixture = snapshot["council_fixture"]
    boundary_sha256 = None
    if fixture is not None:
        value = fixture.get(role)
        if not isinstance(value, dict):
            raise ContractError(f"Council fixture is missing {role}")
        if value.get("delay_seconds"):
            time.sleep(value["delay_seconds"])
        if value.get("error"):
            raise ContractError(value["error"])
        result = {"document": value.get("document"), "observed_identity": None, "native_ids": None, "usage": None}
        identity_scope = "all_fixture"
    else:
        from .codex_review_worker import run as native_turn
        adapter = snapshot["council_adapters"][role][selected["profile_id"]]
        boundary = snapshot["council_boundaries"][role][selected["profile_id"]]
        native = {"task": snapshot["task"], "workspace": {"path": str(directory)},
                  "routing": {"roles": {role: {"selected": selected}}},
                  "council_adapter": adapter, "council_boundary": boundary}
        prompt = role_prompt(role, packet)
        result = native_turn(native, council_role=role, council_prompt=prompt)
        identity_scope = "native_verified"
        boundary_sha256 = boundary["profile_sha256"]
    checks = []
    if role == "critic":
        from .review_worker import run_review_and_checks
        from .workflows import review_mode
        # Reuse the trusted, isolated candidate-check path. This temporary
        # internal review document is only a check driver, never Council review evidence.
        check_snapshot = json.loads(canonical_json(snapshot))
        check_snapshot["task"]["workflow"] = "branch-review"
        check_snapshot["task"].pop("council", None)
        check_snapshot["routing"]["roles"]["reviewer"] = {"selected": selected}
        workspace = snapshot["workspace"]
        driver = {"schema_version": 1, "candidate_sha256": workspace["candidate_sha256"],
                  "base_oid": workspace["base_oid"], "target_oid": workspace["target_oid"],
                  "review_mode": review_mode(check_snapshot["task"]), "verdict": "clean", "summary": "Trusted checks only", "findings": []}
        checks = run_review_and_checks(check_snapshot, driver)["checks"]
    return {"schema_version": 1, "role": role, "brief_sha256": digest(snapshot["council_brief"]),
            "prompt_version": PROMPT_VERSION, "prompt_sha256": prompt_digest(role, packet),
            "role_packet_sha256": digest(packet), "output_schema_sha256": digest(output_schema(role)),
            "profile": selected["profile"], "profile_sha256": selected["profile_sha256"],
            **result, "identity_scope": identity_scope, "checks": checks, "boundary_sha256": boundary_sha256}


def main() -> int:
    payload = sys.stdin.buffer.read(4 * MAX_PACKET_BYTES + 1)
    if len(payload) > 4 * MAX_PACKET_BYTES:
        raise ContractError("Council worker snapshot exceeds its byte cap")
    snapshot = json.loads(payload.decode("utf-8"))
    sys.stdout.write(canonical_json(run(snapshot)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
