"""Deterministic, side-effect-free M3 execution-profile selection."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any

from .contracts import (
    CapabilityUnavailable,
    ContractError,
    PolicyDenied,
    ProfileUnsupported,
)
from .store import canonical_json
from .validation import validate_policy, validate_profile_registry, validate_task


QUALITY_RANK = {"unvalidated": 0, "trial": 1, "proven": 2}
ROLE_PERMISSIONS = {
    "implementer": "workspace_write",
    "reviewer": "read_only",
    "lead": "read_only",
    "researcher": "read_only",
}
WORKFLOW_ROLES = {
    "branch-review": ("reviewer",),
    "issue-delivery": ("implementer", "reviewer"),
}
CAPACITY_STATES = {"available", "exhausted", "unknown"}


def _strict_json(payload: bytes | str, label: str) -> tuple[dict[str, Any], str]:
    if isinstance(payload, str):
        encoded = payload.encode()
    elif isinstance(payload, bytes):
        encoded = payload
    else:
        raise ContractError(f"{label} must be JSON bytes or text")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ContractError(f"{label} contains duplicate key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ContractError(f"{label} contains non-finite number: {value}")

    try:
        value = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label} must contain a JSON object")
    return value, hashlib.sha256(encoded).hexdigest()


def _availability_snapshot(
    policy: dict[str, Any], availability: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    supplied = {} if availability is None else availability
    if not isinstance(supplied, dict):
        raise ContractError("capacity availability must be an object")
    unknown_pools = set(supplied) - set(policy["account_pools"])
    if unknown_pools:
        raise ContractError(f"capacity references unknown account pools: {sorted(unknown_pools)}")
    result = {}
    for pool_id, pool_policy in policy["account_pools"].items():
        observation = supplied.get(pool_id, {"status": "unknown", "in_flight": 0})
        if not isinstance(observation, dict):
            raise ContractError("capacity observation must be an object")
        unknown = set(observation) - {"status", "in_flight", "observed_at"}
        missing = {"status", "in_flight"} - set(observation)
        if unknown or missing:
            raise ContractError(
                f"capacity observation fields invalid: unknown={sorted(unknown)} "
                f"missing={sorted(missing)}"
            )
        status, in_flight = observation["status"], observation["in_flight"]
        if not isinstance(status, str) or status not in CAPACITY_STATES:
            raise ContractError("capacity status is invalid")
        if type(in_flight) is not int or in_flight < 0:
            raise ContractError("capacity in_flight must be a non-negative integer")
        observed_at = observation.get("observed_at")
        if observed_at is not None:
            if not isinstance(observed_at, str) or not observed_at:
                raise ContractError("capacity observed_at must be a timestamp or null")
            try:
                parsed = datetime.fromisoformat(observed_at)
            except ValueError as exc:
                raise ContractError("capacity observed_at must be an ISO timestamp") from exc
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ContractError("capacity observed_at must include a timezone")
        result[pool_id] = {
            "status": status,
            "in_flight": in_flight,
            "observed_at": observed_at,
            "max_concurrency": pool_policy["max_concurrency"],
            "unknown_capacity_policy": pool_policy.get(
                "unknown_capacity_policy", "allow_bounded",
            ),
        }
    return result


def _resolve_reference(
    reference: dict[str, str],
    profiles: dict[str, dict[str, Any]],
    bindings: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    if reference["kind"] == "profile":
        profile = profiles.get(reference["id"])
        return profile, None, None if profile else "profile_not_found"
    binding = bindings.get(reference["id"])
    if binding is None:
        return None, None, "alias_unbound"
    profile = profiles.get(binding["profile_id"])
    if profile is None:  # Defensive: registry validation already establishes this.
        return None, None, "binding_target_missing"
    return profile, {
        "alias": reference["id"],
        "version": binding["version"],
        "profile_id": binding["profile_id"],
    }, None


def _static_reason(
    profile: dict[str, Any],
    role: str,
    minimum_quality: str,
    pool_policy: dict[str, Any] | None,
    implementer: dict[str, Any] | None,
    require_different_model: bool,
) -> str | None:
    if profile["quality_status"] == "suspended":
        return "profile_suspended"
    minimum_rank = QUALITY_RANK.get(minimum_quality)
    profile_rank = QUALITY_RANK.get(profile["quality_status"])
    if minimum_rank is None or profile_rank is None or profile_rank < minimum_rank:
        return "quality_below_task_minimum"
    if profile["permission_policy"] != ROLE_PERMISSIONS[role]:
        return "permission_mismatch"
    if pool_policy is None:
        return "account_pool_not_declared"
    if profile["billing_mode"] not in pool_policy["allowed_billing_modes"]:
        return "billing_mode_not_allowed"
    if (role == "reviewer" and implementer is not None and require_different_model
            and profile["model_family"] == implementer["model_family"]
            and profile["model_id"] == implementer["model_id"]):
        return "review_model_not_independent"
    return None


def _capacity_reason(
    profile: dict[str, Any], capacity: dict[str, dict[str, Any]],
) -> str | None:
    pool = capacity[profile["account_pool_id"]]
    if pool["status"] == "exhausted":
        return "account_pool_exhausted"
    if pool["in_flight"] >= pool["max_concurrency"]:
        return "account_pool_concurrency_full"
    if pool["status"] == "unknown":
        if pool["unknown_capacity_policy"] == "block":
            return "unknown_capacity_blocked"
        if pool["in_flight"] >= 1:
            return "unknown_capacity_trial_in_flight"
    return None


def _profile_snapshot(
    profile: dict[str, Any], reference: dict[str, str], binding: dict[str, Any] | None,
) -> dict[str, Any]:
    frozen = json.loads(canonical_json(profile))
    return {
        "reference": dict(reference),
        "binding": dict(binding) if binding is not None else None,
        "profile_id": profile["id"],
        "profile_sha256": hashlib.sha256(canonical_json(profile).encode()).hexdigest(),
        "profile": frozen,
    }


def _policy_candidates(
    role: str,
    policy: dict[str, Any],
    profiles: dict[str, dict[str, Any]],
    bindings: dict[str, dict[str, Any]],
    minimum_quality: str,
    capacity: dict[str, dict[str, Any]],
    implementer: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    references = list(policy["roles"].get(role, []))
    if role == "reviewer" and implementer is not None and policy.get(
        "prefer_different_harness_for_review", False,
    ):
        references.sort(
            key=lambda reference: (
                (_resolve_reference(reference, profiles, bindings)[0] or {}).get("harness")
                == implementer["harness"]
            )
        )
    eligible, excluded, has_static_candidate = [], [], False
    seen = set()
    for reference in references:
        profile, binding, resolution_error = _resolve_reference(
            reference, profiles, bindings,
        )
        reason = resolution_error
        if profile is not None and reason is None:
            reason = _static_reason(
                profile,
                role,
                minimum_quality,
                policy["account_pools"].get(profile["account_pool_id"]),
                implementer,
                policy["require_different_model_for_review"],
            )
            if reason is None:
                has_static_candidate = True
                reason = _capacity_reason(profile, capacity)
        profile_id = profile["id"] if profile is not None else None
        if reason is None and profile_id in seen:
            reason = "duplicate_effective_profile"
        if reason is not None:
            excluded.append({
                "reference": dict(reference),
                "profile_id": profile_id,
                "reason": reason,
            })
            continue
        seen.add(profile_id)
        eligible.append(_profile_snapshot(profile, reference, binding))
    return eligible, excluded, has_static_candidate


def resolve_routing(
    task: dict[str, Any],
    profile_registry: dict[str, Any],
    policy: dict[str, Any],
    *,
    availability: dict[str, Any] | None = None,
    profiles_sha256: str | None = None,
    policy_sha256: str | None = None,
) -> dict[str, Any]:
    """Resolve and freeze every model role used by a fixed workflow."""
    validate_task(task)
    validate_profile_registry(profile_registry)
    validate_policy(policy)
    profiles = {profile["id"]: profile for profile in profile_registry["profiles"]}
    bindings = profile_registry["bindings"]
    capacity = _availability_snapshot(policy, availability)
    minimum_quality = policy["task_classes"].get(task["task_class"])
    if minimum_quality is None:
        raise PolicyDenied(f"policy does not authorize task class: {task['task_class']}")

    roles = list(WORKFLOW_ROLES[task["workflow"]])
    if task["lead"]["mode"] == "headless":
        roles.append("lead")
    overrides = task["routing"].get("overrides", {})
    unsupported_overrides = set(overrides) - set(roles)
    if unsupported_overrides:
        raise ContractError(
            f"routing overrides are not roles in {task['workflow']}: "
            f"{sorted(unsupported_overrides)}"
        )
    missing_roles = [role for role in roles if not policy["roles"].get(role)]
    if missing_roles:
        raise PolicyDenied(f"policy has no candidates for required roles: {missing_roles}")

    selected_roles: dict[str, Any] = {}
    implementer = None
    max_fallbacks = task["budget"]["max_fallbacks_per_step"]
    for role in roles:
        eligible, excluded, has_static_candidate = _policy_candidates(
            role,
            policy,
            profiles,
            bindings,
            minimum_quality,
            capacity,
            implementer,
        )
        override = overrides.get(role)
        selected = None
        source = "automatic"
        fallback_mode = "policy"
        if override is not None:
            source = "override"
            fallback_mode = override.get("fallback", "none")
            pinned = profiles.get(override["profile_id"])
            if pinned is None:
                raise ProfileUnsupported(
                    f"pinned {role} profile does not exist: {override['profile_id']}"
                )
            static_reason = _static_reason(
                pinned,
                role,
                minimum_quality,
                policy["account_pools"].get(pinned["account_pool_id"]),
                implementer,
                policy["require_different_model_for_review"],
            )
            if static_reason is not None:
                raise ProfileUnsupported(
                    f"pinned {role} profile is unsupported: {static_reason}"
                )
            capacity_reason = _capacity_reason(pinned, capacity)
            if capacity_reason is None:
                selected = _profile_snapshot(
                    pinned, {"kind": "profile", "id": pinned["id"]}, None,
                )
            elif fallback_mode == "none":
                raise CapabilityUnavailable(
                    f"pinned {role} profile is unavailable: {capacity_reason}"
                )
            else:
                excluded.insert(0, {
                    "reference": {"kind": "profile", "id": pinned["id"]},
                    "profile_id": pinned["id"],
                    "reason": capacity_reason,
                })
        if selected is None:
            if not eligible:
                if has_static_candidate:
                    raise CapabilityUnavailable(
                        f"no currently available profile for role: {role}"
                    )
                raise PolicyDenied(f"no policy-eligible profile for role: {role}")
            selected = eligible.pop(0)
        fallbacks = []
        if fallback_mode == "policy":
            fallbacks = [
                candidate for candidate in eligible
                if candidate["profile_id"] != selected["profile_id"]
            ][:max_fallbacks]
        selected_roles[role] = {
            "source": source,
            "fallback_mode": fallback_mode,
            "selected": selected,
            "fallbacks": fallbacks,
            "excluded": excluded,
        }
        if role == "implementer":
            implementer = selected["profile"]

    return {
        "schema_version": 1,
        "policy": {
            "id": policy["id"],
            "version": policy["version"],
            "sha256": policy_sha256 or hashlib.sha256(canonical_json(policy).encode()).hexdigest(),
        },
        "profile_registry": {
            "schema_version": profile_registry["schema_version"],
            "sha256": profiles_sha256
            or hashlib.sha256(canonical_json(profile_registry).encode()).hexdigest(),
        },
        "task_class": task["task_class"],
        "workflow": task["workflow"],
        "capacity": capacity,
        "roles": selected_roles,
    }


def load_routing(
    task: dict[str, Any],
    profiles_payload: bytes | str,
    policy_payload: bytes | str,
    *,
    availability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Strictly decode profile/policy files and freeze hashes with selection."""
    profile_registry, profiles_sha256 = _strict_json(profiles_payload, "profiles file")
    policy, policy_sha256 = _strict_json(policy_payload, "policy file")
    return resolve_routing(
        task,
        profile_registry,
        policy,
        availability=availability,
        profiles_sha256=profiles_sha256,
        policy_sha256=policy_sha256,
    )


def capacity_with_live_reservations(
    policy_payload: bytes | str,
    in_flight: dict[str, int],
) -> dict[str, dict[str, Any]]:
    """Bind transactionally observed local reservations to one policy snapshot."""
    policy, _ = _strict_json(policy_payload, "policy file")
    validate_policy(policy)
    if (not isinstance(in_flight, dict)
            or not all(
                isinstance(pool_id, str) and pool_id
                and type(count) is int and count >= 0
                for pool_id, count in in_flight.items()
            )):
        raise ContractError("live capacity reservations are invalid")
    return {
        pool_id: {
            "status": "unknown",
            "in_flight": in_flight.get(pool_id, 0),
        }
        for pool_id in policy["account_pools"]
    }
