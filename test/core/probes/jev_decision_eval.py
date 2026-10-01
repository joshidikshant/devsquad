#!/usr/bin/env python3
"""Run one bounded, synthetic Jev decision-classifier pilot.

The probe is deliberately separate from the DevSquad runtime. It makes exactly
one billable request, performs no retries, accepts the API key through the
environment or an explicitly selected private env file, and writes a redacted
result containing no task text. Dry run is the default and makes no API call.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import ssl
import stat
import sys
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SPEC = (
    ROOT
    / "docs"
    / "plans"
    / "engineering-team"
    / "experiments"
    / "jev-pilot-v1.json"
)
CONTEXT_LIMIT_TOKENS = 64_000
OFFICIAL_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
PINNED_MODEL = "jev-1.13.0"
MAX_RESPONSE_BYTES = 1_048_576
MAX_ENV_BYTES = 16_384


class ProbeError(RuntimeError):
    """A safe, user-facing probe failure."""


def _key_value(value: str) -> str | None:
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127
           for character in value):
        raise ProbeError("TYPESAFE_API_KEY must be a single non-whitespace value")
    return value or None


def load_api_key(env_file: Path | None = None) -> str | None:
    """Read only the Jev key; never source shell code or mutate process env."""
    exported = os.environ.get("TYPESAFE_API_KEY")
    if exported:
        return _key_value(exported)
    if env_file is None:
        return None
    try:
        descriptor = os.open(env_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ProbeError("Jev env file must be a regular file")
            if info.st_mode & 0o077:
                raise ProbeError("Jev env file permissions must be private (chmod 600)")
            if info.st_size > MAX_ENV_BYTES:
                raise ProbeError("Jev env file exceeded the size limit")
            contents = handle.read(MAX_ENV_BYTES + 1)
    except (OSError, UnicodeError):
        raise ProbeError("Jev env file could not be read; use a private UTF-8 regular file") from None
    if len(contents) > MAX_ENV_BYTES:
        raise ProbeError("Jev env file exceeded the size limit")
    key = None
    found = False
    for line in contents.splitlines():
        match = re.fullmatch(r"(?:export[ \t]+)?TYPESAFE_API_KEY[ \t]*=[ \t]*(.*)", line.strip())
        if match is None:
            continue
        if found:
            raise ProbeError("Jev env file defines TYPESAFE_API_KEY more than once")
        found = True
        value = match[1].strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ProbeError("Jev env file has an invalid quoted TYPESAFE_API_KEY")
            value = value[1:-1]
        else:
            value = value.partition(" #")[0].rstrip()
        key = _key_value(value)
    return key


def load_spec(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        spec = json.load(handle)
    required = {
        "schema_version",
        "experiment_id",
        "status",
        "model",
        "endpoint",
        "pricing",
        "budget",
        "purposes",
        "cases",
    }
    if set(spec) != required:
        raise ProbeError("pilot spec fields do not match the version-1 contract")
    if spec["schema_version"] != 1:
        raise ProbeError("unsupported pilot spec version")
    if spec["status"] != "ready_for_live_run":
        raise ProbeError("pilot spec is not approved for a live run")
    if spec["endpoint"] != OFFICIAL_ENDPOINT or spec["model"] != PINNED_MODEL:
        raise ProbeError("pilot endpoint and model must remain pinned")
    pricing = spec["pricing"]
    if not isinstance(pricing, dict) or set(pricing) != {
        "checked_at",
        "usd_per_million_input_tokens",
        "max_cost_usd",
    }:
        raise ProbeError("pilot pricing fields are invalid")
    if pricing["checked_at"] != "2026-09-26":
        raise ProbeError("pilot pricing must be rechecked before changing its date")
    if not all(
        type(pricing[name]) in {int, float}
        and math.isfinite(pricing[name])
        and pricing[name] > 0
        for name in ("usd_per_million_input_tokens", "max_cost_usd")
    ):
        raise ProbeError("pilot pricing values must be finite and positive")
    if pricing["max_cost_usd"] != 0.01:
        raise ProbeError("pilot cost ceiling must remain exactly $0.01")
    if spec["budget"] != {
        "max_billable_requests": 1,
        "retries": 0,
        "timeout_seconds": 30,
        "data_class": "synthetic_public_fixture",
    }:
        raise ProbeError("pilot budget must remain one request with no retries")
    if not spec["cases"] or not spec["purposes"]:
        raise ProbeError("pilot requires cases and purposes")
    case_ids = [case.get("id") for case in spec["cases"]]
    purpose_ids = [purpose.get("id") for purpose in spec["purposes"]]
    if len(case_ids) != len(set(case_ids)) or not all(
        isinstance(value, str) and value for value in case_ids
    ):
        raise ProbeError("case IDs must be unique non-empty strings")
    if len(purpose_ids) != len(set(purpose_ids)) or not all(
        isinstance(value, str) and value for value in purpose_ids
    ):
        raise ProbeError("purpose IDs must be unique non-empty strings")
    for case in spec["cases"]:
        if set(case) != {"id", "task", "expected"}:
            raise ProbeError(f"case {case.get('id')} has invalid fields")
        if set(case["expected"]) != set(purpose_ids):
            raise ProbeError(f"case {case['id']} lacks an expected purpose label")
    purpose_map = {item["id"]: item for item in spec["purposes"]}
    for purpose_id, purpose in purpose_map.items():
        if set(purpose) != {"id", "instructions", "criteria"}:
            raise ProbeError(f"purpose {purpose_id} has invalid fields")
        if not isinstance(purpose["instructions"], str) or not purpose["instructions"]:
            raise ProbeError(f"purpose {purpose_id} lacks instructions")
        if (
            not isinstance(purpose["criteria"], dict)
            or len(purpose["criteria"]) < 2
            or not all(
                isinstance(key, str)
                and key
                and isinstance(value, str)
                and value
                for key, value in purpose["criteria"].items()
            )
        ):
            raise ProbeError(f"purpose {purpose_id} criteria are invalid")
    for case in spec["cases"]:
        if not isinstance(case["task"], str) or not case["task"]:
            raise ProbeError(f"case {case['id']} lacks task text")
        for purpose_id, expected in case["expected"].items():
            if expected not in purpose_map[purpose_id]["criteria"]:
                raise ProbeError(f"case {case['id']} has an unknown expected label")
    return spec


def build_request(spec: dict[str, Any]) -> dict[str, Any]:
    state = [
        {"task_id": case["id"], "task": case["task"]}
        for case in spec["cases"]
    ]
    questions: dict[str, Any] = {}
    for case in spec["cases"]:
        for purpose in spec["purposes"]:
            key = f"{case['id']}__{purpose['id']}"
            questions[key] = {
                "type": "choice",
                "instructions": (
                    f"For task_id {case['id']} only: {purpose['instructions']}"
                ),
                "criteria": purpose["criteria"],
            }
    return {"state": state, "model": spec["model"], "questions": questions}


def _finite_probability(value: Any) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(value)
        and 0.0 <= float(value) <= 1.0
    )


def validate_response(
    spec: dict[str, Any], request_body: dict[str, Any], response: Any
) -> dict[str, Any]:
    if not isinstance(response, dict) or set(response) != {"model", "answers", "usage"}:
        raise ProbeError("Jev response envelope is invalid")
    if response["model"] != spec["model"]:
        raise ProbeError("Jev response model does not match the concrete pin")
    answers = response["answers"]
    if not isinstance(answers, dict) or set(answers) != set(request_body["questions"]):
        raise ProbeError("Jev answer IDs do not match the frozen questions")
    usage = response["usage"]
    if not isinstance(usage, dict) or set(usage) != {"input_tokens", "output_tokens"}:
        raise ProbeError("Jev usage is invalid")
    if not all(type(usage[name]) is int and usage[name] >= 0 for name in usage):
        raise ProbeError("Jev usage counts must be non-negative integers")

    results = []
    purpose_map = {item["id"]: item for item in spec["purposes"]}
    for case in spec["cases"]:
        predictions: dict[str, Any] = {}
        for purpose_id, purpose in purpose_map.items():
            key = f"{case['id']}__{purpose_id}"
            answer = answers[key]
            criteria = set(purpose["criteria"])
            if not isinstance(answer, dict) or set(answer) != {
                "type",
                "choice",
                "confidence",
                "probabilities",
            }:
                raise ProbeError(f"answer {key} has invalid fields")
            probabilities = answer["probabilities"]
            if answer["type"] != "choice" or answer["choice"] not in criteria:
                raise ProbeError(f"answer {key} has an unknown choice")
            if not _finite_probability(answer["confidence"]):
                raise ProbeError(f"answer {key} has invalid confidence")
            if not isinstance(probabilities, dict) or set(probabilities) != criteria:
                raise ProbeError(f"answer {key} probability labels are invalid")
            if not all(_finite_probability(value) for value in probabilities.values()):
                raise ProbeError(f"answer {key} probabilities are invalid")
            if not math.isclose(sum(probabilities.values()), 1.0, abs_tol=0.02):
                raise ProbeError(f"answer {key} probabilities do not sum to one")
            predictions[purpose_id] = {
                "expected": case["expected"][purpose_id],
                "choice": answer["choice"],
                "correct": answer["choice"] == case["expected"][purpose_id],
                "confidence": answer["confidence"],
                "probabilities": probabilities,
            }
        results.append({"case_id": case["id"], "predictions": predictions})
    return {"model": response["model"], "usage": usage, "cases": results}


def summarize(
    spec: dict[str, Any],
    validated: dict[str, Any],
    elapsed_ms: int,
    request_sha256: str,
) -> dict[str, Any]:
    per_purpose: dict[str, dict[str, int]] = {
        purpose["id"]: {"correct": 0, "total": 0}
        for purpose in spec["purposes"]
    }
    for case in validated["cases"]:
        for purpose_id, prediction in case["predictions"].items():
            per_purpose[purpose_id]["total"] += 1
            per_purpose[purpose_id]["correct"] += int(prediction["correct"])
    for counts in per_purpose.values():
        counts["accuracy_percent"] = round(
            100.0 * counts["correct"] / counts["total"], 1
        )
    input_tokens = validated["usage"]["input_tokens"]
    rate = spec["pricing"]["usd_per_million_input_tokens"]
    cost = input_tokens * rate / 1_000_000
    return {
        "schema_version": 1,
        "experiment_id": spec["experiment_id"],
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "data_class": spec["budget"]["data_class"],
        "request_sha256": request_sha256,
        "billable_requests": 1,
        "requested_model": spec["model"],
        "observed_model": validated["model"],
        "elapsed_ms": elapsed_ms,
        "usage": validated["usage"],
        "pricing": {
            "usd_per_million_input_tokens": rate,
            "estimated_cost_usd": round(cost, 8),
            "max_cost_usd": spec["pricing"]["max_cost_usd"],
        },
        "per_purpose": per_purpose,
        "cases": validated["cases"],
        "interpretation": (
            "Synthetic smoke result only; it does not establish a production "
            "routing improvement or authorize advisory mode."
        ),
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def execute(
    spec: dict[str, Any], request_body: dict[str, Any], *, env_file: Path | None = None,
) -> dict[str, Any]:
    key = load_api_key(env_file)
    if not key:
        raise ProbeError("TYPESAFE_API_KEY is not set")
    encoded = json.dumps(request_body, separators=(",", ":")).encode("utf-8")
    request = Request(
        spec["endpoint"],
        data=encoded,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "devsquad-jev-pilot/1",
        },
        method="POST",
    )
    start = time.monotonic()
    try:
        with urlopen(
            request,
            timeout=spec["budget"]["timeout_seconds"],
            context=ssl.create_default_context(),
        ) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ProbeError("TypeSafe API response exceeded the size limit")
            payload = json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        raise ProbeError(f"TypeSafe API returned HTTP {exc.code}; no retry attempted") from None
    except URLError as exc:
        raise ProbeError(f"TypeSafe API was unreachable ({exc.reason}); no retry attempted") from None
    except (TimeoutError, json.JSONDecodeError):
        raise ProbeError("TypeSafe API timed out or returned invalid JSON; no retry attempted") from None
    elapsed_ms = round((time.monotonic() - start) * 1000)
    validated = validate_response(spec, request_body, payload)
    request_sha256 = hashlib.sha256(encoded).hexdigest()
    return summarize(spec, validated, elapsed_ms, request_sha256)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--env-file", type=Path,
        help="Read TYPESAFE_API_KEY from a private file; an exported key takes precedence",
    )
    args = parser.parse_args(argv)
    try:
        spec = load_spec(args.spec)
        request_body = build_request(spec)
        price = spec["pricing"]["usd_per_million_input_tokens"]
        max_cost = spec["pricing"]["max_cost_usd"]
        documented_max_cost = CONTEXT_LIMIT_TOKENS * price / 1_000_000
        if documented_max_cost > max_cost:
            raise ProbeError(
                "one maximum-context request exceeds the frozen cost ceiling; "
                "stop and evaluate Laya"
            )
        dry_run = {
            "experiment_id": spec["experiment_id"],
            "cases": len(spec["cases"]),
            "questions": len(request_body["questions"]),
            "billable_requests": 1,
            "retries": 0,
            "request_bytes": len(
                json.dumps(request_body, separators=(",", ":")).encode("utf-8")
            ),
            "request_sha256": hashlib.sha256(
                json.dumps(request_body, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "documented_max_request_cost_usd": round(documented_max_cost, 8),
            "cost_ceiling_usd": max_cost,
            "data_class": spec["budget"]["data_class"],
        }
        if not args.execute:
            if args.env_file is not None:
                dry_run["api_key_configured"] = bool(load_api_key(args.env_file))
            print(json.dumps(dry_run, indent=2, sort_keys=True))
            return 0
        if args.output is None:
            raise ProbeError("--output is required for a live run")
        if args.output.resolve().is_relative_to(ROOT):
            raise ProbeError("live output must remain outside the Git repository")
        result = execute(spec, request_body, env_file=args.env_file)
        write_json(args.output, result)
        print(json.dumps({**dry_run, "result": str(args.output)}, indent=2, sort_keys=True))
        if result["pricing"]["estimated_cost_usd"] > max_cost:
            raise ProbeError("actual reported usage exceeded the cost ceiling; switch to Laya")
        return 0
    except ProbeError as exc:
        print(f"JEV_PROBE_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
