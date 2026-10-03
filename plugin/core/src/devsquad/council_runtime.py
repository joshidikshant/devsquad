"""Council stage projections over the existing durable runner and handoff store."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .contracts import CapabilityUnavailable, ContractError
from .council import (ROLES, MAX_PACKET_BYTES, decode, digest, label_mapping, output_schema,
                      sanitized_proposals, validate_choice, validate_critique, validate_proposal, verify_identities)
from .store import ConflictError, canonical_json, request_hash


def frozen_fields(snapshot: dict) -> dict:
    return {key: snapshot[key] for key in ("task", "base_oid", "target_oid", "configs", "routing",
            "workspace", "check_workspace", "council_brief", "council_directories", "council_adapters",
            "council_boundaries", "council_fixture")}


def verify_origin(store, run_id: str, snapshot: dict) -> None:
    artifact = store.artifact_named(run_id, "council-origin.json")
    if (artifact is None or hashlib.sha256(Path(artifact["path"]).read_bytes()).hexdigest() != artifact["sha256"]
            or digest(frozen_fields(snapshot)) != snapshot.get("council_origin_sha256")
            or artifact["sha256"] != snapshot["council_origin_sha256"]):
        raise ConflictError("Council frozen inputs changed or are missing")


def verified_artifact(store, run_id: str, name: str, expected_sha256: str | None = None) -> bytes:
    artifact = store.artifact_named(run_id, name)
    if artifact is None:
        raise ConflictError("Council evidence artifact is missing")
    path = Path(artifact["path"])
    expected_parent = (store.artifacts / run_id).resolve()
    if path.is_symlink() or path.resolve(strict=True).parent != expected_parent:
        raise ConflictError("Council evidence artifact escaped its private directory")
    raw = path.read_bytes()
    if (len(raw) != artifact["byte_size"] or hashlib.sha256(raw).hexdigest() != artifact["sha256"]
            or expected_sha256 is not None and artifact["sha256"] != expected_sha256):
        raise ConflictError("Council evidence artifact is corrupt")
    return raw


def validate_saved_handoff(store, run_id: str, handoff, snapshot: dict) -> None:
    """Re-derive quorum/check truth from exact imported stage artifacts."""
    verify_origin(store, run_id, snapshot)
    state = snapshot["council_state"]
    if set(state["documents"]) != set(ROLES) or state["next_role"] != "lead":
        raise ConflictError("Council handoff has no complete sealed proposal/critic quorum")
    attempts = {a["id"]: a for a in store.attempts_for_run(run_id)}
    for role in ROLES:
        reference = state["artifacts"][role]
        evidence = decode(verified_artifact(store, run_id, reference["name"], reference["sha256"]))
        matching = [a for a in attempts.values() if reference["name"] == f"council-{role}-{a['id']}.json"]
        if len(matching) != 1 or matching[0]["status"] != "finished" or evidence != state["documents"][role]:
            raise ConflictError("Council finalized stage projection differs from its actual imported attempt")
        validate_document(evidence, snapshot, matching[0])
    verify_identities(state["documents"], fixture=snapshot["council_fixture"] is not None)
    mapping = label_mapping(snapshot["task"]["council"]["seed"], ["proposer_a", "proposer_b"])
    checks = state["documents"]["critic"]["checks"]
    failures = [c["id"] for c in checks if c["required_to_pass"] and c["status"] != "passed"]
    integrity = [c["id"] for c in checks if c.get("integrity", {}).get("status") in {"violated", "not_run"}]
    packet = handoff.packet
    if (digest(packet) != handoff.packet_sha256 or packet.get("workflow") != "council-decision"
            or packet.get("brief_sha256") != digest(snapshot["council_brief"])
            or packet.get("candidate_sha256") != snapshot["workspace"]["candidate_sha256"]
            or packet.get("base_oid") != snapshot["base_oid"] or packet.get("target_oid") != snapshot["target_oid"]
            or packet.get("label_mapping") != mapping or packet.get("checks") != checks
            or packet.get("critique") != state["documents"]["critic"]["document"]
            or packet.get("proposals") != sanitized_proposals(snapshot)
            or packet.get("evaluation") != {"accept_allowed": not failures and not integrity,
                "required_failures": failures, "integrity_failures": integrity}):
        raise ConflictError("Council saved handoff differs from frozen evidence/check truth")
    for reference in packet["artifacts"]:
        raw = verified_artifact(store, run_id, reference["name"], reference["sha256"])
        if reference["name"].startswith("council-evidence-") and decode(raw) != {
                "documents": state["documents"], "label_mapping": mapping, "brief_sha256": digest(snapshot["council_brief"])}:
            raise ConflictError("Council raw provenance bundle differs from finalized evidence")


def prepare(snapshot: dict, *, store, run_id: str, runtime: Path, fixture: dict | None) -> None:
    from .codex_review_worker import freeze_codex_role
    from .council_isolation import freeze_boundary, verify_read_boundary, verify_native_bootstrap
    from .workspaces import committed_regular_file, _git

    task = snapshot["task"]
    if fixture is not None and not isinstance(fixture, dict):
        raise ContractError("Council fixture must be an object")
    brief = {"schema_version": 1, "goal": task["goal"], "base_oid": snapshot["base_oid"],
             "target_oid": snapshot["target_oid"], "scope": task["scope"], "acceptance": task["acceptance"],
             "rubric": task["council"]["rubric"], "evidence": [], "source_files": []}
    for ref in task["council"]["evidence"]:
        row = store.connection.execute(
            "SELECT a.*,r.project_id FROM artifacts a JOIN runs r ON r.id=a.run_id WHERE a.id=?", (ref["artifact_id"],)
        ).fetchone()
        if row is None or row["project_id"] != store.run(run_id)["project_id"] or row["sha256"] != ref["sha256"]:
            raise ContractError("Council evidence is missing, belongs to another project, or has changed")
        raw = Path(row["path"]).read_bytes()
        if len(raw) != row["byte_size"] or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise ConflictError("Council evidence artifact is corrupt")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractError("Council evidence must be UTF-8 text") from exc
        brief["evidence"].append({**ref, "content": content})
    repo = Path(task["project"]["repo_path"])
    files = _git(repo, "ls-tree", "-r", "--name-only", "-z", snapshot["target_oid"]).split(b"\0")
    for encoded in files:
        if not encoded:
            continue
        path = encoded.decode("utf-8", "surrogateescape")
        if not any(scope == "." or path == scope or path.startswith(scope.rstrip("/") + "/") for scope in task["scope"]["read_paths"]):
            continue
        try:
            raw = committed_regular_file(repo, snapshot["target_oid"], path)
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractError("Council scoped source must be UTF-8; narrow the read scope") from exc
        source_hash = hashlib.sha256(raw).hexdigest()
        brief["source_files"].append({"path": path, "artifact_id": f"source:{path}:{source_hash}", "sha256": source_hash, "content": content})
        if len(canonical_json(brief).encode()) > MAX_PACKET_BYTES:
            raise ContractError("Council evidence exceeds its cap; narrow the read scope")
    if len(canonical_json(brief).encode()) > MAX_PACKET_BYTES:
        raise ContractError("Council evidence exceeds its byte cap")
    snapshot.update(council_brief=brief, council_directories={}, council_adapters={}, council_boundaries={},
                    council_fixture=fixture, council_state={"documents": {}, "next_role": "proposer_a"})
    for role in (*ROLES, "lead"):
        directory = runtime / "council" / run_id / role
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        snapshot["council_directories"][role] = str(directory.resolve())
        # A common-brief-only sentinel is also the actual per-role filesystem
        # probe target. Peer proposals never enter another author's directory.
        (directory / "brief.json").write_bytes(canonical_json(brief).encode())
    private_log = runtime / "private-logs" / f"{run_id}.boundary-probe"
    private_log.parent.mkdir(parents=True, exist_ok=True)
    private_log.write_bytes(b"Private coordinator boundary probe\n")
    private_log.chmod(0o600)
    prior_probe = store.artifact_named(run_id, "council-boundary-probe.json")
    if prior_probe is None:
        store.store_artifact(run_id, "council-boundary-probe.json", b'{"private_coordinator_probe":true}\n')
    private_artifact = Path(store.artifact_named(run_id, "council-boundary-probe.json")["path"])
    for role in (*ROLES, "lead"):
        directory = Path(snapshot["council_directories"][role])
        if fixture is None and (role != "lead" or task["lead"]["mode"] == "headless"):
            routed = snapshot["routing"]["roles"][role]
            snapshot["council_adapters"][role] = {}
            snapshot["council_boundaries"][role] = {}
            for selected in [routed["selected"], *routed["fallbacks"]]:
                adapter = freeze_codex_role(selected, role=role, output_schema=output_schema(role))
                snapshot["council_adapters"][role][selected["profile_id"]] = adapter
                boundary = freeze_boundary(executable=Path(adapter["binary"]), evidence=directory, native_codex=True)
                forbidden = tuple(Path(path) / "brief.json" for peer, path in snapshot["council_directories"].items() if peer != role)
                verify_read_boundary(boundary, own_file=directory / "brief.json",
                                     forbidden=(*forbidden, runtime / "state.sqlite3", private_log, private_artifact))
                verify_native_bootstrap(boundary, executable=Path(adapter["binary"]),
                                        expected_version=adapter["harness_version"], evidence=directory)
                from .council_isolation import verify_native_network
                verify_native_network(boundary)
                snapshot["council_boundaries"][role][selected["profile_id"]] = {**boundary, "probe_status": "passed"}
    snapshot["council_origin_sha256"] = digest(frozen_fields(snapshot))
    content = canonical_json(frozen_fields(snapshot)).encode()
    prior = store.artifact_named(run_id, "council-origin.json")
    if prior is None:
        store.store_artifact(run_id, "council-origin.json", content)
    elif prior["sha256"] != snapshot["council_origin_sha256"]:
        raise ConflictError("recovered Council preparation changed frozen inputs")


def selected_for(snapshot: dict, role: str, index: int) -> dict:
    route = snapshot["routing"]["roles"][role]
    return [route["selected"], *route["fallbacks"]][index]


def validate_document(evidence: dict, snapshot: dict, attempt: dict) -> dict:
    from .council import exact, text, PROMPT_VERSION, output_schema, prompt_digest
    from .council_worker import role_packet
    exact(evidence, {"schema_version", "role", "brief_sha256", "profile", "profile_sha256", "document",
                     "observed_identity", "identity_scope", "native_ids", "usage", "checks", "boundary_sha256",
                     "prompt_version", "prompt_sha256", "role_packet_sha256", "output_schema_sha256"}, "Council attempt")
    role = attempt["role"]
    selected = selected_for(snapshot, role, attempt["profile_index"])
    packet = role_packet(snapshot, role)
    if (evidence["schema_version"] != 1 or evidence["role"] != role or evidence["profile"] != selected["profile"]
            or evidence["profile_sha256"] != selected["profile_sha256"]
            or attempt["profile_id"] != selected["profile_id"]
            or evidence["brief_sha256"] != digest(snapshot["council_brief"])
            or evidence["prompt_version"] != PROMPT_VERSION
            or evidence["prompt_sha256"] != prompt_digest(role, packet)
            or evidence["role_packet_sha256"] != digest(packet)
            or evidence["output_schema_sha256"] != digest(output_schema(role))):
        raise ContractError("Council evidence differs from its actual frozen role/profile/brief")
    fixture = snapshot["council_fixture"] is not None
    identity = evidence["observed_identity"]
    if fixture:
        if (identity is not None or evidence["identity_scope"] != "all_fixture" or evidence["boundary_sha256"] is not None
                or evidence["native_ids"] is not None or evidence["usage"] is not None):
            raise ContractError("Council fixture cannot claim native identity/isolation")
    else:
        adapter = snapshot["council_adapters"][role][selected["profile_id"]]
        exact(identity, {"harness", "harness_version", "model_provider", "model_id", "effort", "permission_policy", "verification"},
              "observed Council native identity")
        expected_identity = {"harness": adapter["harness"], "harness_version": adapter["harness_version"],
            "model_provider": adapter["model_provider"], "model_id": selected["profile"]["model_id"],
            "effort": selected["profile"]["effort"]["value"], "permission_policy": "read_only", "verification": "verified"}
        if (identity != expected_identity or evidence["identity_scope"] != "native_verified"
                or evidence["boundary_sha256"] != snapshot["council_boundaries"][role][selected["profile_id"]]["profile_sha256"]):
            raise ContractError("Council requires verified native identity and frozen read isolation")
        exact(evidence["native_ids"], {"thread_id", "turn_id"}, "Council native IDs")
        for key, value in evidence["native_ids"].items():
            text(value, f"Council native {key}")
            if len(value) > 500:
                raise ContractError("Council native IDs exceed their bound")
        exact(evidence["usage"], {"input_tokens", "output_tokens", "total_tokens", "source"}, "Council native usage")
        usage = evidence["usage"]
        tokens = [usage[key] for key in ("input_tokens", "output_tokens", "total_tokens")]
        if (usage["source"] not in {"native_reported", "unavailable"}
                or any(value is not None and (type(value) is not int or value < 0) for value in tokens)
                or usage["source"] == "unavailable" and any(value is not None for value in tokens)
                or usage["source"] == "native_reported" and any(value is None for value in tokens)):
            raise ContractError("Council native token usage is invalid or invents missing observations")
    if not isinstance(evidence["checks"], list):
        raise ContractError("Council trusted checks must be an array")
    spec = {**snapshot["task"]["council"], "source_ids": [item["artifact_id"] for item in snapshot["council_brief"]["source_files"]]}
    if role in {"proposer_a", "proposer_b"}:
        validate_proposal(evidence["document"], spec)
        if evidence["checks"]:
            raise ContractError("Council proposers cannot supply trusted check results")
    elif role == "critic":
        validate_critique(evidence["document"], spec)
        documents = {**snapshot["council_state"]["documents"], "critic": evidence}
        verify_identities(documents, fixture=fixture)
        from .workflows import validate_check_results
        validate_check_results(evidence["checks"], snapshot["task"], snapshot["workspace"])
    elif role == "lead":
        validate_choice(evidence["document"], spec, snapshot["council_state"]["documents"]["critic"]["document"])
        if evidence["document"]["disposition"] == "accept":
            checks = snapshot["council_state"]["documents"]["critic"]["checks"]
            if any(c["required_to_pass"] and c["status"] != "passed" or c.get("integrity", {}).get("status") in {"violated", "not_run"} for c in checks):
                raise ContractError("Council lead cannot override failed mandatory checks/integrity")
    else:
        raise ContractError("Council attempt role is invalid")
    return evidence


def add_artifact(store, run_id: str, name: str, value: Any) -> dict:
    path, sha256, size = store.finalize_artifact(run_id, name, canonical_json(value).encode())
    return {"name": name, "path": path, "sha256": sha256, "byte_size": size}


def import_stage(store, run_id: str, attempt: dict, artifacts: list, metadata: dict, snapshot: dict, raw: bytes) -> str:
    verify_origin(store, run_id, snapshot)
    evidence = validate_document(decode(raw), snapshot, attempt)
    role = attempt["role"]
    name = f"council-{role}-{attempt['id']}.json"
    artifacts.append(add_artifact(store, run_id, name, evidence))
    if role == "lead":
        # The existing headless import/reopen fence remains the single lead authority.
        artifacts.append(add_artifact(store, run_id, f"lead-attempt-{attempt['id']}.json", evidence))
        return store.commit_headless_lead(run_id, attempt["attempt_token"], artifacts, metadata)
    updated = json.loads(canonical_json(snapshot))
    state = updated["council_state"]
    state["documents"][role] = evidence
    state.setdefault("artifacts", {})[role] = {"name": name, "sha256": artifacts[-1]["sha256"]}
    if role != "critic":
        state["next_role"] = "proposer_b" if role == "proposer_a" else "critic"
        return store.commit_council_stage(run_id, attempt["attempt_token"], artifacts, metadata, updated, role)
    # Save the critic projection before publishing a handoff in the same transaction.
    state["next_role"] = "lead"
    mapping = label_mapping(snapshot["task"]["council"]["seed"], ["proposer_a", "proposer_b"])
    checks = evidence["checks"]
    failures = [c["id"] for c in checks if c["required_to_pass"] and c["status"] != "passed"]
    integrity = [c["id"] for c in checks if c.get("integrity", {}).get("status") in {"violated", "not_run"}]
    packet = {"schema_version": 1, "workflow": "council-decision", "candidate_sha256": snapshot["workspace"]["candidate_sha256"],
              "base_oid": snapshot["base_oid"], "target_oid": snapshot["target_oid"], "brief_sha256": evidence["brief_sha256"],
              "proposals": sanitized_proposals(updated),
              "label_mapping": mapping, "critique": evidence["document"], "checks": checks,
              "evaluation": {"accept_allowed": not failures and not integrity, "required_failures": failures,
                             "integrity_failures": integrity},
              "artifacts": [], "instructions": "The sole lead must retain objections and validation; votes cannot override failed checks.",
              "identity_scope": "all_fixture" if snapshot["council_fixture"] is not None else "native_verified"}
    bundle = {"documents": state["documents"], "label_mapping": mapping, "brief_sha256": evidence["brief_sha256"]}
    bundled = add_artifact(store, run_id, f"council-evidence-{attempt['id']}.json", bundle)
    artifacts.append(bundled)
    packet["artifacts"] = [{"name": a["name"], "sha256": a["sha256"]} for a in (artifacts[-2], bundled)]
    return store.commit_durable_handoff(run_id, attempt["attempt_token"], artifacts, metadata, packet,
                                        mutable_snapshot=updated)


def reports(store, run_id: str, snapshot: dict | None, state: str, *, choice: dict | None = None, error: dict | None = None) -> dict[str, bytes]:
    from .reports import _contents_with_manifest, _event_export
    run = store.run(run_id)
    snapshot = snapshot or {}
    documents = snapshot.get("council_state", {}).get("documents", {})
    attempts = []
    for attempt in store.attempts_for_run(run_id):
        role = attempt["role"]
        saved = store.artifact_named(run_id, f"council-{role}-{attempt['id']}.json")
        evidence = None
        if saved is not None:
            raw = Path(saved["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != saved["sha256"]:
                raise ConflictError("Council attempt evidence is corrupt")
            evidence = decode(raw)
        ended = datetime.fromisoformat(attempt["finished_at"]) if attempt["finished_at"] else datetime.now(timezone.utc)
        latency = max(0, int((ended - datetime.fromisoformat(attempt["created_at"])).total_seconds() * 1000)) if attempt["pid"] is not None else None
        pending = attempt["status"] in {"running", "cancelling"} and state in {"failed", "cancelled"}
        metadata = json.loads(attempt["output_metadata"] or "{}")
        attempts.append({"id": attempt["id"], "role": role,
                         "status": state if pending else "failed" if metadata.get("failure") else attempt["status"],
                         "profile_id": attempt["profile_id"], "profile_index": attempt["profile_index"],
                         "requested_profile": selected_for(snapshot, role, attempt["profile_index"])["profile"] if "routing" in snapshot else None,
                         "observed_identity": evidence.get("observed_identity") if evidence else None,
                         "usage": evidence.get("usage") if evidence else None,
                         "native_ids": evidence.get("native_ids") if evidence else None, "native_model_requests": None,
                         "worker_invocations": 1 if attempt["pid"] is not None else 0,
                         "policy_sha256": snapshot.get("routing", {}).get("policy", {}).get("sha256"),
                         "runtime_package_sha256": run.get("package_digest"), "prompt_version": "council-role-packet-v1",
                         "prompt_sha256": evidence.get("prompt_sha256") if evidence else None,
                         "role_packet_sha256": evidence.get("role_packet_sha256") if evidence else None,
                         "output_schema_sha256": evidence.get("output_schema_sha256") if evidence else None,
                         "output_sha256": saved["sha256"] if saved else None,
                         "latency_ms": latency, "error": error if pending else metadata.get("failure")})
    artifacts = store.artifacts_for_run(run_id)
    receipt = {"schema_version": 1, "run_id": run_id, "workflow": "council-decision", "state": state,
               "candidate": {"sha256": snapshot.get("workspace", {}).get("candidate_sha256"),
                             "base_oid": snapshot.get("base_oid"), "target_oid": snapshot.get("target_oid")},
               "completed_at": datetime.now(timezone.utc).isoformat(), "goal": snapshot.get("task", {}).get("goal"),
               "candidate_sha256": snapshot.get("workspace", {}).get("candidate_sha256"),
               "brief_sha256": digest(snapshot["council_brief"]) if "council_brief" in snapshot else None,
               "attempts": attempts, "criteria": snapshot.get("task", {}).get("acceptance", []),
               "lead": {"disposition": choice.get("disposition") if choice else None, "choice": choice,
                        "work_source": "headless" if snapshot.get("task", {}).get("lead", {}).get("mode") == "headless" else "host",
                        "usage": None}, "dissent": documents.get("critic", {}).get("document", {}).get("objections", []),
               "documents": documents, "error": error, "automatic_enabled": False,
               "dispositions": [choice] if choice else [],
               "identity_scope": ("all_fixture" if snapshot.get("council_fixture") is not None else
                                  "native_verified" if len(documents) == 3 else "incomplete"),
               "worker_invocations": store.worker_invocations(run_id),
               "execution_elapsed_ms": store._execution_elapsed_ms(run_id, run, datetime.now(timezone.utc)),
               "limitations": ["Partial anonymity", "Fixture evidence establishes mechanics only"] if snapshot.get("council_fixture") is not None else ["Partial anonymity"]}
    events, _, _ = _event_export(store.events_for_run(run_id))
    projected = [{key: a[key] for key in ("id", "name", "sha256", "byte_size")} for a in artifacts]
    markdown = f"# DevSquad Council\n\nRun: {run_id}\nState: {state}\nAutomatic triggering: off\n\n" + canonical_json(choice or error or {}) + "\n\nDissent:\n" + canonical_json(receipt["dissent"]) + "\n"
    return _contents_with_manifest(run_id, receipt, markdown, events, projected)


def prepared_reports(store, run_id: str, snapshot: dict | None, state: str, **kwargs) -> list:
    result = []
    for name, content in reports(store, run_id, snapshot, state, **kwargs).items():
        path, sha256, size = store.finalize_artifact(run_id, name, content)
        result.append({"name": name, "path": path, "sha256": sha256, "byte_size": size})
    return result


def decision_gate(store, run_id: str, handoff, snapshot: dict, decision: dict) -> dict:
    from .workflows import validate_handoff_decision_evidence
    validate_saved_handoff(store, run_id, handoff, snapshot)
    validate_handoff_decision_evidence(decision, handoff.packet)
    choice = decision.get("council_choice")
    validate_choice(choice, snapshot["task"]["council"], handoff.packet["critique"])
    if choice["disposition"] != decision["disposition"] or choice["reason"] != decision["reason"]:
        raise ContractError("Council choice contradicts the lead disposition")
    if decision["disposition"] == "accept" and handoff.packet["evaluation"]["accept_allowed"] is not True:
        raise ContractError("Council acceptance is blocked by mandatory checks/integrity")
    if decision["disposition"] == "revise":
        raise ContractError("Council additional rounds require a new explicitly capped run")
    return choice


def finish_recorded(store, run_id: str, handoff, snapshot: dict, decision: dict) -> dict:
    choice = decision_gate(store, run_id, handoff, snapshot, decision)
    entry = store.recorded_handoff_submission(run_id, handoff.handoff_id)
    if entry is None or entry["decision"] != decision:
        raise ConflictError("Council recorded decision differs from continuation")
    if store.run(run_id)["state"] in {"succeeded", "failed"}:
        return {"run_id": run_id, "state": store.run(run_id)["state"], "replayed": True,
                "disposition": decision["disposition"], "launched": False}
    terminal = "succeeded" if decision["disposition"] == "accept" else "failed"
    artifacts = prepared_reports(store, run_id, snapshot, terminal, choice=choice)
    result = store.complete_handoff_terminal(run_id, handoff.handoff_id, decision["submission_id"],
                decision["submission_hash"], artifacts, terminal, {"disposition": decision["disposition"], "receipt": "result-receipt.json"})
    return {"run_id": run_id, **result, "disposition": decision["disposition"], "launched": False}


def complete(service, store, run_id: str, handoff, snapshot: dict, decision: dict) -> dict:
    claim = service._decode_claim(decision.pop("_claim"))
    decision_gate(store, run_id, handoff, snapshot, decision)
    submission = store.record_handoff_submission(run_id, claim, decision)
    result = finish_recorded(store, run_id, handoff, snapshot, decision)
    result["replayed"] = submission.replayed
    return result


def saved_claim(store, run_id: str, owner: str):
    """Reuse exactly our durable live claim, or reacquire only our expired one."""
    handoff = store.handoff_snapshot(run_id)
    prior = handoff.claim
    if prior is not None and prior.owner_id != owner:
        raise ConflictError("Council continuation cannot take another host's claim")
    if prior is not None and datetime.now(timezone.utc) < datetime.fromisoformat(prior.expires_at):
        return prior
    return store.claim_handoff(run_id, store.run(run_id)["version"], owner)


def continue_host_intent(service, store, run_id: str, handoff, snapshot: dict) -> dict:
    decision = store.terminal_finish_decision(run_id, handoff.handoff_id)
    if decision is None:
        raise ConflictError("Council host continuation has no exact current guided-finish claim marker")
    decision_gate(store, run_id, handoff, snapshot, decision)
    # Shared R6 authority reuses only the latest canonical guided marker. An
    # expired identical intent gets a fresh fence; same-name app claims do not.
    claim = store.claim_handoff(run_id, store.run(run_id)["version"], "terminal-operator",
                                initial_only=True, terminal_decision=decision)
    return complete(service, store, run_id, handoff, snapshot,
                    {**decision, "_claim": service._claim_payload(claim)})


def continue_lead(service, store, run_id: str, run: dict, handoff, snapshot: dict) -> dict:
    entry = store.recorded_handoff_submission(run_id, handoff.handoff_id)
    if entry is not None:
        return finish_recorded(store, run_id, handoff, snapshot, entry["decision"])
    if snapshot["task"]["lead"]["mode"] != "headless":
        return continue_host_intent(service, store, run_id, handoff, snapshot)
    verify_origin(store, run_id, snapshot)
    leads = [a for a in store.attempts_for_run(run_id) if a["role"] == "lead"]
    for attempt in reversed(leads):
        artifact = store.artifact_named(run_id, f"lead-attempt-{attempt['id']}.json")
        if artifact is None:
            continue
        raw = Path(artifact["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
            raise ConflictError("Council lead artifact is corrupt")
        evidence = validate_document(decode(raw), snapshot, attempt)
        choice = evidence["document"]
        claim = saved_claim(store, run_id, f"headless-lead:{attempt['id']}")
        body = {"schema_version": 1, "submission_id": f"headless-{attempt['id']}-{claim.fencing_token}",
                "disposition": choice["disposition"], "reason": choice["reason"], "council_choice": choice,
                "evidence_refs": [{"artifact_id": ref["artifact_id"], "sha256": ref["sha256"]} for ref in handoff.packet["artifacts"]]}
        return complete(service, store, run_id, handoff, snapshot,
                        {**body, "submission_hash": request_hash(body), "_claim": service._claim_payload(claim)})
    queued = store.queue_headless_lead(run_id, run["version"])
    if queued["action"] == "budget_exhausted":
        error = {"error": "BUDGET_EXHAUSTED", "message": "Council lead budget exhausted"}
        prepared = prepared_reports(store, run_id, snapshot, "failed", error=error)
        store.fail_queued_budget(run_id, run["version"], error, prepared)
        return {"run_id": run_id, "state": "failed", "launched": False}
    current = store.run(run_id)
    package, package_digest = service._verified_package(current)
    return {"run_id": run_id, "state": current["state"], "launched": True,
            "launch": (current["version"], package, package_digest)}
