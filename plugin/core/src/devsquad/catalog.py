"""Structured, last-good model catalog handling for M1."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .contracts import ContractError


def model_fingerprint(harness: str, version: str | None, model: dict[str, Any]) -> str:
    stable = {"harness": harness, "version": version, "model": model}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def normalize_models(harness: str, version: str | None, models: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for raw in models:
        model_id = raw.get("id") or raw.get("model")
        if not isinstance(model_id, str) or not model_id:
            raise ContractError("catalog model missing id")
        effort_values = raw.get("supportedReasoningEfforts") or raw.get("supported_reasoning_efforts") or []
        efforts = [item.get("reasoningEffort") if isinstance(item, dict) else item for item in effort_values]
        normalized.append({
            "id": model_id,
            "display_name": raw.get("displayName") or raw.get("display_name") or model_id,
            "family": raw.get("family"),
            "default_effort": raw.get("defaultReasoningEffort") or raw.get("default_reasoning_effort"),
            "supported_efforts": efforts,
            "modalities": raw.get("inputModalities") or raw.get("input_modalities") or [],
            "is_default": bool(raw.get("isDefault") or raw.get("is_default")),
            "qualification": "unqualified",
            "fingerprint": model_fingerprint(harness, version, raw),
        })
    return normalized


def update_last_good(path: Path, *, harness: str, version: str | None, models: Iterable[dict[str, Any]] | None, complete: bool, error: str | None = None) -> dict[str, Any]:
    old = json.loads(path.read_text()) if path.exists() else None
    now = datetime.now(timezone.utc).isoformat()
    if error or not complete or models is None:
        if old:
            old["last_refresh"] = {"at": now, "status": "error" if error else "incomplete", "error": error}
            _atomic_json(path, old)
            return old
        raise ContractError(error or "catalog response incomplete and no last-good snapshot exists")
    value = {"schema_version": 1, "harness": harness, "harness_version": version, "fetched_at": now, "complete": True, "models": normalize_models(harness, version, models), "last_refresh": {"at": now, "status": "ok", "error": None}}
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
