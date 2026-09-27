"""Frozen Git input and isolated review-workspace preparation for M3."""

from __future__ import annotations

import hashlib
import fcntl
import os
from pathlib import Path, PurePosixPath
import subprocess
from typing import Iterable

from .contracts import ContractError
from .store import canonical_json, git_common_dir


MAX_CANDIDATE_PATCH_BYTES = 16 * 1024 * 1024


def _git(
    repo: Path,
    *args: str,
    environment: dict[str, str] | None = None,
) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=(None if environment is None else {**os.environ, **environment}),
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
    source_repo: Path,
    workspace: Path,
    target_oid: str,
    scope_paths: Iterable[str],
    *,
    require_clean: bool = True,
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
    if require_clean and dirty_paths(resolved):
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


def reset_check_workspace(
    review_workspace: Path,
    check_workspace: Path,
    target_oid: str,
    scope_paths: Iterable[str],
) -> None:
    """Reset only the exact run-owned check worktree before another check pass."""
    review = review_workspace.resolve(strict=True)
    checks = check_workspace.resolve(strict=True)
    review_prefix = "review-worktree"
    if not review.name.startswith(review_prefix):
        raise ContractError("check workspace is not the review run's owned sibling")
    suffix = review.name[len(review_prefix):]
    if checks != review.parent / f"check-worktree{suffix}":
        raise ContractError("check workspace is not the review run's owned sibling")
    _validate_workspace(review, review, target_oid, scope_paths)
    _validate_workspace(
        review, checks, target_oid, scope_paths, require_clean=False,
    )
    _git(checks, "reset", "--hard", target_oid)
    _git(checks, "clean", "-ffdx")
    _validate_workspace(review, checks, target_oid, scope_paths)


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
    name = _validate_segment(name, "workspace name")
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
    candidate_sha256: str | None = None,
    workspace_name: str = "review-worktree",
) -> dict[str, object]:
    """Create or validate one detached, run-owned worktree at the target commit."""
    repo = source_repo.resolve(strict=True)
    scopes = tuple(_normalized_relative(path, "scope path") for path in scope_paths)
    assert_clean_inputs(repo, scopes, required_clean_paths)
    workspace, scopes = _prepare_detached_workspace(
        repo, runtime, project_id, run_id, target_oid, scopes, workspace_name,
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
    computed_candidate = hashlib.sha256(canonical_json(identity).encode()).hexdigest()
    if candidate_sha256 is not None:
        if (not isinstance(candidate_sha256, str)
                or len(candidate_sha256) != 64
                or any(character not in "0123456789abcdef" for character in candidate_sha256)):
            raise ContractError("candidate SHA-256 override is invalid")
        computed_candidate = candidate_sha256
    return {
        **identity,
        "path": str(workspace.resolve()),
        "candidate_sha256": computed_candidate,
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
    workspace_name: str = "check-worktree",
) -> dict[str, object]:
    """Create an independent candidate worktree for trusted declared checks."""
    repo = source_repo.resolve(strict=True)
    scopes = tuple(_normalized_relative(path, "scope path") for path in scope_paths)
    assert_clean_inputs(repo, scopes, required_clean_paths)
    workspace, scopes = _prepare_detached_workspace(
        repo, runtime, project_id, run_id, target_oid, scopes, workspace_name,
    )
    return {
        "schema_version": 1,
        "path": str(workspace),
        "target_oid": target_oid,
        "scope": list(scopes),
    }


def prepare_delivery_workspace(
    source_repo: Path,
    runtime: Path,
    project_id: str,
    run_id: str,
    target_oid: str,
    read_paths: Iterable[str],
    write_paths: Iterable[str],
    *,
    required_clean_paths: Iterable[str] = (),
) -> dict[str, object]:
    """Create or validate one detached, run-owned implementation worktree."""
    repo = source_repo.resolve(strict=True)
    reads = tuple(_normalized_relative(path, "read scope path") for path in read_paths)
    writes = tuple(
        _normalized_relative(path, "write scope path") for path in write_paths
    )
    if not writes:
        raise ContractError("delivery workspace requires a non-empty write scope")
    scopes = tuple(dict.fromkeys((*reads, *writes)))
    assert_clean_inputs(repo, scopes, required_clean_paths)
    workspace, _ = _prepare_detached_workspace(
        repo, runtime, project_id, run_id, target_oid, scopes,
        "delivery-worktree",
    )
    return {
        "schema_version": 1,
        "path": str(workspace),
        "baseline_oid": target_oid,
        "read_scope": list(reads),
        "write_scope": list(writes),
    }


def _assert_delivery_path_scope(
    workspace: Path,
    changed_paths: Iterable[str],
    write_paths: Iterable[str],
) -> tuple[str, ...]:
    writes = tuple(
        _normalized_relative(path, "write scope path") for path in write_paths
    )
    changed = tuple(sorted(dict.fromkeys(changed_paths)))
    outside = [
        path for path in changed
        if not any(_intersects(path, scope) for scope in writes)
    ]
    if outside:
        raise ContractError(
            "delivery candidate changes paths outside write scope: "
            + ", ".join(outside)
        )
    root = workspace.resolve(strict=True)
    for relative in changed:
        normalized = _normalized_relative(relative, "candidate path")
        candidate = root / normalized
        probe = candidate if candidate.exists() or candidate.is_symlink() else candidate.parent
        try:
            resolved = probe.resolve(strict=False)
        except OSError as exc:
            raise ContractError(
                f"delivery candidate path cannot be resolved: {normalized}"
            ) from exc
        if resolved != root and root not in resolved.parents:
            raise ContractError(
                f"delivery candidate path escapes its workspace: {normalized}"
            )
    return changed


def _candidate_snapshot(
    workspace: Path,
    baseline_oid: str,
    commit_oid: str,
    write_paths: Iterable[str],
) -> tuple[dict[str, object], bytes]:
    changed = _decode_paths(
        _git(
            workspace,
            "diff", "--no-renames", "--name-only", "-z",
            baseline_oid, commit_oid, "--",
        ),
        "delivery candidate diff",
    )
    changed_paths = _assert_delivery_path_scope(workspace, changed, write_paths)
    if not changed_paths:
        raise ContractError("delivery candidate contains no changes")
    captured_untracked = _decode_paths(
        _git(
            workspace,
            "diff", "--no-renames", "--diff-filter=A", "--name-only", "-z",
            baseline_oid, commit_oid, "--",
        ),
        "delivery added-path inventory",
    )
    patch = _git(
        workspace,
        "diff", "--binary", "--no-ext-diff", baseline_oid, commit_oid, "--",
    )
    if len(patch) > MAX_CANDIDATE_PATCH_BYTES:
        raise ContractError("delivery candidate patch exceeds its byte limit")
    tree_oid = _git(
        workspace, "rev-parse", "--verify", f"{commit_oid}^{{tree}}",
    ).decode().strip()
    if (len(tree_oid) != 40
            or any(character not in "0123456789abcdef" for character in tree_oid)):
        raise ContractError("delivery candidate tree did not resolve to a full OID")
    identity = {
        "schema_version": 1,
        "baseline_oid": baseline_oid,
        "commit_oid": commit_oid,
        "tree_oid": tree_oid,
        "patch_sha256": hashlib.sha256(patch).hexdigest(),
        "changed_paths": list(changed_paths),
    }
    return {
        **identity,
        "candidate_sha256": hashlib.sha256(
            canonical_json(identity).encode()
        ).hexdigest(),
        "patch_bytes": len(patch),
        "captured_untracked_paths": sorted(captured_untracked),
    }, patch


def _freeze_delivery_candidate_unlocked(
    source_repo: Path,
    workspace: Path,
    baseline_oid: str,
    write_paths: Iterable[str],
    run_id: str,
    parent_oid: str | None = None,
) -> tuple[dict[str, object], bytes]:
    """Commit one scoped candidate locally and return its stable patch identity."""
    repo = source_repo.resolve(strict=True)
    delivery = workspace.resolve(strict=True)
    run = _validate_segment(run_id, "run id")
    if delivery.name != "delivery-worktree":
        raise ContractError("delivery workspace is not a run-owned delivery worktree")
    expected_parent = parent_oid or baseline_oid
    expected_parent = resolve_commit(delivery, expected_parent)
    head_oid = resolve_commit(delivery, "HEAD")
    _validate_workspace(
        repo,
        delivery,
        head_oid,
        write_paths,
        require_clean=False,
    )
    dirties = dirty_paths(delivery)
    if head_oid != expected_parent:
        if dirties:
            raise ContractError("frozen delivery candidate has later workspace changes")
        parents = _git(
            delivery, "rev-list", "--parents", "-n", "1", head_oid,
        ).decode().strip().split()
        if parents != [head_oid, expected_parent]:
            raise ContractError("delivery candidate is not a single local parent commit")
        marker = _git(
            delivery, "show", "-s", "--format=%s%x00%ae", head_oid,
        ).decode("utf-8", "strict").rstrip("\n").split("\0")
        if marker != [f"DevSquad candidate {run}", "candidate@devsquad.local"]:
            raise ContractError("delivery candidate commit is not coordinator-owned")
        return _candidate_snapshot(
            delivery, baseline_oid, head_oid, write_paths,
        )

    changed_paths = _assert_delivery_path_scope(delivery, dirties, write_paths)
    if not changed_paths:
        raise ContractError("delivery candidate contains no changes")
    _git(delivery, "add", "-A", "--", ".")
    staged = _decode_paths(
        _git(
            delivery, "diff", "--cached", "--no-renames", "--name-only", "-z",
            "--",
        ),
        "staged delivery candidate",
    )
    _assert_delivery_path_scope(delivery, staged, write_paths)
    staged_patch = _git(
        delivery, "diff", "--cached", "--binary", "--no-ext-diff", "--",
    )
    if len(staged_patch) > MAX_CANDIDATE_PATCH_BYTES:
        raise ContractError("delivery candidate patch exceeds its byte limit")
    commit_environment = {
        "GIT_AUTHOR_NAME": "DevSquad Candidate",
        "GIT_AUTHOR_EMAIL": "candidate@devsquad.local",
        "GIT_COMMITTER_NAME": "DevSquad Candidate",
        "GIT_COMMITTER_EMAIL": "candidate@devsquad.local",
    }
    _git(
        delivery,
        "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgSign=false",
        "commit", "--quiet", "--no-verify", "--no-gpg-sign",
        "-m", f"DevSquad candidate {run}",
        environment=commit_environment,
    )
    commit_oid = resolve_commit(delivery, "HEAD")
    snapshot, patch = _candidate_snapshot(
        delivery, baseline_oid, commit_oid, write_paths,
    )
    committed_delta = _git(
        delivery,
        "diff", "--binary", "--no-ext-diff", expected_parent, commit_oid, "--",
    )
    if committed_delta != staged_patch:
        raise ContractError("committed delivery patch differs from the staged candidate")
    if dirty_paths(delivery):
        raise ContractError("delivery workspace remained dirty after candidate commit")
    return snapshot, patch


def freeze_delivery_candidate(
    source_repo: Path,
    workspace: Path,
    baseline_oid: str,
    write_paths: Iterable[str],
    run_id: str,
    *,
    parent_oid: str | None = None,
) -> tuple[dict[str, object], bytes]:
    """Serialize candidate freezing so competing recovery importers replay it."""
    delivery = workspace.resolve(strict=True)
    lock_path = delivery.parent / ".candidate-finalize.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return _freeze_delivery_candidate_unlocked(
            source_repo, delivery, baseline_oid, write_paths, run_id, parent_oid,
        )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
