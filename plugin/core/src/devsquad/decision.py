"""Optional typed decision-helper contracts with no routing authority expansion."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .contracts import ContractError
from .store import canonical_json


SHA256_LENGTH = 64
ROLES = {"implementer", "reviewer", "lead", "researcher"}
OFF_FIELDS = {"schema_version", "mode"}
ENABLED_FIELDS = {
    "schema_version", "mode", "purpose", "adapter", "language",
    "min_confidence", "gate_evidence_sha256", "budget",
}
REQUEST_FIELDS = {
    "schema_version", "purpose", "scope_sha256", "evidence",
    "candidate_catalog_sha256", "candidates", "pins", "adapter", "language",
    "truncation",
}
RESPONSE_FIELDS = {
    "schema_version", "request_sha256", "adapter", "language", "truncation",
    "recommendations", "usage", "elapsed_ms",
}


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ContractError(f"{label} fields are invalid")
    return value


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"decision helper {field} must be a non-empty string")
    return value


def _sha256(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if (not isinstance(value, str) or len(value) != SHA256_LENGTH
            or any(character not in "0123456789abcdef" for character in value)):
        raise ContractError(f"decision helper {field} must be a SHA256 digest")
    return value


def _finite_probability(value: Any, field: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0.0 <= float(value) <= 1.0):
        raise ContractError(f"decision helper {field} must be a finite probability")
    return float(value)


def _adapter(value: Any) -> dict[str, Any]:
    adapter = _exact(
        value, {"id", "model", "runtime_revision", "calibration_version"},
        "decision helper adapter",
    )
    for field in ("id", "model", "runtime_revision"):
        _identifier(adapter[field], f"adapter.{field}")
    calibration = adapter["calibration_version"]
    if calibration is not None:
        _identifier(calibration, "adapter.calibration_version")
    return dict(adapter)


def validate_decision_policy(value: dict[str, Any] | None) -> dict[str, Any]:
    """Validate the optional reviewed policy; omission is exactly mode off."""
    if value is None:
        return {"schema_version": 1, "mode": "off"}
    if not isinstance(value, dict):
        raise ContractError("decision helper policy must be an object")
    mode = value.get("mode")
    fields = OFF_FIELDS if mode == "off" else ENABLED_FIELDS
    _exact(value, fields, "decision helper policy")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("decision helper policy schema_version is invalid")
    if mode not in {"off", "shadow", "advisory"}:
        raise ContractError("decision helper mode is invalid")
    if mode == "off":
        return {"schema_version": 1, "mode": "off"}
    purpose = _exact(
        value["purpose"],
        {"id", "version", "question_sha256", "rubric_sha256"},
        "decision helper purpose",
    )
    _identifier(purpose["id"], "purpose.id")
    if type(purpose["version"]) is not int or purpose["version"] < 1:
        raise ContractError("decision helper purpose.version is invalid")
    _sha256(purpose["question_sha256"], "purpose.question_sha256")
    _sha256(purpose["rubric_sha256"], "purpose.rubric_sha256")
    adapter = _adapter(value["adapter"])
    language = _identifier(value["language"], "language")
    minimum = _finite_probability(value["min_confidence"], "min_confidence")
    gate = _sha256(
        value["gate_evidence_sha256"], "gate_evidence_sha256", nullable=True,
    )
    if mode == "advisory" and gate is None:
        raise ContractError("advisory mode requires reviewed gate evidence")
    if mode == "shadow" and gate is not None:
        raise ContractError("shadow mode cannot claim advisory gate evidence")
    budget = _exact(
        value["budget"],
        {"max_calls", "max_input_bytes", "wall_seconds", "max_cost_usd"},
        "decision helper budget",
    )
    for field in ("max_calls", "max_input_bytes", "wall_seconds"):
        if type(budget[field]) is not int or budget[field] < 1:
            raise ContractError(f"decision helper budget.{field} is invalid")
    if budget["max_calls"] != 1:
        raise ContractError("decision helper v1 permits exactly one call")
    cost = budget["max_cost_usd"]
    if (isinstance(cost, bool) or not isinstance(cost, (int, float))
            or not math.isfinite(cost) or cost < 0):
        raise ContractError("decision helper budget.max_cost_usd is invalid")
    return json.loads(canonical_json({
        **value,
        "purpose": dict(purpose),
        "adapter": adapter,
        "language": language,
        "min_confidence": minimum,
        "gate_evidence_sha256": gate,
        "budget": dict(budget),
    }))


def build_decision_request(
    task: dict[str, Any],
    routing: dict[str, Any],
    policy: dict[str, Any],
    evidence_payload: bytes | str,
    *,
    truncated: bool = False,
) -> dict[str, Any] | None:
    """Build a cache-complete request containing hashes, never raw task text."""
    config = validate_decision_policy(policy.get("decision_helper"))
    if config["mode"] == "off":
        return None
    if isinstance(evidence_payload, str):
        evidence = evidence_payload.encode()
    elif isinstance(evidence_payload, bytes):
        evidence = evidence_payload
    else:
        raise ContractError("decision helper evidence payload must be bytes or text")
    if len(evidence) > config["budget"]["max_input_bytes"] and not truncated:
        raise ContractError("decision helper input exceeds its frozen byte budget")
    candidates = {}
    pins = {}
    for role, routed in routing["roles"].items():
        ordered = [routed["selected"], *routed.get("fallbacks", [])]
        identifiers = [candidate["profile_id"] for candidate in ordered]
        if len(identifiers) != len(set(identifiers)):
            raise ContractError("decision helper candidates must be unique")
        candidates[role] = identifiers
        if routed["source"] == "override":
            pins[role] = routed["selected"]["profile_id"]
    request = {
        "schema_version": 1,
        "purpose": config["purpose"],
        "scope_sha256": hashlib.sha256(
            canonical_json(task["scope"]).encode(),
        ).hexdigest(),
        "evidence": {
            "sha256": hashlib.sha256(evidence).hexdigest(),
            "byte_size": len(evidence),
        },
        "candidate_catalog_sha256": routing["profile_registry"]["sha256"],
        "candidates": candidates,
        "pins": pins,
        "adapter": config["adapter"],
        "language": config["language"],
        "truncation": {
            "occurred": bool(truncated),
            "limit_bytes": config["budget"]["max_input_bytes"],
        },
    }
    return validate_decision_request(request)


def validate_decision_request(value: dict[str, Any]) -> dict[str, Any]:
    _exact(value, REQUEST_FIELDS, "decision helper request")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("decision helper request schema_version is invalid")
    purpose = _exact(
        value["purpose"],
        {"id", "version", "question_sha256", "rubric_sha256"},
        "decision helper request purpose",
    )
    _identifier(purpose["id"], "request purpose.id")
    if type(purpose["version"]) is not int or purpose["version"] < 1:
        raise ContractError("decision helper request purpose.version is invalid")
    _sha256(purpose["question_sha256"], "request purpose.question_sha256")
    _sha256(purpose["rubric_sha256"], "request purpose.rubric_sha256")
    _sha256(value["scope_sha256"], "request scope_sha256")
    _sha256(value["candidate_catalog_sha256"], "request candidate_catalog_sha256")
    evidence = _exact(
        value["evidence"], {"sha256", "byte_size"}, "decision helper evidence",
    )
    _sha256(evidence["sha256"], "request evidence.sha256")
    if type(evidence["byte_size"]) is not int or evidence["byte_size"] < 0:
        raise ContractError("decision helper evidence.byte_size is invalid")
    candidates = value["candidates"]
    if (not isinstance(candidates, dict) or not candidates
            or set(candidates) - ROLES):
        raise ContractError("decision helper candidates are invalid")
    normalized_candidates = {}
    for role, identifiers in candidates.items():
        if (not isinstance(identifiers, list) or not identifiers
                or len(identifiers) != len(set(identifiers))
                or any(not isinstance(item, str) or not item for item in identifiers)):
            raise ContractError("decision helper candidate IDs are invalid")
        normalized_candidates[role] = list(identifiers)
    pins = value["pins"]
    if not isinstance(pins, dict) or set(pins) - set(normalized_candidates):
        raise ContractError("decision helper pins are invalid")
    for role, profile_id in pins.items():
        if profile_id not in normalized_candidates[role]:
            raise ContractError("decision helper pin is outside eligible candidates")
    truncation = _exact(
        value["truncation"], {"occurred", "limit_bytes"},
        "decision helper request truncation",
    )
    if type(truncation["occurred"]) is not bool:
        raise ContractError("decision helper truncation flag is invalid")
    if type(truncation["limit_bytes"]) is not int or truncation["limit_bytes"] < 1:
        raise ContractError("decision helper truncation limit is invalid")
    return json.loads(canonical_json({
        **value,
        "purpose": dict(purpose),
        "evidence": dict(evidence),
        "candidates": normalized_candidates,
        "pins": dict(pins),
        "adapter": _adapter(value["adapter"]),
        "language": _identifier(value["language"], "request language"),
        "truncation": dict(truncation),
    }))


def decision_cache_key(request: dict[str, Any]) -> str:
    normalized = validate_decision_request(request)
    return hashlib.sha256(canonical_json(normalized).encode()).hexdigest()


def validate_decision_response(
    request: dict[str, Any],
    response: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Validate IDs, distributions, usage and provider identity strictly."""
    request = validate_decision_request(request)
    config = validate_decision_policy(config)
    _exact(response, RESPONSE_FIELDS, "decision helper response")
    if type(response["schema_version"]) is not int or response["schema_version"] != 1:
        raise ContractError("decision helper response schema_version is invalid")
    expected_sha = hashlib.sha256(canonical_json(request).encode()).hexdigest()
    if response["request_sha256"] != expected_sha:
        raise ContractError("decision helper response does not bind the request")
    adapter = _adapter(response["adapter"])
    if adapter != request["adapter"] or adapter != config["adapter"]:
        raise ContractError("decision helper observed adapter identity drifted")
    language = _identifier(response["language"], "response language")
    if language != request["language"]:
        raise ContractError("decision helper response language drifted")
    truncation = _exact(
        response["truncation"], {"occurred", "detail"},
        "decision helper response truncation",
    )
    if type(truncation["occurred"]) is not bool:
        raise ContractError("decision helper response truncation flag is invalid")
    if truncation["detail"] is not None:
        _identifier(truncation["detail"], "response truncation.detail")
    recommendations = response["recommendations"]
    if not isinstance(recommendations, dict) or set(recommendations) != set(request["candidates"]):
        raise ContractError("decision helper recommendation roles are invalid")
    normalized_recommendations = {}
    for role, candidate_ids in request["candidates"].items():
        item = _exact(
            recommendations[role],
            {"ranking", "probabilities", "confidence", "abstain_reason"},
            "decision helper recommendation",
        )
        ranking = item["ranking"]
        if (not isinstance(ranking, list) or len(ranking) != len(candidate_ids)
                or len(ranking) != len(set(ranking))
                or set(ranking) != set(candidate_ids)):
            raise ContractError("decision helper ranking changes the eligible set")
        probabilities = item["probabilities"]
        if not isinstance(probabilities, dict) or set(probabilities) != set(candidate_ids):
            raise ContractError("decision helper probability IDs are invalid")
        normalized_probabilities = {
            profile_id: _finite_probability(
                probabilities[profile_id], f"probability.{profile_id}",
            )
            for profile_id in candidate_ids
        }
        if not math.isclose(
                sum(normalized_probabilities.values()), 1.0, abs_tol=0.02):
            raise ContractError("decision helper probabilities do not sum to one")
        confidence = _finite_probability(item["confidence"], "confidence")
        abstain = item["abstain_reason"]
        if abstain is not None:
            abstain = _identifier(abstain, "abstain_reason")
        normalized_recommendations[role] = {
            "ranking": list(ranking),
            "probabilities": normalized_probabilities,
            "confidence": confidence,
            "abstain_reason": abstain,
        }
    usage = _exact(
        response["usage"],
        {"source", "billable_requests", "input_tokens", "output_tokens", "cost_usd"},
        "decision helper usage",
    )
    if usage["source"] not in {"native_reported", "fake", "unavailable"}:
        raise ContractError("decision helper usage source is invalid")
    if (type(usage["billable_requests"]) is not int
            or not 0 <= usage["billable_requests"] <= config["budget"]["max_calls"]):
        raise ContractError("decision helper billable request count is invalid")
    for field in ("input_tokens", "output_tokens"):
        if usage[field] is not None and (
                type(usage[field]) is not int or usage[field] < 0):
            raise ContractError(f"decision helper usage {field} is invalid")
    cost = usage["cost_usd"]
    if cost is not None and (
            isinstance(cost, bool) or not isinstance(cost, (int, float))
            or not math.isfinite(cost) or cost < 0
            or cost > config["budget"]["max_cost_usd"]):
        raise ContractError("decision helper observed cost exceeds its budget")
    if usage["source"] == "unavailable" and any(
            usage[field] is not None
            for field in ("input_tokens", "output_tokens", "cost_usd")):
        raise ContractError("decision helper unavailable usage cannot invent values")
    if type(response["elapsed_ms"]) is not int or response["elapsed_ms"] < 0:
        raise ContractError("decision helper elapsed_ms is invalid")
    return json.loads(canonical_json({
        **response,
        "adapter": adapter,
        "language": language,
        "truncation": dict(truncation),
        "recommendations": normalized_recommendations,
        "usage": dict(usage),
    }))


