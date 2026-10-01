"""Bounded Claude result evidence; requested settings are never observations.

The tested CLI result attributes usage to model IDs, not individual writer
messages. Only a single concrete reported model can establish writer identity.
Pricing aliases are metadata. Effective effort and serving revision are unknown.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from .contracts import ContractError


MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_COUNTER = 2 ** 63 - 1
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,499}\Z")
_COUNTERS = {
    "inputTokens", "outputTokens", "cacheReadInputTokens",
    "cacheCreationInputTokens", "webSearchRequests", "contextWindow",
    "maxOutputTokens",
}
_ALIASES = {"sonnet", "opus", "haiku"}
_NATIVE_FIELDS = {
    "schema_version", "type", "subtype", "is_error", "session_id",
    "model_usage", "top_level_model", "usage",
}
ERROR_CODES = {"AUTH_ERROR", "RATE_LIMITED", "TIMEOUT", "CLI_ERROR"}


def _exact(value: Any, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ContractError("Claude evidence fields are invalid")
    return value


def _model(value: Any) -> str:
    if not isinstance(value, str) or not _MODEL.fullmatch(value):
        raise ContractError("Claude reported model is invalid")
    return value


def _session(value: Any) -> str:
    if not isinstance(value, str) or not _SESSION.fullmatch(value):
        raise ContractError("Claude result session is invalid")
    return value


def _counter(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= MAX_COUNTER:
        raise ContractError("Claude reported counter is invalid")
    return value


def strict_json(payload: bytes | str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError("non-finite number")

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("non-finite number")
        return result

    try:
        encoded = payload.encode("utf-8") if isinstance(payload, str) else payload
        if len(encoded) > MAX_OUTPUT_BYTES:
            raise ValueError("output limit")
        return json.loads(encoded.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=nonfinite, parse_float=finite_float)
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise ContractError("Claude result is not bounded strict UTF-8 JSON") from exc


def unknown_usage() -> dict[str, Any]:
    return {"input_tokens": None, "output_tokens": None,
            "total_tokens": None, "source": "unavailable"}


def reported_usage(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return unknown_usage()
    try:
        incoming = _counter(raw.get("input_tokens"))
        outgoing = _counter(raw.get("output_tokens"))
        total = _counter(incoming + outgoing)
    except ContractError:
        return unknown_usage()
    return {"input_tokens": incoming, "output_tokens": outgoing,
            "total_tokens": total, "source": "native_reported"}


def _usage_evidence(value: Any) -> dict[str, Any]:
    _exact(value, set(unknown_usage()))
    expected = reported_usage(value)
    if expected != value:
        raise ContractError("Claude usage evidence is inconsistent")
    return expected


def model_usage(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not 1 <= len(raw) <= 32:
        raise ContractError("Claude result has no bounded model usage")
    result = {}
    for model, entry in raw.items():
        _model(model)
        if not isinstance(entry, dict):
            raise ContractError("Claude model usage is invalid")
        normalized = {key: _counter(entry[key]) for key in
                      ("inputTokens", "outputTokens") if key in entry}
        if len(normalized) != 2:
            raise ContractError("Claude model usage counters are missing")
        for key in _COUNTERS & entry.keys():
            normalized[key] = _counter(entry[key])
        if "costUSD" in entry:
            cost = entry["costUSD"]
            if (type(cost) not in (int, float) or not 0 <= cost <= MAX_COUNTER
                    or not math.isfinite(cost)):
                raise ContractError("Claude model cost is invalid")
            normalized["costUSD"] = cost
        for key in ("canonicalModel", "provider"):
            if key in entry:
                normalized[key] = _model(entry[key])
        result[model] = normalized
    return result


def native_result(document: Any) -> tuple[str, dict[str, Any]]:
    if (not isinstance(document, dict) or document.get("type") != "result"
            or document.get("subtype") != "success"
            or document.get("is_error") is not False
            or not isinstance(document.get("result"), str)
            or not document["result"].strip()):
        raise ContractError("Claude implementation has no successful result")
    summary = document["result"].strip()
    if len(summary) > 20_000:
        raise ContractError("Claude summary exceeds its limit")
    return summary, {
        "schema_version": 1, "type": "result", "subtype": "success",
        "is_error": False, "session_id": _session(document.get("session_id")),
        "model_usage": model_usage(document.get("modelUsage")),
        "top_level_model": _model(document["model"]) if "model" in document else None,
        "usage": reported_usage(document.get("usage")),
    }


def observed_identity(native: Any, adapter: dict[str, Any],
                      profile: dict[str, Any]) -> dict[str, Any]:
    _exact(native, _NATIVE_FIELDS)
    if (type(native["schema_version"]) is not int or native["schema_version"] != 1
            or native["type"] != "result" or native["subtype"] != "success"
            or native["is_error"] is not False):
        raise ContractError("Claude native identity envelope is invalid")
    _session(native["session_id"])
    _usage_evidence(native["usage"])
    models = model_usage(native["model_usage"])
    if models != native["model_usage"] or len(models) != 1:
        raise ContractError("Claude result cannot identify a unique writer model")
    model = next(iter(models))
    if model in _ALIASES:
        raise ContractError("Claude reported identity is an unresolved alias")
    requested = _model(profile.get("model_id"))
    alias = requested in _ALIASES
    if (alias and not model.startswith(f"claude-{requested}-")) or (
            not alias and requested != model):
        raise ContractError("Claude reported model does not match the requested profile")
    top = native["top_level_model"]
    if top is not None and _model(top) != model:
        raise ContractError("Claude result contains contradictory model identity")
    if (adapter.get("harness") != "claude"
            or adapter.get("model_provider") != "anthropic"
            or not isinstance(adapter.get("harness_version"), str)
            or not adapter["harness_version"]
            or profile.get("harness") != "claude"
            or profile.get("permission_policy") != "workspace_write"):
        raise ContractError("Claude identity does not match the frozen adapter")
    provider = models[model].get("provider")
    if provider is not None and provider != adapter["model_provider"]:
        raise ContractError("Claude reported provider contradicts the frozen adapter")
    return {
        "harness": "claude", "harness_version": adapter["harness_version"],
        "model_provider": adapter["model_provider"], "model_id": model,
        "effort": None, "backing_revision": None,
        "permission_policy": "workspace_write", "verification": "verified",
        "verification_scope": "reported_model",
        "model_source": "claude.result.modelUsage",
        "alias_resolution": {"requested": requested, "reported": model} if alias else None,
        "native_evidence": native,
    }


def validate_observation(value: Any, adapter: dict[str, Any],
                         profile: dict[str, Any], ids: Any, usage: Any) -> None:
    if not isinstance(value, dict):
        raise ContractError("Claude observed identity is missing")
    native = value.get("native_evidence")
    expected = observed_identity(native, adapter, profile)
    # Canonical JSON comparison distinguishes booleans from integers.
    if json.dumps(value, sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise ContractError("Claude observed identity differs from native evidence")
    if ids != {"session_id": native["session_id"]} or usage != native["usage"]:
        raise ContractError("Claude session or usage differs from native evidence")


def failure_diagnostics(payload: bytes | str, document: Any, reason: str) -> dict[str, Any]:
    """Keep only bounded typed native fields, never provider prose or stderr."""
    document = document if isinstance(document, dict) else {}
    session = document.get("session_id")
    try:
        _session(session)
    except ContractError:
        session = None
    models = {}
    raw = document.get("modelUsage")
    if isinstance(raw, dict) and len(raw) <= 32:
        for key, value in raw.items():
            try:
                models.update(model_usage({key: value}))
            except (ContractError, OverflowError):
                continue
    encoded = payload.encode("utf-8") if isinstance(payload, str) else payload
    return {
        "schema_version": 1, "identity_status": "unverified", "reason": reason,
        "output_sha256": hashlib.sha256(encoded).hexdigest(),
        "output_bytes": len(encoded), "session_id": session,
        "model_usage": models, "usage": reported_usage(document.get("usage")),
    }


class ClaudeResultError(ContractError):
    def __init__(self, code: str, diagnostics: dict[str, Any]):
        super().__init__(f"{code}: Claude implementation failed")
        self.code = code
        self.diagnostics = diagnostics


def failure_envelope(error: ClaudeResultError, profile_sha256: str) -> dict[str, Any]:
    return {"schema_version": 1, "type": "claude_implementation_failure",
            "profile_sha256": profile_sha256, "error": error.code,
            "native_diagnostics": error.diagnostics}


def validate_failure(value: Any, profile_sha256: str) -> dict[str, Any]:
    _exact(value, {"schema_version", "type", "profile_sha256", "error",
                   "native_diagnostics"})
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["type"] != "claude_implementation_failure"
            or value["profile_sha256"] != profile_sha256
            or not isinstance(value["error"], str)
            or value["error"] not in ERROR_CODES):
        raise ContractError("Claude failed attempt identity is invalid")
    data = _exact(value["native_diagnostics"], {
        "schema_version", "identity_status", "reason", "output_sha256",
        "output_bytes", "session_id", "model_usage", "usage",
    })
    if (type(data["schema_version"]) is not int or data["schema_version"] != 1
            or data["identity_status"] != "unverified"
            or not isinstance(data["reason"], str)
            or data["reason"] not in {"native_result_invalid", "execution_failed", "output_limit"}
            or not isinstance(data["output_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", data["output_sha256"])):
        raise ContractError("Claude failure diagnostics are invalid")
    _counter(data["output_bytes"])
    if data["session_id"] is not None:
        _session(data["session_id"])
    if data["model_usage"] != {}:
        if model_usage(data["model_usage"]) != data["model_usage"]:
            raise ContractError("Claude failed model usage is invalid")
    _usage_evidence(data["usage"])
    return value
