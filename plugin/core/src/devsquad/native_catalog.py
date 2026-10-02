"""Private, scoped last-good discovery and native subscription observations.

No inference, login, billing, credit-reset, or configuration mutation lives here.
The short-lived OS lock is the refresh lease: process death releases ownership.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import uuid
from typing import Any, Callable

from .catalog import analyze_catalog_drift, normalize_models
from .contracts import ContractError
from .store import canonical_json

CATALOG_TTL = timedelta(hours=24)
REFRESH_BACKOFF = timedelta(minutes=2)
QUOTA_TTL = timedelta(seconds=60)


def native_scope(account_result: dict[str, Any], config: dict[str, Any], binary: str, version: str) -> str:
    account = account_result.get("account")
    if not isinstance(account, dict) or account.get("type") != "chatgpt":
        raise ContractError("normal entry requires native ChatGPT subscription authentication")
    identity = {key: account.get(key) for key in ("type", "id", "accountId", "email", "planType")}
    if not any(isinstance(identity[key], str) and identity[key] for key in ("id", "accountId", "email")):
        raise ContractError("native account identity is unknown; discovery cannot be reused")
    # Hash in memory only. Neither account identifiers nor effective configuration
    # (which can contain sensitive provider fields) are persisted or displayed.
    return hashlib.sha256(canonical_json({
        "account": identity, "config": config, "binary": binary, "version": version,
    }).encode()).hexdigest()


def normalize_codex_limits(result: dict[str, Any], pool_id: str, *, now: datetime | None = None) -> list[dict[str, Any]]:
    current = now or datetime.now(timezone.utc)
    buckets = result.get("rateLimitsByLimitId")
    bucket = buckets.get("codex") if isinstance(buckets, dict) else result.get("rateLimits")
    bucket = bucket if isinstance(bucket, dict) else {}
    observations = []
    for slot in ("primary", "secondary"):
        window = bucket.get(slot)
        window = window if isinstance(window, dict) else {}
        used, duration, reset = (window.get(key) for key in ("usedPercent", "windowDurationMins", "resetsAt"))
        valid = (type(used) in (int, float) and math.isfinite(used) and 0 <= used <= 100
                 and type(duration) is int and duration > 0 and type(reset) is int)
        resets_at = None
        if valid:
            try:
                resets_at = datetime.fromtimestamp(reset, timezone.utc)
                valid = resets_at > current
            except (ValueError, OverflowError, OSError):
                valid = False
        expires = min(current + QUOTA_TTL, resets_at) if valid else current + QUOTA_TTL
        evidence = {
            "schema_version": 1, "pool_id": pool_id, "window_id": f"codex:{slot}",
            "applies_to": {"harnesses": ["codex"], "model_families": [], "model_ids": []},
            "observed_at": current.isoformat(), "expires_at": expires.isoformat(),
            "source": "native_reported", "used": used if valid else None,
            "limit": 100 if valid else None, "unit": "percent",
            "resets_at": resets_at.isoformat() if valid else None,
            "confidence": "reported",
        }
        evidence["observation_id"] = "native-" + hashlib.sha256(canonical_json(evidence).encode()).hexdigest()
        observations.append(evidence)
    return observations


class NativeCatalogCache:
    def __init__(self, directory: Path, scope: str, version: str):
        self.directory, self.scope, self.version = directory, scope, version
        self.path = directory / (hashlib.sha256(scope.encode()).hexdigest() + ".json")

    def _read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_bytes())
            if (value["scope"] != self.scope or value["harness_version"] != self.version
                    or value["complete"] is not True or not isinstance(value["models"], list)):
                raise ValueError()
            return value
        except (ValueError, KeyError, TypeError) as exc:
            raise ContractError("native catalog cache integrity is invalid") from exc

    @staticmethod
    def _recent(timestamp: str, current: datetime, ttl: timedelta) -> bool:
        try:
            at = datetime.fromisoformat(timestamp)
            return at.tzinfo is not None and timedelta(0) <= current - at < ttl
        except (TypeError, ValueError):
            return False

    def _write(self, value: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(f".tmp-{uuid.uuid4().hex}")
        try:
            with temporary.open("x", encoding="utf-8") as output:
                os.chmod(temporary, 0o600)
                output.write(canonical_json(value) + "\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def refresh(self, fetch: Callable[[], list[dict[str, Any]]], *, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a+") as lease:
            os.chmod(lease.name, 0o600)
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                previous = self._read()
                if previous is None:
                    raise ContractError("native catalog refresh is in progress; retry shortly")
                return previous
            try:
                previous = self._read()
                if previous is not None and (
                    self._recent(previous["fetched_at"], current, CATALOG_TTL)
                    or self._recent(previous["last_refresh"]["at"], current, REFRESH_BACKOFF)
                ):
                    return previous
                try:
                    raw_models = fetch()
                    if not isinstance(raw_models, list):
                        raise ContractError("native catalog response is incomplete")
                    models = normalize_models("codex", self.version, raw_models)
                except (ContractError, EOFError, OSError, TimeoutError):
                    if previous is None:
                        raise ContractError("native discovery failed with no scoped last-good catalog") from None
                    previous["last_refresh"] = {"at": current.isoformat(), "status": "error", "error": "discovery_failed"}
                    self._write(previous)
                    return previous
                value = {
                    "schema_version": 1, "scope": self.scope, "harness": "codex",
                    "harness_version": self.version, "fetched_at": current.isoformat(),
                    "complete": True, "models": models,
                    "last_refresh": {"at": current.isoformat(), "status": "ok", "error": None},
                }
                value["catalog_change"] = analyze_catalog_drift(previous, value)
                self._write(value)
                return value
            finally:
                fcntl.flock(lease, fcntl.LOCK_UN)
