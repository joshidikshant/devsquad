"""Bounded normal-command task preparation over the strict saved-run contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import sys
import time
from typing import Any, Iterable

from .adapters import AdapterManifest, harness_version
from .catalog import normalize_models
from .codex_protocol import (
    JsonLinePeer,
    discover_models,
    initialize_request,
    initialized_notification,
    receive_response,
    request,
)
from .contracts import ContractError
from .probe_process import capture_probe_identity, close_probe, subscription_environment
from .store import canonical_json
from .validation import validate_profile, validate_task


SOURCE_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = (
    SOURCE_ROOT
    if (SOURCE_ROOT / "adapters").is_dir()
    else Path(sys.prefix) / "share" / "devsquad"
)
MAX_USER_CHECKS = 12
NORMAL_POLICY = {"id": "managed-normal-entry", "version": 1}
NORMAL_ALIASES = {"implementer": "implement.balanced", "reviewer": "review.deep"}


def _git(repo: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *arguments],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContractError("cannot inspect the Git project") from exc
    if completed.returncode != 0:
        raise ContractError(f"Git project check failed: {' '.join(arguments[:2])}")
    return completed.stdout.strip()


def resolve_repository(project_dir: str | Path) -> Path:
    try:
        requested = Path(project_dir).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ContractError("project directory does not exist") from exc
    root = Path(_git(requested, "rev-parse", "--show-toplevel"))
    try:
        return root.resolve(strict=True)
    except OSError as exc:
        raise ContractError("Git project root does not exist") from exc


def _resolve_commit(repo: Path, reference: str) -> str:
    if not isinstance(reference, str) or not reference:
        raise ContractError("Git reference must be non-empty")
    return _git(repo, "rev-parse", "--verify", f"{reference}^{{commit}}")


def discover_codex_identity(
    repo: Path,
    *,
    requested_model: str | None = None,
    requested_effort: str | None = None,
    timeout_seconds: int = 15,
    runtime: Path | None = None,
) -> dict[str, str]:
    """Discover one currently available exact Codex model without generating."""

    manifest = AdapterManifest.load(CORE_ROOT / "adapters/codex/adapter.json")
    binary = manifest.resolve_binary()
    if binary is None:
        raise ContractError("Codex is unavailable; run squad doctor")
    version = harness_version(binary)
    if version not in manifest.verified_versions:
        raise ContractError(f"Codex version is not verified: {version or 'unknown'}")
    try:
        process = subprocess.Popen(
            [binary, "app-server", "--listen", "stdio://"],
            cwd=repo,
            env=subscription_environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
    except OSError as exc:
        raise ContractError("Codex native process could not start") from exc
    start_identity = capture_probe_identity(process)
    try:
        if start_identity is None:
            raise ContractError("Codex native process ownership is unavailable")
        assert process.stdin is not None and process.stdout is not None
        peer = JsonLinePeer(process.stdout, process.stdin)
        peer.send(initialize_request(1))
        initialized = receive_response(peer, 1, timeout_seconds=timeout_seconds)
        if "error" in initialized:
            raise ContractError("Codex native initialization failed")
        peer.send(initialized_notification())
        scope = None
        if runtime is None:
            models = normalize_models(
                "codex", version,
                discover_models(peer, first_request_id=10, timeout_seconds=timeout_seconds),
            )
        else:
            from .native_catalog import NativeCatalogCache, native_account_pool, native_scope, normalize_codex_limits
            from .service import Service

            deadline = time.monotonic() + timeout_seconds
            def read_native(request_id, method, params=None):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("native discovery deadline expired")
                peer.send(request(request_id, method, params))
                reply = receive_response(peer, request_id, timeout_seconds=remaining)
                if "error" in reply or not isinstance(reply.get("result"), dict):
                    raise ContractError(f"native {method} is unavailable")
                return reply["result"]

            account = read_native(2, "account/read", {"refreshToken": False})
            configuration = read_native(3, "config/read", {"includeLayers": False})
            scope = native_scope(account, configuration, binary, version)
            catalog = NativeCatalogCache(runtime / "catalogs", scope, version).refresh(
                lambda: discover_models(peer, first_request_id=10, timeout_seconds=max(0.001, deadline - time.monotonic())),
            )
            models = catalog["models"]
            pool_id = native_account_pool(account)
            try:
                limits = read_native(1000, "account/rateLimits/read")
            except (ContractError, EOFError, OSError, TimeoutError):
                # A failed query is not evidence that a still-fresh exhausted
                # window disappeared. Retain prior observations until expiry.
                limits = None
            service = Service(runtime)
            for observation in (normalize_codex_limits(limits, pool_id) if limits is not None else []):
                service.capacity_observe(observation)
    except (EOFError, OSError, TimeoutError) as exc:
        raise ContractError("Codex model discovery did not complete") from exc
    finally:
        close_probe(process, start_identity=start_identity)
    candidates = [
        model for model in models
        if model["supported_efforts"]
        and (requested_model is None or model["id"] == requested_model)
    ]
    if not candidates:
        qualifier = requested_model or "any model with effort metadata"
        raise ContractError(f"Codex model is unavailable: {qualifier}")
    selected = next(
        (model for model in candidates if model["is_default"]),
        candidates[0],
    )
    efforts = selected["supported_efforts"]
    if requested_effort is not None:
        if requested_effort not in efforts:
            raise ContractError(
                f"Codex effort {requested_effort!r} is unavailable for "
                f"{selected['id']}"
            )
        effort = requested_effort
    else:
        effort = next(
            (value for value in ("low", "medium") if value in efforts),
            efforts[0],
        )
    family = selected.get("family")
    return {
        "harness": "codex",
        "harness_version": version,
        "model_id": selected["id"],
        "model_family": family if isinstance(family, str) and family else "gpt",
        "effort": effort,
        **({"account_pool_id": pool_id, "native_scope": scope, "catalog_fingerprint": selected["fingerprint"]} if scope is not None else {}),
    }


def parse_checks(values: Iterable[str] | None) -> tuple[tuple[str, ...], ...]:
    parsed = []
    for value in values or ():
        if not isinstance(value, str) or not value.strip():
            raise ContractError("check command must be non-empty")
        try:
            arguments = tuple(shlex.split(value))
        except ValueError as exc:
            raise ContractError("check command has invalid quoting") from exc
        if not arguments:
            raise ContractError("check command must contain an executable")
        parsed.append(arguments)
    if len(parsed) > MAX_USER_CHECKS:
        raise ContractError(f"at most {MAX_USER_CHECKS} check commands are allowed")
    return tuple(parsed)


def _relative_path(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{label} must be non-empty")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ContractError(f"{label} must be repository-relative")
    normalized = path.as_posix().rstrip("/")
    return normalized or "."


def _managed_routing(
    workflow: str,
    codex: dict[str, str],
    *,
    claude_model: str,
    claude_effort: str,
    role_bindings: dict[str, Any],
    pinned_roles: set[str],
) -> dict[str, Any]:
    reviewer = {
        "id": "managed-codex-reviewer",
        "harness": "codex",
        "model_family": codex["model_family"],
        "model_id": codex["model_id"],
        "effort": {"value": codex["effort"], "transport": "native"},
        "required_tools": ["read"],
        "permission_policy": "read_only",
        "account_pool_id": codex.get("account_pool_id", "codex-subscription"),
        "billing_mode": "subscription",
        "quality_status": "trial",
        "evidence_refs": [
            f"runtime-catalog:{codex['harness_version']}:{codex['model_id']}",
            *([f"runtime-catalog-fingerprint:{codex['catalog_fingerprint']}"] if "catalog_fingerprint" in codex else []),
            *([f"runtime-native-scope:{codex['native_scope']}"] if "native_scope" in codex else []),
        ],
    }
    trial_profiles = {"reviewer": reviewer}
    if workflow == "issue-delivery":
        implementer = {
            "id": "managed-claude-implementer",
            "harness": "claude",
            "model_family": next((f"claude-{family}" for family in ("sonnet", "opus", "haiku") if family in claude_model.lower()), "claude"),
            "model_id": claude_model,
            "effort": {"value": claude_effort, "transport": "native"},
            "required_tools": ["read", "write"],
            "permission_policy": "workspace_write",
            "account_pool_id": "claude-subscription",
            "billing_mode": "subscription",
            "quality_status": "trial",
            "evidence_refs": ["verified-claude-cli-2.1.220"],
        }
        trial_profiles = {"implementer": implementer, **trial_profiles}
    profiles = []
    bindings = {}
    overrides = {}
    roles = {}
    for role, profile in trial_profiles.items():
        # A catalog/default/pin change must not reuse a concrete profile ID
        # with different bytes or overwrite an approved incumbent.
        digest = hashlib.sha256(canonical_json(profile).encode()).hexdigest()
        profile["id"] += f"-{digest[:16]}"
        profiles.append(profile)
        alias = NORMAL_ALIASES[role]
        approved = role_bindings.get(role)
        if approved is not None:
            if (not isinstance(approved, dict) or approved.get("alias") != alias
                    or type(approved.get("version")) is not int or approved["version"] < 1):
                raise ContractError("normal role binding identity is invalid")
            incumbent = json.loads(canonical_json(approved.get("profile")))
            validate_profile(incumbent)
            if (incumbent["harness"] != profile["harness"]
                    or incumbent["permission_policy"] != profile["permission_policy"]
                    or incumbent["billing_mode"] != "subscription"
                    or incumbent["quality_status"] != "proven"):
                raise ContractError("normal role binding exceeds the supported role contract")
            if role == "reviewer" and "catalog_fingerprint" in codex and (
                incumbent["account_pool_id"] != codex["account_pool_id"]
                or f"runtime-catalog-fingerprint:{codex['catalog_fingerprint']}" not in incumbent["evidence_refs"]
                or ("native_scope" in codex and f"runtime-native-scope:{codex['native_scope']}" not in incumbent["evidence_refs"])
            ):
                raise ContractError("approved reviewer requires requalification for the current native account/config/catalog")
            if incumbent["id"] == profile["id"] and incumbent != profile:
                raise ContractError("normal role binding conflicts with the trial profile")
            if incumbent["id"] != profile["id"]:
                profiles.append(incumbent)
            bindings[alias] = {"profile_id": incumbent["id"], "version": approved["version"]}
        else:
            bindings[alias] = {"profile_id": profile["id"], "version": 1}
        roles[role] = [{"kind": "alias", "id": alias}]
        if role in pinned_roles:
            overrides[role] = {"profile_id": profile["id"], "fallback": "none"}
    account_pools = {
        profile["account_pool_id"]: {
            "allowed_billing_modes": ["subscription"],
            "max_concurrency": 1,
            "unknown_capacity_policy": "allow_bounded",
        }
        for profile in profiles
    }
    return {
        "profiles": {
            "schema_version": 1,
            "profiles": profiles,
            "bindings": bindings,
        },
        "policy": {
            "schema_version": 1,
            **NORMAL_POLICY,
            "roles": roles,
            "task_classes": {
                "managed-review" if workflow == "branch-review"
                else "managed-fix": "trial",
            },
            "require_different_model_for_review": True,
            "prefer_different_harness_for_review": True,
            "account_pools": account_pools,
            "experiment_budget": {},
            "decision_helper": {"schema_version": 1, "mode": "off"},
        },
        **({"overrides": overrides} if overrides else {}),
    }


def _tree_entry(repo: Path, oid: str, relative: str) -> tuple[str, str] | None:
    """Return (mode, type) for a path in the exact oid tree, or None if absent."""
    output = _git(repo, "ls-tree", oid, "--", relative)
    if not output:
        return None
    meta, _, _ = output.splitlines()[0].partition("\t")
    parts = meta.split()
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def _is_regular_blob(repo: Path, oid: str, relative: str) -> bool:
    entry = _tree_entry(repo, oid, relative)
    return entry is not None and entry[1] == "blob" and entry[0] != "120000"


def _is_tree(repo: Path, oid: str, relative: str) -> bool:
    entry = _tree_entry(repo, oid, relative)
    return entry is not None and entry[1] == "tree"


def _git_entries_z(repo: Path, *arguments: str) -> list[str]:
    """Run git and split NUL-delimited output, preserving embedded tabs/newlines."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *arguments],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContractError("cannot inspect the Git project") from exc
    if completed.returncode != 0:
        raise ContractError(f"Git project check failed: {' '.join(arguments[:2])}")
    return [
        entry.decode("utf-8", errors="surrogateescape")
        for entry in completed.stdout.split(b"\0")
        if entry
    ]