def apply_decision_response(
    routing: dict[str, Any],
    request: dict[str, Any],
    response: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Apply advisory ordering only inside the already-frozen eligible set."""
    config = validate_decision_policy(config)
    if config["mode"] == "off":
        return json.loads(canonical_json(routing))
    request = validate_decision_request(request)
    response = validate_decision_response(request, response, config)
    result = json.loads(canonical_json(routing))
    applied_roles = []
    role_status = {}
    too_late = response["elapsed_ms"] > config["budget"]["wall_seconds"] * 1000
    truncated = request["truncation"]["occurred"] or response["truncation"]["occurred"]
    for role, recommendation in response["recommendations"].items():
        routed = result["roles"][role]
        reason = None
        if config["mode"] == "shadow":
            reason = "shadow_mode"
        elif routed["source"] == "override" or role in request["pins"]:
            reason = "pinned_route"
        elif truncated:
            reason = "truncated"
        elif too_late:
            reason = "late"
        elif recommendation["abstain_reason"] is not None:
            reason = "abstained"
        elif recommendation["confidence"] < config["min_confidence"]:
            reason = "below_confidence_gate"
        if reason is not None:
            role_status[role] = reason
            continue
        candidates = {
            item["profile_id"]: item
            for item in [routed["selected"], *routed.get("fallbacks", [])]
        }
        ordered = [candidates[profile_id] for profile_id in recommendation["ranking"]]
        routed["selected"] = ordered[0]
        routed["fallbacks"] = ordered[1:]
        applied_roles.append(role)
        role_status[role] = "applied"
    result["decision_helper"] = {
        "schema_version": 1,
        "mode": config["mode"],
        "request_sha256": response["request_sha256"],
        "response_sha256": hashlib.sha256(
            canonical_json(response).encode(),
        ).hexdigest(),
        "applied_roles": sorted(applied_roles),
        "role_status": role_status,
        "usage": response["usage"],
        "elapsed_ms": response["elapsed_ms"],
    }
    return result


def decision_fallback(
    routing: dict[str, Any], mode: str, status: str, request_sha256: str | None,
) -> dict[str, Any]:
    """Record an unusable helper result without changing deterministic routing."""
    if mode == "off":
        return json.loads(canonical_json(routing))
    if mode not in {"shadow", "advisory"}:
        raise ContractError("decision helper fallback mode is invalid")
    status = _identifier(status, "fallback status")
    if request_sha256 is not None:
        _sha256(request_sha256, "fallback request_sha256")
    result = json.loads(canonical_json(routing))
    result["decision_helper"] = {
        "schema_version": 1,
        "mode": mode,
        "request_sha256": request_sha256,
        "response_sha256": None,
        "applied_roles": [],
        "role_status": {
            role: status for role in sorted(result["roles"])
        },
        "usage": {
            "source": "unavailable", "billable_requests": 0,
            "input_tokens": None, "output_tokens": None, "cost_usd": None,
        },
        "elapsed_ms": None,
    }
    return result
