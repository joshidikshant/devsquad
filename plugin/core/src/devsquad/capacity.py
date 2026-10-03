"""Strict shared-capacity observations and deterministic availability derivation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from typing import Any

from .contracts import ContractError
from .store import canonical_json


OBSERVATION_FIELDS = {
    "schema_version",
    "observation_id",
    "pool_id",
    "window_id",
    "applies_to",
    "observed_at",
    "expires_at",
    "source",
    "used",
    "limit",
    "unit",
    "resets_at",
    "confidence",
}
APPLIES_TO_FIELDS = {"harnesses", "model_families", "model_ids"}
SOURCES = {"native_reported", "manual_reported", "estimated"}
UNITS = {"percent", "requests", "tokens", "provider-native-string"}
CONFIDENCE = {"confirmed", "reported", "estimated"}
CAPACITY_STATES = {"available", "exhausted", "unknown"}
MAX_CLOCK_SKEW = timedelta(minutes=5)


def _authoritative_now(value: datetime | None) -> datetime:
    current = datetime.now(timezone.utc) if value is None else value
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise ContractError("capacity evaluation time must include a timezone")
    return current.astimezone(timezone.utc)


def _timestamp(value: Any, field: str, *, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        suffix = " or null" if nullable else ""
        raise ContractError(f"capacity {field} must be an ISO timestamp{suffix}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"capacity {field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"capacity {field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"capacity {field} must be a non-empty string")
    return value


def _measurement(value: Any, field: str) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"capacity {field} must be a finite non-negative number or null")
    if not math.isfinite(value) or value < 0:
        raise ContractError(f"capacity {field} must be a finite non-negative number or null")
    return value


def _selector(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict) or set(value) != APPLIES_TO_FIELDS:
        raise ContractError("capacity applies_to fields are invalid")
    normalized: dict[str, list[str]] = {}
    for field in sorted(APPLIES_TO_FIELDS):
        entries = value[field]
        if not isinstance(entries, list):
            raise ContractError(f"capacity applies_to.{field} must be an array")
        if any(not isinstance(item, str) or not item for item in entries):
            raise ContractError(
                f"capacity applies_to.{field} must contain non-empty strings",
            )
        if len(set(entries)) != len(entries):
            raise ContractError(f"capacity applies_to.{field} must be unique")
        normalized[field] = sorted(entries)
    return normalized


def validate_observation(
    value: dict[str, Any], *, now: datetime | None = None,
) -> dict[str, Any]:
    """Validate and normalize one schema-v1 capacity observation."""
    if not isinstance(value, dict) or set(value) != OBSERVATION_FIELDS:
        unknown = sorted(set(value) - OBSERVATION_FIELDS) if isinstance(value, dict) else []
        missing = sorted(OBSERVATION_FIELDS - set(value)) if isinstance(value, dict) else []
        raise ContractError(
            f"capacity observation fields invalid: unknown={unknown} missing={missing}",
        )
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("capacity observation schema_version is invalid")
    normalized = {
        **value,
        "observation_id": _identifier(value["observation_id"], "observation_id"),
        "pool_id": _identifier(value["pool_id"], "pool_id"),
        "window_id": _identifier(value["window_id"], "window_id"),
        "applies_to": _selector(value["applies_to"]),
    }
    if not isinstance(value["source"], str) or value["source"] not in SOURCES:
        raise ContractError("capacity source is invalid")
    if not isinstance(value["unit"], str) or value["unit"] not in UNITS:
        raise ContractError("capacity unit is invalid")
    if not isinstance(value["confidence"], str) or value["confidence"] not in CONFIDENCE:
        raise ContractError("capacity confidence is invalid")
    normalized["used"] = _measurement(value["used"], "used")
    normalized["limit"] = _measurement(value["limit"], "limit")
    if value["unit"] == "percent" and any(
        item is not None and item > 100
        for item in (normalized["used"], normalized["limit"])
    ):
        raise ContractError("capacity percent measurements must be at most 100")
    observed = _timestamp(value["observed_at"], "observed_at")
    expires = _timestamp(value["expires_at"], "expires_at")
    _timestamp(value["resets_at"], "resets_at", nullable=True)
    if observed > expires:
        raise ContractError("capacity observed_at must not be after expires_at")
    current = _authoritative_now(now)
    if observed > current + MAX_CLOCK_SKEW:
        raise ContractError("capacity observed_at exceeds allowed clock skew")
    # A canonical round trip proves every retained value is finite JSON and
    # prevents callers from mutating the source object after validation.
    return json.loads(canonical_json(normalized))


def _matches(selector: dict[str, list[str]], target: dict[str, Any] | None) -> bool:
    mapping = {
        "harnesses": "harness",
        "model_families": "model_family",
        "model_ids": "model_id",
    }
    for selector_field, target_field in mapping.items():
        allowed = selector[selector_field]
        if not allowed:
            continue
        if target is None or target.get(target_field) not in allowed:
            return False
    return True


def derive_pool_capacity(
    pool_id: str,
    observations: list[dict[str, Any]],
    *,
    target: dict[str, Any] | None = None,
    in_flight: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Derive hard availability from the latest applicable observation per window."""
    _identifier(pool_id, "pool_id")
    if not isinstance(observations, list):
        raise ContractError("capacity observations must be an array")
    if type(in_flight) is not int or in_flight < 0:
        raise ContractError("capacity in_flight must be a non-negative integer")
    if target is not None:
        if not isinstance(target, dict) or any(
            not isinstance(target.get(field), str) or not target[field]
            for field in ("harness", "model_family", "model_id")
        ):
            raise ContractError("capacity target identity is invalid")
    current = _authoritative_now(now)
    latest: dict[tuple[str, str], tuple[datetime, str, dict[str, Any]]] = {}
    seen_ids: set[str] = set()
    for raw in observations:
        observation = validate_observation(raw, now=current)
        observation_id = observation["observation_id"]
        if observation_id in seen_ids:
            raise ContractError("capacity observation ids must be unique")
        seen_ids.add(observation_id)
        if observation["pool_id"] != pool_id or not _matches(
            observation["applies_to"], target,
        ):
            continue
        key = (observation["window_id"], canonical_json(observation["applies_to"]))
        ordering = (
            _timestamp(observation["observed_at"], "observed_at"),
            observation_id,
        )
        prior = latest.get(key)
        if prior is None or ordering[:2] > prior[:2]:
            latest[key] = (ordering[0], ordering[1], observation)

    windows = []
    for key in sorted(latest):
        observation = latest[key][2]
        fresh = current <= _timestamp(observation["expires_at"], "expires_at")
        authoritative = (
            observation["source"] != "estimated"
            and observation["confidence"] != "estimated"
        )
        if not fresh:
            status, reason = "unknown", "stale_observation"
        elif not authoritative:
            status, reason = "unknown", "estimated_observation"
        elif observation["used"] is None or observation["limit"] is None:
            status, reason = "unknown", "unknown_measurement"
        elif observation["used"] >= observation["limit"]:
            status, reason = "exhausted", "window_exhausted"
        else:
            status, reason = "available", "window_available"
        windows.append({
            "observation_id": observation["observation_id"],
            "window_id": observation["window_id"],
            "applies_to": observation["applies_to"],
            "observed_at": observation["observed_at"],
            "expires_at": observation["expires_at"],
            "resets_at": observation["resets_at"],
            "source": observation["source"],
            "confidence": observation["confidence"],
            "used": observation["used"],
            "limit": observation["limit"],
            "unit": observation["unit"],
            "fresh": fresh,
            "authoritative": authoritative,
            "status": status,
            "reason": reason,
        })

    if any(window["status"] == "exhausted" for window in windows):
        status = "exhausted"
    elif not windows or any(window["status"] == "unknown" for window in windows):
        status = "unknown"
    else:
        status = "available"
    observed_at = None
    if windows:
        observed_at = max(
            windows,
            key=lambda window: _timestamp(window["observed_at"], "observed_at"),
        )["observed_at"]
    reasons = [
        f"{window['reason']}:{window['window_id']}"
        for window in windows
        if window["status"] != "available"
    ]
    if not windows:
        reasons = ["no_applicable_observations"]
    return {
        "schema_version": 1,
        "pool_id": pool_id,
        "status": status,
        "in_flight": in_flight,
        "observed_at": observed_at,
        "evaluated_at": current.isoformat(),
        "target": None if target is None else {
            field: target[field] for field in ("harness", "model_family", "model_id")
        },
        "windows": windows,
        "reasons": reasons,
    }
