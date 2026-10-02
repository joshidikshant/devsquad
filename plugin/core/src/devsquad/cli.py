"""Versioned JSON command surface for local DevSquad operations."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .adapters import AdapterManifest, classify_cli, harness_version, prepare_cli, prepare_native_codex_from_catalog
from .contracts import ContractError, envelope, error_payload
from .diagnostics import build_doctor_report
from .integrations import LocalIntegrationManager, load_integrations
from .service import Service
from .store import ConflictError, SchemaVersionError
from .task_entry import (
    build_managed_task,
    discover_codex_identity,
    parse_checks,
    resolve_repository,
)

SOURCE_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = SOURCE_ROOT if (SOURCE_ROOT / "adapters").is_dir() else Path(sys.prefix) / "share" / "devsquad"
WAIT_POLL_SECONDS = 0.25
WAIT_EXIT_CODES = {
    "succeeded": 0,
    "blocked": 2,
    "awaiting_host": 2,
    "failed": 3,
    "cancelled": 4,
}
WAIT_ACTIVE_STATES = {"queued", "running", "cancelling"}


def manifests() -> list[tuple[Path, AdapterManifest]]:
    return [(path, AdapterManifest.load(path)) for path in sorted((CORE_ROOT / "adapters").glob("*/adapter.json"))]


def command_doctor(args: argparse.Namespace) -> tuple[dict, int]:
    report = build_doctor_report(
        project=Path(args.project_dir),
        squad_executable=(
            Path(args.squad_executable) if args.squad_executable else None
        ),
    )
    return envelope(data=report), 0 if report["ready"] else 1


def command_setup(args: argparse.Namespace) -> tuple[dict, int]:
    templates = load_integrations()
    selected = set(args.host or (template.id for template in templates))
    manager = LocalIntegrationManager(
        project=Path(args.project_dir),
        squad_executable=(
            Path(args.squad_executable) if args.squad_executable else None
        ),
    )
    rows = [
        manager.setup(template, dry_run=args.dry_run)
        for template in templates
        if template.id in selected
    ]
    successful_actions = {
        "added", "updated", "unchanged", "would_add", "would_update",
    }
    completed = all(row["action"] in successful_actions for row in rows)
    ready = all(row["ready"] for row in rows)
    return envelope(data={
        "completed": completed,
        "ready": ready,
        "dry_run": args.dry_run,
        "hosts": rows,
    }), 0 if completed else 1


def _read_json(path: str, label: str) -> Any:
    def object_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate key: {key}")
            value[key] = item
        return value

    def reject_constant(value):
        raise ValueError(f"non-finite number: {value}")

    try:
        return json.loads(
            Path(path).read_text(),
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ContractError(f"cannot read {label}: {exc}") from exc


def command_prepare(args: argparse.Namespace) -> tuple[dict, int]:
    manifest = AdapterManifest.load(CORE_ROOT / "adapters" / args.adapter / "adapter.json")
    transport = args.transport or manifest.transport
    if transport == "native_protocol":
        if manifest.name != "codex" or not args.catalog_file or not args.model or not args.effort:
            raise ContractError("native preparation requires Codex, --catalog-file, --model and --effort")
        binary = manifest.resolve_binary()
        version = harness_version(binary) if binary else None
        snapshot = _read_json(args.catalog_file, "catalog file")
        spec = prepare_native_codex_from_catalog(manifest, snapshot, cwd=args.cwd, model=args.model, effort=args.effort, permission=args.permission, timeout_seconds=args.timeout, harness_version_value=version or "unknown")
    elif transport == "cli_exec":
        spec = prepare_cli(manifest, prompt=args.prompt, cwd=args.cwd, model=args.model, effort=args.effort, permission=args.permission, timeout_seconds=args.timeout)
    else:
        raise ContractError(f"unsupported transport: {transport}")
    return envelope(data=spec.to_dict()), 0


def command_classify(args: argparse.Namespace) -> tuple[dict, int]:
    manifest = AdapterManifest.load(CORE_ROOT / "adapters" / args.adapter / "adapter.json")
    spec = prepare_cli(manifest, prompt="classification", cwd=args.cwd, model=args.model, effort=args.effort, permission=args.permission, timeout_seconds=args.timeout)
    try:
        stdout = Path(args.stdout_file).read_text()
        stderr = Path(args.stderr_file).read_text()
    except (OSError, UnicodeError) as exc:
        raise ContractError(f"cannot read classification output: {exc}") from exc
    result = classify_cli(spec, returncode=args.returncode, stdout=stdout, stderr=stderr)
    return envelope(data=result.to_dict()), 0


def _service(args: argparse.Namespace) -> Service:
    return Service(Path(args.runtime_dir))


def _wait_for_run(
    service: Service,
    started: dict[str, Any],
    *,
    resume_candidate_review: bool = False,
) -> tuple[dict[str, Any], int]:
    run_id = started["run_id"]
    resumed_versions: set[int] = set()
    try:
        while True:
            status = service.status(run_id)
            state = status.get("state")
            if state in WAIT_EXIT_CODES:
                return envelope(data=status), WAIT_EXIT_CODES[state]
            if state not in WAIT_ACTIVE_STATES:
                raise RuntimeError(f"service returned unsupported run state: {state!r}")
            version = status.get("version")
            if (resume_candidate_review
                    and state == "queued"
                    and status.get("next_action") == "resume_candidate_review"
                    and type(version) is int
                    and version not in resumed_versions):
                resumed_versions.add(version)
                service.resume(run_id)
            time.sleep(WAIT_POLL_SECONDS)
    except KeyboardInterrupt:
        cancel_command = f"squad cancel {run_id}"
        print(
            f"Stopped observing run {run_id}; the run was not cancelled and remains saved. "
            f"To cancel it explicitly, run: {cancel_command}",
            file=sys.stderr,
        )
        return envelope(data={
            "run_id": run_id,
            "state": started.get("state"),
            "observation_stopped": True,
            "cancelled": False,
            "next_action": cancel_command,
        }), 130


def command_start(args: argparse.Namespace) -> tuple[dict, int]:
    task = _read_json(args.task_file, "task file")
    service = _service(args)
    started = service.start(task, args.idempotency_key, args.supersedes_run)
    if not args.wait:
        return envelope(data=started), 0
    return _wait_for_run(service, started)


def command_trial(args: argparse.Namespace) -> tuple[dict, int]:
    service = _service(args)
    started = service.trial_start(
        _read_json(args.experiment, "experiment file"), args.case, args.arm,
        _read_json(args.task_file, "trial task file"), args.idempotency_key,
    )
    if args.wait:
        return _wait_for_run(service, started, resume_candidate_review=True)
    return envelope(data=started), 0


def _normal_entry_result(
    summary: dict[str, Any],
    idempotency_key: str,
    run: dict[str, Any] | None,
) -> dict[str, Any]:
    run_id = run.get("run_id") if run is not None else None
    state = run.get("state") if run is not None else "not_started"
    if run_id is None:
        next_action = "rerun this command without --dry-run"
    elif state == "succeeded":
        next_action = f"squad result {run_id} --json"
    else:
        next_action = f"squad status {run_id} --json"
    return {
        "dry_run": run is None,
        "run_id": run_id,
        "state": state,
        "service": run,
        "idempotency_key": idempotency_key,
        **summary,
        "next_action": next_action,
    }


def _command_normal_entry(
    args: argparse.Namespace,
    *,
    workflow: str,
) -> tuple[dict, int]:
    if args.dry_run and args.wait:
        raise ContractError("--wait cannot be combined with --dry-run")
    repo = resolve_repository(args.project_dir)
    reviewer_model = args.model if workflow == "branch-review" else args.review_model
    reviewer_effort = args.effort if workflow == "branch-review" else args.review_effort
    pins = tuple(role for role, explicit in (
        ("reviewer", reviewer_model is not None or reviewer_effort is not None),
        ("implementer", workflow == "issue-delivery" and (args.implementer_model is not None or args.implementer_effort is not None)),
    ) if explicit)
    bindings = (
        _service(args).normal_entry_bindings(workflow, pinned_roles=pins)
        if (Path(args.runtime_dir) / "state.sqlite3").is_file() else {}
    )
    incumbent = bindings.get("reviewer", {}).get("profile", {})
    codex_identity = discover_codex_identity(
        repo,
        requested_model=reviewer_model or incumbent.get("model_id"),
        requested_effort=reviewer_effort or incumbent.get("effort", {}).get("value"),
        runtime=Path(args.runtime_dir),
    )
    if workflow == "branch-review":
        mode = args.mode
        focus = args.focus
        goal = (
            f"Review exact target {args.target} against base {args.base}"
            + (f" with adversarial focus on {focus.strip()}" if focus else "")
            + "."
        )
        write_paths: tuple[str, ...] = ()
        claude_model = "sonnet"
        claude_effort = "high"
    else:
        mode = args.review_mode
        focus = args.review_focus
        goal = args.issue
        write_paths = tuple(args.write_path or ())
        incumbent = bindings.get("implementer", {}).get("profile", {})
        claude_model = args.implementer_model or incumbent.get("model_id", "sonnet")
        claude_effort = args.implementer_effort or incumbent.get("effort", {}).get("value", "high")
    task, summary = build_managed_task(
        workflow=workflow,
        project_dir=repo,
        base_ref=args.base,
        target_ref=args.target,
        goal=goal,
        codex_identity=codex_identity,
        write_paths=write_paths,
        checks=parse_checks(args.check),
        check_timeout=args.check_timeout,
        review_mode=mode,
        review_focus=focus,
        claude_model=claude_model,
        claude_effort=claude_effort,
        role_bindings=bindings,
        pinned_roles=pins,
    )
    idempotency_key = args.idempotency_key or (
        f"normal-{workflow}-{summary['task_sha256']}"
    )
    if args.dry_run:
        return envelope(data=_normal_entry_result(
            summary, idempotency_key, None,
        )), 0
    service = _service(args)
    started = service.start(task, idempotency_key, None)
    run, code = (
        _wait_for_run(
            service,
            started,
            resume_candidate_review=(workflow == "issue-delivery"),
        ) if args.wait
        else (envelope(data=started), 0)
    )
    service_data = run["data"]
    return envelope(data=_normal_entry_result(
        summary, idempotency_key, service_data,
    )), code


def command_review(args: argparse.Namespace) -> tuple[dict, int]:
    return _command_normal_entry(args, workflow="branch-review")


def command_fix(args: argparse.Namespace) -> tuple[dict, int]:
    return _command_normal_entry(args, workflow="issue-delivery")


def command_capacity_observe(args: argparse.Namespace) -> tuple[dict, int]:
    observation = _read_json(args.file, "capacity observation file")
    return envelope(data=_service(args).capacity_observe(observation)), 0


def command_outcome_add(args: argparse.Namespace) -> tuple[dict, int]:
    outcome = _read_json(args.file, "outcome file")
    return envelope(data=_service(args).outcome_add(args.run, outcome)), 0


def command_report(args: argparse.Namespace) -> tuple[dict, int]:
    return envelope(data=_service(args).learning_report(args.project)), 0


def command_policy_evaluate(args: argparse.Namespace) -> tuple[dict, int]:
    experiment = _read_json(args.experiment, "experiment file")
    if args.revision_id is not None or args.previous_evaluation_sha256 is not None:
        return envelope(data=_service(args).policy_evaluate(
            experiment, revision_id=args.revision_id,
            previous_evaluation_sha256=args.previous_evaluation_sha256,
        )), 0
    return envelope(data=_service(args).policy_evaluate(experiment)), 0


def command_learn_propose(args: argparse.Namespace) -> tuple[dict, int]:
    return envelope(data=_service(args).learning_propose(args.project)), 0


def command_profile_template_add(args: argparse.Namespace) -> tuple[dict, int]:
    return envelope(data=_service(args).profile_template_add(
        _read_json(args.file, "profile template file"),
    )), 0


def command_profile_binding_bootstrap(args: argparse.Namespace) -> tuple[dict, int]:
    return envelope(data=_service(args).profile_binding_bootstrap(
        _read_json(args.file, "profile binding bootstrap file"),
    )), 0


def command_profile_qualification_add(args: argparse.Namespace) -> tuple[dict, int]:
    return envelope(data=_service(args).profile_qualification_add(
        _read_json(args.file, "profile qualification file"),
    )), 0


def command_profile_binding_change(args: argparse.Namespace) -> tuple[dict, int]:
    return envelope(data=_service(args).profile_binding_change(
        _read_json(args.file, "profile binding change file"),
    )), 0


def command_profile_binding_fallback(args: argparse.Namespace) -> tuple[dict, int]:
    return envelope(data=_service(args).profile_binding_fallback(
        _read_json(args.file, "profile binding fallback file"),
    )), 0


def command_profile_binding_show(args: argparse.Namespace) -> tuple[dict, int]:
    return envelope(data=_service(args).profile_binding_status(args.alias)), 0


def command_status(args: argparse.Namespace) -> tuple[dict, int]: return envelope(data=_service(args).status(args.run)), 0
def command_events(args: argparse.Namespace) -> tuple[dict, int]: return envelope(data=_service(args).events(args.run, args.after, args.limit)), 0
def command_result(args: argparse.Namespace) -> tuple[dict, int]: return envelope(data=_service(args).result(args.run)), 0
def command_cancel(args: argparse.Namespace) -> tuple[dict, int]: return envelope(data=_service(args).cancel(args.run)), 0
def command_resume(args: argparse.Namespace) -> tuple[dict, int]:
    recovery = _read_json(args.recovery_file, "recovery file") if args.recovery_file else None
    return envelope(data=_service(args).resume(args.run, recovery)), 0


def command_handoff_claim(args: argparse.Namespace) -> tuple[dict, int]:
    prior_claim = _read_json(args.claim_file, "claim file") if args.claim_file else None
    return envelope(data=_service(args).handoff_claim(
        args.run, args.expected_version, args.owner, prior_claim,
    )), 0


def command_handoff_complete(args: argparse.Namespace) -> tuple[dict, int]:
    claim = _read_json(args.claim_file, "claim file")
    decision = _read_json(args.decision_file, "decision file")
    return envelope(data=_service(args).handoff_complete(args.run, claim, decision)), 0


def command_mcp_serve(args: argparse.Namespace) -> int:
    # Keep this import inside the explicitly requested command.  Importing the
    # ordinary CLI must remain valid when the optional SDK is absent.
    from .mcp_server import MCPDependencyUnavailable, serve_stdio

    try:
        serve_stdio(
            Path(args.runtime_dir),
            caller_surface=args.surface,
            caller_session_ref=args.session_ref,
        )
    except MCPDependencyUnavailable as exc:
        # stdout is the MCP protocol channel, including during startup.
        print(str(exc), file=sys.stderr)
        return 69
    return 0


class ContractParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ContractError(message)


def parser() -> argparse.ArgumentParser:
    p = ContractParser(prog="squad")
    p.add_argument("--version", action="version", version=f"squad {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--project-dir", default=str(Path.cwd()))
    doctor.add_argument("--squad-executable")
    doctor.set_defaults(func=command_doctor)
    setup = sub.add_parser("setup")
    setup.add_argument(
        "--host",
        action="append",
        choices=("codex", "claude-code", "antigravity", "grok"),
    )
    setup.add_argument("--dry-run", action="store_true")
    setup.add_argument("--json", action="store_true")
    setup.add_argument("--project-dir", default=str(Path.cwd()))
    setup.add_argument("--squad-executable")
    setup.set_defaults(func=command_setup)
    for name, fn in (("prepare", command_prepare), ("classify", command_classify)):
        cmd = sub.add_parser(name)
        cmd.add_argument("adapter", choices=("codex", "antigravity", "grok"))
        cmd.add_argument("--cwd", default=str(Path.cwd()))
        cmd.add_argument("--model")
        cmd.add_argument("--effort")
        cmd.add_argument("--permission", choices=("read_only", "workspace_write"), default="read_only")
        cmd.add_argument("--timeout", type=int, default=90)
        cmd.add_argument("--transport", choices=("cli_exec", "native_protocol"))
        cmd.add_argument("--catalog-file")
        if name == "prepare":
            cmd.add_argument("--prompt", required=True)
        else:
            cmd.add_argument("--returncode", type=int, required=True)
            cmd.add_argument("--stdout-file", required=True)
            cmd.add_argument("--stderr-file", required=True)
        cmd.set_defaults(func=fn)
    runtime_default = os.environ.get("DEVSQUAD_RUNTIME_DIR", str(Path.home() / ".devsquad" / "runtime"))
    review = sub.add_parser(
        "review",
        help="start an exact-commit Codex branch review without task JSON",
    )
    review.add_argument("--base", default="main")
    review.add_argument("--target", default="HEAD")
    review.add_argument("--project-dir", default=str(Path.cwd()))
    review.add_argument("--model")
    review.add_argument("--effort")
    review.add_argument("--mode", choices=("standard", "adversarial"), default="standard")
    review.add_argument("--focus")
    review.add_argument("--check", action="append")
    review.add_argument("--check-timeout", type=int, default=600)
    review.add_argument("--idempotency-key")
    review.add_argument("--dry-run", action="store_true")
    review.add_argument("--wait", action="store_true")
    review.add_argument("--json", action="store_true")
    review.add_argument("--runtime-dir", default=runtime_default)
    review.set_defaults(func=command_review)
    fix = sub.add_parser(
        "fix",
        help="start bounded Claude implementation and independent Codex review",
    )
    fix.add_argument("issue")
    fix.add_argument("--base", default="HEAD")
    fix.add_argument("--target", default="HEAD")
    fix.add_argument("--project-dir", default=str(Path.cwd()))
    fix.add_argument("--write-path", action="append")
    fix.add_argument("--check", action="append")
    fix.add_argument("--check-timeout", type=int, default=600)
    fix.add_argument("--review-model")
    fix.add_argument("--review-effort")
    fix.add_argument(
        "--review-mode", choices=("standard", "adversarial"),
        default="standard",
    )
    fix.add_argument("--review-focus")
    fix.add_argument("--implementer-model")
    fix.add_argument("--implementer-effort")
    fix.add_argument("--idempotency-key")
    fix.add_argument("--dry-run", action="store_true")
    fix.add_argument("--wait", action="store_true")
    fix.add_argument("--json", action="store_true")
    fix.add_argument("--runtime-dir", default=runtime_default)
    fix.set_defaults(func=command_fix)
    start = sub.add_parser("start"); start.add_argument("--task-file", required=True); start.add_argument("--idempotency-key", required=True); start.add_argument("--supersedes-run"); start.add_argument("--wait", action="store_true"); start.add_argument("--json", action="store_true"); start.add_argument("--runtime-dir", default=runtime_default); start.set_defaults(func=command_start)
    trial = sub.add_parser("trial", help="explicitly run one predeclared bounded experiment arm")
    trial.add_argument("--experiment", required=True)
    trial.add_argument("--case", required=True)
    trial.add_argument("--arm", choices=("control", "candidate"), required=True)
    trial.add_argument("--task-file", required=True)
    trial.add_argument("--idempotency-key", required=True)
    trial.add_argument("--wait", action="store_true")
    trial.add_argument("--json", action="store_true")
    trial.add_argument("--runtime-dir", default=runtime_default)
    trial.set_defaults(func=command_trial)
    for name, fn in (("status",command_status),("result",command_result),("cancel",command_cancel),("resume",command_resume)):
        cmd=sub.add_parser(name); cmd.add_argument("run"); cmd.add_argument("--json",action="store_true"); cmd.add_argument("--runtime-dir",default=runtime_default)
        if name == "resume": cmd.add_argument("--recovery-file")
        cmd.set_defaults(func=fn)
    events=sub.add_parser("events"); events.add_argument("run"); events.add_argument("--after",type=int,default=0); events.add_argument("--limit",type=int,default=100); events.add_argument("--json",action="store_true"); events.add_argument("--runtime-dir",default=runtime_default); events.set_defaults(func=command_events)
    capacity = sub.add_parser("capacity")
    capacity_sub = capacity.add_subparsers(dest="capacity_command", required=True)
    observe = capacity_sub.add_parser("observe")
    observe.add_argument("--file", required=True)
    observe.add_argument("--json", action="store_true")
    observe.add_argument("--runtime-dir", default=runtime_default)
    observe.set_defaults(func=command_capacity_observe)
    outcome = sub.add_parser("outcome")
    outcome_sub = outcome.add_subparsers(dest="outcome_command", required=True)
    outcome_add = outcome_sub.add_parser("add")
    outcome_add.add_argument("run")
    outcome_add.add_argument("--file", required=True)
    outcome_add.add_argument("--json", action="store_true")
    outcome_add.add_argument("--runtime-dir", default=runtime_default)
    outcome_add.set_defaults(func=command_outcome_add)
    report = sub.add_parser("report")
    report.add_argument("--project", required=True)
    report.add_argument("--json", action="store_true")
    report.add_argument("--runtime-dir", default=runtime_default)
    report.set_defaults(func=command_report)
    policy = sub.add_parser("policy")
    policy_sub = policy.add_subparsers(dest="policy_command", required=True)
    evaluate = policy_sub.add_parser("evaluate")
    evaluate.add_argument("--experiment", required=True)
    evaluate.add_argument("--revision-id")
    evaluate.add_argument("--previous-evaluation-sha256")
    evaluate.add_argument("--json", action="store_true")
    evaluate.add_argument("--runtime-dir", default=runtime_default)
    evaluate.set_defaults(func=command_policy_evaluate)
    learn = sub.add_parser("learn")
    learn_sub = learn.add_subparsers(dest="learn_command", required=True)
    propose = learn_sub.add_parser("propose")
    propose.add_argument("--project", required=True)
    propose.add_argument("--json", action="store_true")
    propose.add_argument("--runtime-dir", default=runtime_default)
    propose.set_defaults(func=command_learn_propose)
    profile = sub.add_parser("profile")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    for name, fn in (
        ("template-add", command_profile_template_add),
        ("binding-bootstrap", command_profile_binding_bootstrap),
        ("qualification-add", command_profile_qualification_add),
        ("binding-change", command_profile_binding_change),
        ("binding-fallback", command_profile_binding_fallback),
    ):
        operation = profile_sub.add_parser(name)
        operation.add_argument("--file", required=True)
        operation.add_argument("--json", action="store_true")
        operation.add_argument("--runtime-dir", default=runtime_default)
        operation.set_defaults(func=fn)
    binding_show = profile_sub.add_parser("binding-show")
    binding_show.add_argument("alias")
    binding_show.add_argument("--json", action="store_true")
    binding_show.add_argument("--runtime-dir", default=runtime_default)
    binding_show.set_defaults(func=command_profile_binding_show)
    handoff = sub.add_parser("handoff")
    handoff_sub = handoff.add_subparsers(dest="handoff_command", required=True)
    claim = handoff_sub.add_parser("claim")
    claim.add_argument("run")
    claim.add_argument("--expected-version", type=int, required=True)
    claim.add_argument("--owner", required=True)
    claim.add_argument("--claim-file")
    claim.add_argument("--json", action="store_true")
    claim.add_argument("--runtime-dir", default=runtime_default)
    claim.set_defaults(func=command_handoff_claim)
    complete = handoff_sub.add_parser("complete")
    complete.add_argument("run")
    complete.add_argument("--claim-file", required=True)
    complete.add_argument("--decision-file", required=True)
    complete.add_argument("--json", action="store_true")
    complete.add_argument("--runtime-dir", default=runtime_default)
    complete.set_defaults(func=command_handoff_complete)
    mcp = sub.add_parser("mcp")
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)
    serve = mcp_sub.add_parser("serve")
    serve.add_argument("--runtime-dir", default=runtime_default)
    serve.add_argument("--surface")
    serve.add_argument("--session-ref")
    serve.set_defaults(stream_func=command_mcp_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        if hasattr(args, "stream_func"):
            return args.stream_func(args)
        response, code = args.func(args)
        print(json.dumps(response, sort_keys=True))
        return code
    except (ConflictError, SchemaVersionError) as exc:
        print(json.dumps(envelope(error=error_payload(exc.code, str(exc))), sort_keys=True))
        return 75
    except ContractError as exc:
        print(json.dumps(envelope(error=error_payload(exc.code, str(exc))), sort_keys=True))
        return 64
    except Exception as exc:
        print(json.dumps(envelope(error=error_payload("INTERNAL_ERROR", str(exc))), sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
