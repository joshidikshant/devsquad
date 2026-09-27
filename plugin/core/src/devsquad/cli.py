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


def command_start(args: argparse.Namespace) -> tuple[dict, int]:
    task = _read_json(args.task_file, "task file")
    service = _service(args)
    started = service.start(task, args.idempotency_key, args.supersedes_run)
    if not args.wait:
        return envelope(data=started), 0
    run_id = started["run_id"]
    try:
        while True:
            status = service.status(run_id)
            state = status.get("state")
            if state in WAIT_EXIT_CODES:
                return envelope(data=status), WAIT_EXIT_CODES[state]
            if state not in WAIT_ACTIVE_STATES:
                raise RuntimeError(f"service returned unsupported run state: {state!r}")
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


def command_capacity_observe(args: argparse.Namespace) -> tuple[dict, int]:
    observation = _read_json(args.file, "capacity observation file")
    return envelope(data=_service(args).capacity_observe(observation)), 0


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
    start = sub.add_parser("start"); start.add_argument("--task-file", required=True); start.add_argument("--idempotency-key", required=True); start.add_argument("--supersedes-run"); start.add_argument("--wait", action="store_true"); start.add_argument("--json", action="store_true"); start.add_argument("--runtime-dir", default=runtime_default); start.set_defaults(func=command_start)
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
