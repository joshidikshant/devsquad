"""Structured, last-good model catalog handling for M1."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .contracts import ContractError
from .store import canonical_json
from .validation import validate_profile


def model_fingerprint(harness: str, version: str | None, model: dict[str, Any]) -> str:
    stable = {"harness": harness, "version": version, "model": model}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def normalize_models(harness: str, version: str | None, models: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    identifiers = set()
    for raw in models:
        if not isinstance(raw, dict):
            raise ContractError("catalog model must be an object")
        model_id = raw.get("id") or raw.get("model")
        if not isinstance(model_id, str) or not model_id:
            raise ContractError("catalog model missing id")
        if model_id in identifiers:
            raise ContractError("catalog model ids must be unique")
        identifiers.add(model_id)
        effort_values = raw.get("supportedReasoningEfforts") or raw.get("supported_reasoning_efforts") or []
        efforts = [item.get("reasoningEffort") if isinstance(item, dict) else item for item in effort_values]
        if not all(isinstance(effort, str) and effort for effort in efforts):
            raise ContractError("catalog model effort metadata is invalid")
        normalized.append({
            "id": model_id,
            "display_name": raw.get("displayName") or raw.get("display_name") or model_id,
            "family": raw.get("family"),
            "default_effort": raw.get("defaultReasoningEffort") or raw.get("default_reasoning_effort"),
            "supported_efforts": efforts,
            "modalities": raw.get("inputModalities") or raw.get("input_modalities") or [],
            "revision": raw.get("modelRevision") or raw.get("revision"),
            "is_default": bool(raw.get("isDefault") or raw.get("is_default")),
            "qualification": "unqualified",
            "fingerprint": model_fingerprint(harness, version, raw),
        })
    return normalized


def analyze_catalog_drift(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    profiles: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Identify exact model/profile drift without changing any binding."""
    if (not isinstance(current, dict) or current.get("complete") is not True
            or not isinstance(current.get("harness"), str)
            or not isinstance(current.get("models"), list)):
        raise ContractError("current catalog snapshot is invalid")
    if previous is not None and (
            not isinstance(previous, dict) or previous.get("complete") is not True
            or previous.get("harness") != current["harness"]
            or not isinstance(previous.get("models"), list)):
        raise ContractError("previous catalog snapshot is invalid")

    def models_by_id(snapshot):
        result = {}
        for model in snapshot.get("models", []):
            if (not isinstance(model, dict)
                    or not isinstance(model.get("id"), str)
                    or model["id"] in result):
                raise ContractError("catalog snapshot model identity is invalid")
            result[model["id"]] = model
        return result

    old_models = {} if previous is None else models_by_id(previous)
    new_models = models_by_id(current)
    added = sorted(set(new_models) - set(old_models))
    removed = sorted(set(old_models) - set(new_models))
    changed = sorted(
        model_id for model_id in set(old_models) & set(new_models)
        if old_models[model_id].get("fingerprint")
        != new_models[model_id].get("fingerprint")
    )
    unknown_revision = sorted(
        model_id for model_id in changed
        if (old_models[model_id].get("revision") is None
            or new_models[model_id].get("revision") is None)
    )
    profile_rows = [] if profiles is None else list(profiles)
    for profile in profile_rows:
        validate_profile(profile)
    affected_ids = set(changed) | set(removed)
    affected_profiles = sorted(
        profile["id"] for profile in profile_rows
        if (profile["harness"] == current["harness"]
            and profile["model_id"] in affected_ids)
    )
    unavailable_profiles = sorted(
        profile["id"] for profile in profile_rows
        if (profile["harness"] == current["harness"]
            and profile["model_id"] in removed)
    )
    previous_sha256 = (
        hashlib.sha256(canonical_json(previous).encode()).hexdigest()
        if previous is not None else None
    )
    return {
        "schema_version": 1,
        "harness": current["harness"],
        "previous_sha256": previous_sha256,
        "current_sha256": hashlib.sha256(
            canonical_json(current).encode(),
        ).hexdigest(),
        "added_model_ids": added,
        "removed_model_ids": removed,
        "changed_model_ids": changed,
        "same_id_revision_unknown": unknown_revision,
        "affected_profile_ids": affected_profiles,
        "unavailable_profile_ids": unavailable_profiles,
        "profile_scope": "provided" if profiles is not None else "unavailable",
        "unqualified_candidate_ids": added,
        "binding_changes_applied": False,
    }


def update_last_good(path: Path, *, harness: str, version: str | None, models: Iterable[dict[str, Any]] | None, complete: bool, error: str | None = None, profiles: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    old = json.loads(path.read_text()) if path.exists() else None
    now = datetime.now(timezone.utc).isoformat()
    if error or not complete or models is None:
        if old:
            old["last_refresh"] = {"at": now, "status": "error" if error else "incomplete", "error": error}
            _atomic_json(path, old)
            return old
        raise ContractError(error or "catalog response incomplete and no last-good snapshot exists")
    value = {"schema_version": 1, "harness": harness, "harness_version": version, "fetched_at": now, "complete": True, "models": normalize_models(harness, version, models), "last_refresh": {"at": now, "status": "ok", "error": None}}
    value["catalog_change"] = analyze_catalog_drift(
        old, value, profiles=profiles,
    )
    _atomic_json(path, value)
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def verified_efforts(snapshot: dict[str, Any], *, harness: str, version: str, model_id: str) -> tuple[str, ...]:
    if snapshot.get("complete") is not True or snapshot.get("harness") != harness or snapshot.get("harness_version") != version:
        raise ContractError("catalog snapshot does not verify this harness version")
    matches = [m for m in snapshot.get("models", []) if m.get("id") == model_id]
    if len(matches) != 1:
        raise ContractError(f"model is not uniquely present in verified catalog: {model_id}")
    efforts = matches[0].get("supported_efforts")
    if not isinstance(efforts, list) or not all(isinstance(v, str) for v in efforts):
        raise ContractError("model effort metadata is unknown")
    return tuple(efforts)