def _has_python_tests(repo: Path, oid: str, relative: str) -> bool:
    """Return True if a real tracked tree at `relative` holds a regular test*.py file."""
    if not _is_tree(repo, oid, relative):
        return False
    for entry in _git_entries_z(repo, "ls-tree", "-r", "-z", oid, "--", relative):
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) < 2:
            continue
        mode, object_type = parts[0], parts[1]
        if object_type != "blob" or mode == "120000":
            continue
        name = PurePosixPath(path).name
        if name.startswith("test") and name.endswith(".py"):
            return True
    return False


def _detect_tests(repo: Path, oid: str) -> list[tuple[str, ...]]:
    detected: list[tuple[str, ...]] = []
    if _is_regular_blob(repo, oid, "test/run.sh"):
        detected.append(("bash", "test/run.sh"))
    elif _has_python_tests(repo, oid, "tests"):
        detected.append(("python3", "-m", "unittest", "discover", "-s", "tests"))
    elif _has_python_tests(repo, oid, "test"):
        detected.append(("python3", "-m", "unittest", "discover", "-s", "test"))
    if (
        _is_tree(repo, oid, "plugin/core/src")
        and _is_tree(repo, oid, "test/core")
        and _is_regular_blob(repo, oid, "scripts/run-core-tests.py")
    ):
        detected.append((
            "env",
            "PYTHONPATH=plugin/core/src:test/core",
            "PYTHONWARNINGS=error::ResourceWarning",
            "python3",
            "scripts/run-core-tests.py",
        ))
        if _is_regular_blob(repo, oid, "scripts/generate-core-reference.py"):
            detected.append(("python3", "scripts/generate-core-reference.py", "--check"))
    return detected


