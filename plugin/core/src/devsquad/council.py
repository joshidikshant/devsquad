"""Strict contracts for the explicitly invoked, read-only Council workflow."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .contracts import ContractError
from .store import canonical_json

ROLES = ("proposer_a", "proposer_b", "critic")
MAX_PACKET_BYTES = 512 * 1024
PROMPT_VERSION = "council-role-packet-v1"


def role_prompt(role: str, packet: dict) -> str:
    return (f"You are the single read-only Council {role}. Read evidence.json in your directory. "
            "Use the frozen rubric and cite only supplied evidence IDs. Produce the requested structured document. "
            "No peer research or implementation writes. The lead retains every objection ID and required validation. "
            "Preference cannot override a failed mandatory check.\n" + canonical_json(packet))


def prompt_digest(role: str, packet: dict) -> str:
    return hashlib.sha256(role_prompt(role, packet).encode()).hexdigest()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def exact(value: Any, fields: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise ContractError(f"{label} fields are invalid")
    return value


def text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 16000:
        raise ContractError(f"{label} must be bounded non-empty text")
    return value


def sha(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ContractError("Council hash is invalid")
    return value


def validate_spec(spec: Any) -> dict:
    exact(spec, {"schema_version", "enabled", "automatic", "reason", "min_valid_proposals",
                 "required_critics", "max_invocations", "seed", "evidence", "rubric"}, "CouncilSpec")
    if type(spec["schema_version"]) is not int or spec["schema_version"] != 1:
        raise ContractError("CouncilSpec version is invalid")
    if spec["enabled"] is not True or spec["automatic"] is not False:
        raise ContractError("Council requires explicit invocation; automatic Council is disabled")
    if type(spec["min_valid_proposals"]) is not int or spec["min_valid_proposals"] != 2:
        raise ContractError("Council requires two valid proposals")
    if type(spec["required_critics"]) is not int or spec["required_critics"] != 1:
        raise ContractError("Council requires one distinct critic")
    if type(spec["max_invocations"]) is not int or not 3 <= spec["max_invocations"] <= 16:
        raise ContractError("Council invocation cap must be between 3 and 16")
    text(spec["reason"], "Council reason")
    sha(spec["seed"])
    if not isinstance(spec["evidence"], list) or len(spec["evidence"]) > 32:
        raise ContractError("Council evidence must be a bounded array")
    identifiers = set()
    for item in spec["evidence"]:
        exact(item, {"artifact_id", "sha256"}, "Council evidence reference")
        text(item["artifact_id"], "artifact_id")
        sha(item["sha256"])
        if item["artifact_id"] in identifiers:
            raise ContractError("Council evidence IDs must be unique")
        identifiers.add(item["artifact_id"])
    if not isinstance(spec["rubric"], list) or not 1 <= len(spec["rubric"]) <= 32:
        raise ContractError("Council rubric must be a non-empty bounded array")
    identifiers = set()
    for item in spec["rubric"]:
        exact(item, {"id", "description"}, "Council rubric item")
        text(item["id"], "rubric id")
        text(item["description"], "rubric description")
        if item["id"] in identifiers:
            raise ContractError("Council rubric IDs must be unique")
        identifiers.add(item["id"])
    return spec


def label_mapping(seed: str, authors: list[str]) -> dict[str, str]:
    sha(seed)
    if len(authors) != 2 or len(set(authors)) != 2:
        raise ContractError("Council label mapping requires two distinct authors")
    ordered = sorted(authors, key=lambda author: hashlib.sha256((seed + ":" + author).encode()).hexdigest())
    return dict(zip(("A", "B"), ordered))


def evidence_ids(value: Any, spec: dict) -> list[str]:
    allowed = {ref["artifact_id"] for ref in spec["evidence"]} | set(spec.get("source_ids", []))
    if (not isinstance(value, list) or not all(isinstance(v, str) and v in allowed for v in value)
            or len(value) != len(set(value))):
        raise ContractError("Council evidence IDs are unknown or duplicated")
    return value


def sanitized_proposals(snapshot: dict) -> dict:
    """Strip supplied identity tokens from text; raw originals remain sealed."""
    import re
    mapping = label_mapping(snapshot["task"]["council"]["seed"], ["proposer_a", "proposer_b"])
    documents = snapshot["council_state"]["documents"]
    sensitive = set()
    for role in ("proposer_a", "proposer_b"):
        evidence = documents[role]
        profile = evidence["profile"]
        sensitive.update([role, profile["id"], profile["model_id"], profile["account_pool_id"]])
        sensitive.update(str(value) for value in (evidence.get("observed_identity") or {}).values()
                         if isinstance(value, str) and len(value) > 3)
    def sanitize(value, key=None):
        if key == "evidence_ids":
            return value  # Frozen provenance IDs must not be rewritten.
        if isinstance(value, str):
            for token in sorted(sensitive, key=len, reverse=True):
                value = re.sub(re.escape(token), "[identity omitted]", value, flags=re.IGNORECASE)
            return value
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        if isinstance(value, dict):
            return {field: sanitize(item, field) for field, item in value.items()}
        return value
    return {label: sanitize(documents[author]["document"]) for label, author in mapping.items()}


def validate_proposal(value: Any, spec: dict) -> dict:
    exact(value, {"summary", "approach", "claims", "validation"}, "Council proposal")
    for field in ("summary", "approach", "validation"):
        text(value[field], field)
    if not isinstance(value["claims"], list) or not 1 <= len(value["claims"]) <= 64:
        raise ContractError("Council proposal claims must be non-empty and bounded")
    for claim in value["claims"]:
        exact(claim, {"text", "evidence_ids"}, "Council claim")
        text(claim["text"], "claim text")
        evidence_ids(claim["evidence_ids"], spec)
    return value


def validate_critique(value: Any, spec: dict) -> dict:
    exact(value, {"summary", "assessments", "objections"}, "Council critique")
    text(value["summary"], "critique summary")
    required = {(label, criterion["id"]) for label in ("A", "B") for criterion in spec["rubric"]}
    seen = set()
    if not isinstance(value["assessments"], list) or len(value["assessments"]) != len(required):
        raise ContractError("Council critique must assess every label and rubric criterion")
    for item in value["assessments"]:
        exact(item, {"label", "criterion_id", "status", "reason", "evidence_ids"}, "Council assessment")
        pair = (item["label"], item["criterion_id"])
        if pair not in required or pair in seen or item["status"] not in {"supported", "unsupported", "uncertain"}:
            raise ContractError("Council assessment label/criterion/status is invalid or duplicated")
        seen.add(pair)
        text(item["reason"], "assessment reason")
        evidence_ids(item["evidence_ids"], spec)
    if not isinstance(value["objections"], list) or len(value["objections"]) > 64:
        raise ContractError("Council objections must be bounded")
    seen = set()
    for item in value["objections"]:
        exact(item, {"id", "label", "reason", "evidence_ids"}, "Council objection")
        if item["label"] not in {"A", "B"} or item["id"] in seen:
            raise ContractError("Council objection label/ID is invalid")
        seen.add(text(item["id"], "objection id"))
        text(item["reason"], "objection reason")
        evidence_ids(item["evidence_ids"], spec)
    return value


def validate_choice(value: Any, spec: dict, critique: dict) -> dict:
    exact(value, {"disposition", "reason", "chosen", "supported_claims", "discarded_alternatives",
                  "unresolved_objections", "validation"}, "Council decision")
    if value["disposition"] not in {"accept", "reject", "revise"} or value["chosen"] not in {"A", "B", "synthesis"}:
        raise ContractError("Council disposition/chosen label is invalid")
    for field in ("reason", "validation"):
        text(value[field], field)
    for field in ("supported_claims", "discarded_alternatives", "unresolved_objections"):
        if not isinstance(value[field], list) or len(value[field]) > 64 or not all(isinstance(v, str) and v for v in value[field]):
            raise ContractError(f"Council decision {field} must be a bounded text array")
        for entry in value[field]:
            text(entry, f"Council decision {field} entry")
    objections = {item["id"] for item in critique["objections"]}
    # Retain every objection; disposition does not silently erase a dissenting source.
    if set(value["unresolved_objections"]) != objections or len(value["unresolved_objections"]) != len(objections):
        raise ContractError("Council decision must retain every recorded objection ID")
    if value["disposition"] == "accept" and not value["supported_claims"]:
        raise ContractError("Council acceptance needs supported claims")
    return value


def output_schema(role: str) -> dict:
    string = {"type": "string", "minLength": 1}
    array = {"type": "array", "items": string}
    def obj(properties):
        return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}
    if role in {"proposer_a", "proposer_b"}:
        return obj({"summary": string, "approach": string, "claims": {"type": "array", "minItems": 1,
                    "items": obj({"text": string, "evidence_ids": array})}, "validation": string})
    if role == "critic":
        return obj({"summary": string, "assessments": {"type": "array", "items": obj({
                    "label": {"enum": ["A", "B"]}, "criterion_id": string,
                    "status": {"enum": ["supported", "unsupported", "uncertain"]}, "reason": string,
                    "evidence_ids": array})}, "objections": {"type": "array", "items": obj({
                    "id": string, "label": {"enum": ["A", "B"]}, "reason": string, "evidence_ids": array})}})
    if role == "lead":
        return obj({"disposition": {"enum": ["accept", "reject", "revise"]}, "reason": string,
                    "chosen": {"enum": ["A", "B", "synthesis"]}, "supported_claims": array,
                    "discarded_alternatives": array, "unresolved_objections": array, "validation": string})
    raise ContractError("Council role is invalid")


def verify_identities(documents: dict[str, dict], *, fixture: bool) -> None:
    identities = []
    native_ids = set()
    for role in ROLES:
        value = documents.get(role)
        if not isinstance(value, dict):
            raise ContractError(f"Council is missing {role}; no valid quorum")
        identity = value.get("observed_identity")
        if fixture:
            if identity is not None or value.get("identity_scope") != "all_fixture":
                raise ContractError("Council mixed fixture/native identities cannot establish quorum")
            identities.append(value["profile"]["model_id"].casefold())
        else:
            if (not isinstance(identity, dict) or identity.get("verification") != "verified"
                    or not isinstance(identity.get("model_id"), str) or not identity["model_id"]):
                raise ContractError("Council requires verified observed model identities")
            identities.append(identity["model_id"].casefold())
            ids = value.get("native_ids", {})
            pair = (ids.get("thread_id"), ids.get("turn_id"))
            if not all(isinstance(item, str) and item for item in pair) or pair in native_ids:
                raise ContractError("Council native role turns must be distinct correlated thread/turn identities")
            native_ids.add(pair)
    if len(set(identities)) != 3:
        raise ContractError("Council critic/proposers must have three distinct model identities")


def decode(value: bytes | str) -> dict:
    from .workflows import _strict_json_object
    result = _strict_json_object(value, "Council evidence", maximum=MAX_PACKET_BYTES)
    return result
