"""Frozen Git input and isolated review-workspace preparation for M3."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import subprocess
from typing import Iterable

from .contracts import ContractError
from .store import canonical_json, git_common_dir


def _git(repo: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise ContractError("Git is unavailable while preparing the review workspace") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise ContractError(f"Git workspace operation failed: {detail or args[0]}")
    return result.stdout


def resolve_commit(repo: Path, ref: str) -> str:
    if not isinstance(ref, str) or not ref:
        raise ContractError("Git ref must be a non-empty string")
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", f"{ref}^{{commit}}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise ContractError("Git is unavailable while resolving a commit") from exc
    if result.returncode != 0:
        raise ContractError(f"Git ref does not resolve to a commit: {ref}")
    oid = result.stdout.decode().strip()
    if len(oid) != 40 or any(character not in "0123456789abcdef" for character in oid):
        raise ContractError(f"Git ref did not resolve to a full commit OID: {ref}")
    return oid


def _decode_paths(payload: bytes, label: str) -> list[str]:
    values = payload.split(b"\0")
    if values and values[-1] == b"":
        values.pop()
    result = []
    for raw in values:
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractError(f"{label} contains a non-UTF-8 Git path") from exc
        if not value:
            raise ContractError(f"{label} contains an empty Git path")
        result.append(value)
    return result


def dirty_paths(repo: Path) -> tuple[str, ...]:
    paths = set()
    for args in (
        ("diff", "--no-renames", "--name-only", "-z", "--"),
        ("diff", "--cached", "--no-renames", "--name-only", "-z", "--"),
        ("ls-files", "--others", "--exclude-standard", "-z", "--"),
    ):
        paths.update(_decode_paths(_git(repo, *args), "dirty inventory"))
    return tuple(sorted(paths))


def _normalized_relative(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{label} must be a non-empty repository-relative path")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ContractError(f"{label} must be repository-relative without traversal")
    normalized = candidate.as_posix()
    return "." if normalized in {"", "."} else normalized.rstrip("/")


def repo_relative_config(repo: Path, configured: str, label: str) -> str:
    if not isinstance(configured, str) or not configured:
        raise ContractError(f"{label} must be a non-empty path")
    source = Path(configured)
    if source.is_absolute():
        try:
            return source.resolve().relative_to(repo.resolve()).as_posix()
        except ValueError as exc:
            raise ContractError(f"{label} escapes project") from exc
    return _normalized_relative(configured, label)


def _intersects(path: str, scope: str) -> bool:
    return scope == "." or path == scope or path.startswith(f"{scope}/")


def assert_clean_inputs(
    repo: Path,
    scope_paths: Iterable[str],
    required_paths: Iterable[str] = (),
) -> None:
    scopes = tuple(_normalized_relative(path, "scope path") for path in scope_paths)
    required = tuple(
        _normalized_relative(path, "required committed path") for path in required_paths
    )
    intersections = [
        path for path in dirty_paths(repo)
        if path in required or any(_intersects(path, scope) for scope in scopes)
    ]
    if intersections:
        raise ContractError(
            "committed-input mode rejects dirty scoped/config paths: "
            + ", ".join(intersections)
        )


def committed_regular_file(repo: Path, commit_oid: str, relative_path: str) -> bytes:
    relative = _normalized_relative(relative_path, "committed file path")
    listing = _git(repo, "ls-tree", "-z", commit_oid, "--", relative)
    records = [record for record in listing.split(b"\0") if record]
    if len(records) != 1 or b"\t" not in records[0]:
        raise ContractError(f"committed config is missing or ambiguous: {relative}")
    metadata, raw_name = records[0].split(b"\t", 1)
    fields = metadata.split()
    if len(fields) != 3 or fields[0] not in {b"100644", b"100755"} or fields[1] != b"blob":
        raise ContractError(f"committed config must be a regular file: {relative}")
    try:
        stored_name = raw_name.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError("committed config path is not UTF-8") from exc
    if stored_name != relative:
        raise ContractError(f"committed config path mismatch: {relative}")
    return _git(repo, "show", f"{commit_oid}:{relative}")


def _validate_segment(value: str, label: str) -> str:
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or "/" in value or "\\" in value or "\0" in value or os.sep in value):
        raise ContractError(f"{label} is not a safe path segment")
    return value


def _validate_workspace(
    source_repo: Path, workspace: Path, target_oid: str, scope_paths: Iterable[str],
) -> None:
    try:
        resolved = workspace.resolve(strict=True)
    except OSError as exc:
        raise ContractError("review workspace does not exist") from exc
    top = Path(_git(resolved, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if top != resolved:
        raise ContractError("review workspace top level does not match its run-owned path")
    if git_common_dir(resolved) != git_common_dir(source_repo):
        raise ContractError("review workspace belongs to a different Git project")
    if resolve_commit(resolved, "HEAD") != target_oid:
        raise ContractError("existing review workspace targets a different commit")
    if _git(resolved, "rev-parse", "--abbrev-ref", "HEAD").strip() != b"HEAD":
        raise ContractError("review workspace must use detached HEAD")
    if dirty_paths(resolved):
        raise ContractError("existing review workspace is dirty")

    for scope in scope_paths:
        normalized = _normalized_relative(scope, "scope path")
        candidate = resolved if normalized == "." else resolved / normalized
        try:
            destination = candidate.resolve(strict=False)
        except OSError as exc:
            raise ContractError(f"scope path cannot be resolved: {normalized}") from exc
        if destination != resolved and resolved not in destination.parents:
            raise ContractError(f"scope path escapes the review workspace: {normalized}")
        symlinks = _git(resolved, "ls-tree", "-r", "-z", "HEAD", "--", normalized)
        for record in (item for item in symlinks.split(b"\0") if item):
            metadata, raw_name = record.split(b"\t", 1)
            if metadata.split()[0] != b"120000":
                continue
            try:
                name = raw_name.decode("utf-8")
                destination = (resolved / name).resolve(strict=True)
            except (UnicodeDecodeError, OSError) as exc:
                raise ContractError("scoped symlink is invalid or broken") from exc
            if destination != resolved and resolved not in destination.parents:
                raise ContractError(f"scoped symlink escapes the review workspace: {name}")


def _prepare_detached_workspace(
    source_repo: Path,
    runtime: Path,
    project_id: str,
    run_id: str,
    target_oid: str,
    scope_paths: Iterable[str],
    name: str,
) -> tuple[Path, tuple[str, ...]]:
    repo = source_repo.resolve(strict=True)
    scopes = tuple(_normalized_relative(path, "scope path") for path in scope_paths)
    project = _validate_segment(project_id, "project id")
    run = _validate_segment(run_id, "run id")
    workspace = (
        runtime.resolve() / "projects" / project / "runs" / run / name
    )
    workspace.parent.mkdir(parents=True, exist_ok=True)
    if not workspace.exists():
        try:
            result = subprocess.run(
                ["git", "-C", str(repo), "worktree", "add", "--detach", "--quiet",
                 str(workspace), target_oid],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError as exc:
            raise ContractError(
                "Git is unavailable while creating the frozen review workspace"
            ) from exc
        if result.returncode != 0 and not workspace.exists():
            detail = result.stderr.decode("utf-8", "replace").strip()
            label = name.replace("-", " ")
            raise ContractError(f"could not create frozen {label}: {detail}")
    _validate_workspace(repo, workspace, target_oid, scopes)
    return workspace.resolve(), scopes


def prepare_review_workspace(
    source_repo: Path,
    runtime: Path,
    project_id: str,
    run_id: str,
    base_oid: str,
    target_oid: str,
    scope_paths: Iterable[str],
    *,
    required_clean_paths: Iterable[str] = (),
) -> dict[str, object]:
    """Create or validate one detached, run-owned worktree at the target commit."""
    repo = source_repo.resolve(strict=True)
    scopes = tuple(_normalized_relative(path, "scope path") for path in scope_paths)
    assert_clean_inputs(repo, scopes, required_clean_paths)
    workspace, scopes = _prepare_detached_workspace(
        repo, runtime, project_id, run_id, target_oid, scopes, "review-worktree",
    )
    changed = _decode_paths(
        _git(
            repo, "diff", "--no-renames", "--name-only", "-z",
            base_oid, target_oid, "--",
        ),
        "candidate diff",
    )
    identity = {
        "schema_version": 1,
        "base_oid": base_oid,
        "target_oid": target_oid,
        "changed_paths": sorted(changed),
    }
    return {
        **identity,
        "path": str(workspace.resolve()),
        "candidate_sha256": hashlib.sha256(canonical_json(identity).encode()).hexdigest(),
        "scope": list(scopes),
    }


def prepare_check_workspace(
    source_repo: Path,
    runtime: Path,
    project_id: str,
    run_id: str,
    target_oid: str,
    scope_paths: Iterable[str],
    *,
    required_clean_paths: Iterable[str] = (),
) -> dict[str, object]:
    """Create an independent candidate worktree for trusted declared checks."""
    repo = source_repo.resolve(strict=True)
    scopes = tuple(_normalized_relative(path, "scope path") for path in scope_paths)
    assert_clean_inputs(repo, scopes, required_clean_paths)
    workspace, scopes = _prepare_detached_workspace(
        repo, runtime, project_id, run_id, target_oid, scopes, "check-worktree",
    )
    return {
        "schema_version": 1,
        "path": str(workspace),
        "target_oid": target_oid,
        "scope": list(scopes),
    }