def _checks(
    repo: Path,
    *,
    workflow: str,
    base_oid: str,
    target_oid: str,
    supplied: tuple[tuple[str, ...], ...],
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    required = workflow == "issue-delivery"
    checks: list[dict[str, Any]] = [{
        "id": "candidate-diff-check",
        "argv": (
            ["git", "diff", "--check", base_oid, target_oid, "--"]
            if workflow == "branch-review"
            else ["git", "diff", "--check", base_oid, "HEAD", "--"]
        ),
        "cwd": ".",
        "timeout_seconds": min(timeout_seconds, 120),
        "required_to_pass": required,
    }]
    seen: set[tuple[str, ...]] = {tuple(checks[0]["argv"])}
    for argv in _detect_tests(repo, target_oid):
        check_id = (
            "generated-core-reference" if argv == ("python3", "scripts/generate-core-reference.py", "--check")
            else "detected-core-tests" if argv[-1] == "scripts/run-core-tests.py"
            else "detected-tests"
        )
        checks.append({
            "id": check_id,
            "argv": list(argv),
            "cwd": ".",
            "timeout_seconds": timeout_seconds,
            "required_to_pass": required,
        })
        seen.add(argv)
    index = 0
    for arguments in supplied:
        if arguments in seen:
            continue
        index += 1
        checks.append({
            "id": f"user-check-{index}",
            "argv": list(arguments),
            "cwd": ".",
            "timeout_seconds": timeout_seconds,
            "required_to_pass": required,
        })
        seen.add(arguments)
    if len(checks) > 16:
        raise ContractError("normal entry produced too many checks")
    return checks


def build_managed_task(
    *,
    workflow: str,
    project_dir: str | Path,
    base_ref: str,
    target_ref: str,
    goal: str,
    codex_identity: dict[str, str],
    write_paths: Iterable[str] = (),
    checks: tuple[tuple[str, ...], ...] = (),
    check_timeout: int = 600,
    review_mode: str = "standard",
    review_focus: str | None = None,
    claude_model: str = "sonnet",
    claude_effort: str = "high",
    role_bindings: dict[str, Any] | None = None,
    pinned_roles: Iterable[str] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    if workflow not in {"branch-review", "issue-delivery"}:
        raise ContractError("normal entry workflow is unsupported")
    if role_bindings is not None and not isinstance(role_bindings, dict):
        raise ContractError("normal role bindings must be an object")
    pins = set(pinned_roles)
    if not isinstance(goal, str) or not goal.strip():
        raise ContractError("goal must be non-empty")
    if type(check_timeout) is not int or not 1 <= check_timeout <= 3600:
        raise ContractError("check timeout must be between 1 and 3600 seconds")
    if review_mode not in {"standard", "adversarial"}:
        raise ContractError("review mode must be standard or adversarial")
    if review_focus is not None:
        if review_mode != "adversarial":
            raise ContractError("review focus requires adversarial mode")
        if not isinstance(review_focus, str) or not review_focus.strip():
            raise ContractError("review focus must be non-empty")
    required_identity = {
        "harness", "harness_version", "model_id", "model_family", "effort",
    }
    if (not isinstance(codex_identity, dict)
            or set(codex_identity) - (required_identity | {"account_pool_id", "native_scope", "catalog_fingerprint"})
            or required_identity - set(codex_identity)
            or codex_identity.get("harness") != "codex"
            or not all(
                isinstance(codex_identity[field], str)
                and codex_identity[field]
                for field in required_identity - {"harness"}
            )):
        raise ContractError("Codex reviewer identity is invalid")
    if workflow == "issue-delivery" and (
        not isinstance(claude_model, str) or not claude_model
        or not isinstance(claude_effort, str) or not claude_effort
    ):
        raise ContractError("Claude model and effort must be non-empty")
    repo = resolve_repository(project_dir)
    base_oid = _resolve_commit(repo, base_ref)
    target_oid = _resolve_commit(repo, target_ref)
    normalized_writes = tuple(
        dict.fromkeys(_relative_path(path, "write path") for path in write_paths)
    )
    if workflow == "branch-review" and normalized_writes:
        raise ContractError("review entry cannot declare write paths")
    if workflow == "issue-delivery" and not normalized_writes:
        normalized_writes = (".",)
    routing = _managed_routing(
        workflow,
        codex_identity,
        claude_model=claude_model,
        claude_effort=claude_effort,
        role_bindings={} if role_bindings is None else role_bindings,
        pinned_roles=pins,
    )
    if pins - set(routing["policy"]["roles"]):
        raise ContractError("normal pin names an unsupported role")
    task_class = "managed-review" if workflow == "branch-review" else "managed-fix"
    task: dict[str, Any] = {
        "schema_version": 1,
        "project": {
            "repo_path": str(repo),
            "base_ref": base_oid,
            "target_ref": target_oid,
        },
        "workflow": workflow,
        "goal": goal.strip(),
        "task_class": task_class,
        "acceptance": [
            {
                "id": "bounded-goal",
                "description": (
                    "Review findings are bound to the exact base and target commits."
                    if workflow == "branch-review"
                    else f"The candidate addresses this bounded issue: {goal.strip()}"
                ),
                "evidence_kind": "review",
            },
            {
                "id": "declared-checks",
                "description": "Every declared check result is retained in the receipt.",
                "evidence_kind": "check",
            },
        ],
        "checks": _checks(
            repo,
            workflow=workflow,
            base_oid=base_oid,
            target_oid=target_oid,
            supplied=checks,
            timeout_seconds=check_timeout,
        ),
        "scope": {
            "read_paths": ["."],
            "write_paths": list(normalized_writes),
        },
        "lead": {"mode": "host"},
        "routing": routing,
        "budget": {
            "wall_seconds": 900 if workflow == "branch-review" else 1800,
            "max_worker_invocations": 1 if workflow == "branch-review" else 7,
            "max_revisions": 0 if workflow == "branch-review" else 2,
            "max_fallbacks_per_step": 0,
        },
        "origin": {"surface": "cli-normal-entry"},
        "review": {"mode": review_mode},
    }
    if review_focus is not None:
        task["review"]["focus"] = review_focus.strip()
    validate_task(task, require_existing_repo=True)
    task_sha256 = hashlib.sha256(canonical_json(task).encode()).hexdigest()
    profiles_by_id = {p["id"]: p for p in routing["profiles"]["profiles"]}
    roles = {}
    for role in routing["policy"]["roles"]:
        alias = NORMAL_ALIASES[role]
        override = routing.get("overrides", {}).get(role)
        profile = profiles_by_id[(override or routing["profiles"]["bindings"][alias])["profile_id"]]
        roles[role] = {
            "profile_id": profile["id"],
            "harness": profile["harness"],
            "model_id": profile["model_id"],
            "effort": profile["effort"]["value"],
            "permission": profile["permission_policy"],
            "alias": alias,
            "selection_mode": "pinned" if override else "approved_alias" if profile["quality_status"] == "proven" else "bounded_trial",
        }
    return task, {
        "workflow": workflow,
        "task_sha256": task_sha256,
        "project": str(repo),
        "base_oid": base_oid,
        "target_oid": target_oid,
        "planned_roles": roles,
        "selection_reason": (
            "stable policy aliases; explicit pins are fixed, approved incumbents "
            "are retained, otherwise a bounded trial is used; "
            "different-harness review is mandatory for delivery"
        ),
        "scope": task["scope"],
        "checks": [check["id"] for check in task["checks"]],
        "check_plan": task["checks"],
    }
