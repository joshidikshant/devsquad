"""Small M1 command surface: version, doctor, prepare and classify."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .adapters import AdapterManifest, classify_cli, harness_version, prepare_cli, prepare_native_codex_from_catalog
from .contracts import ContractError, envelope, error_payload
from .service import Service
from .store import ConflictError

SOURCE_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = SOURCE_ROOT if (SOURCE_ROOT / "adapters").is_dir() else Path(sys.prefix) / "share" / "devsquad"


def manifests() -> list[tuple[Path, AdapterManifest]]:
    return [(path, AdapterManifest.load(path)) for path in sorted((CORE_ROOT / "adapters").glob("*/adapter.json"))]


def command_doctor(_: argparse.Namespace) -> tuple[dict, int]:
    rows = []
    for path, manifest in manifests():
        binary = manifest.resolve_binary()
        version = harness_version(binary) if binary else None
        status = "unavailable" if not binary else ("supported" if version in manifest.verified_versions else "unverified")
        rows.append({"adapter": manifest.name, "transport": manifest.transport, "status": status, "binary": binary, "version": version, "manifest": str(path)})
    ready = any(row["status"] != "unavailable" for row in rows)
    return envelope(data={"core_version": __version__, "ready": ready, "adapters": rows}), 0 if ready else 1


def command_prepare(args: argparse.Namespace) -> dict:
    manifest = AdapterManifest.load(CORE_ROOT / "adapters" / args.adapter / "adapter.json")
    transport = args.transport or manifest.transport
    if transport == "native_protocol":
        if manifest.name != "codex" or not args.catalog_file or not args.model or not args.effort:
            raise ContractError("native preparation requires Codex, --catalog-file, --model and --effort")
        binary = manifest.resolve_binary()
        version = harness_version(binary) if binary else None
        snapshot = json.loads(Path(args.catalog_file).read_text())
        spec = prepare_native_codex_from_catalog(manifest, snapshot, cwd=args.cwd, model=args.model, effort=args.effort, permission=args.permission, timeout_seconds=args.timeout, harness_version_value=version or "unknown")
    elif transport == "cli_exec":
        spec = prepare_cli(manifest, prompt=args.prompt, cwd=args.cwd, model=args.model, effort=args.effort, permission=args.permission, timeout_seconds=args.timeout)
    else:
        raise ContractError(f"unsupported transport: {transport}")
    return envelope(data=spec.to_dict()), 0


def command_classify(args: argparse.Namespace) -> dict:
    manifest = AdapterManifest.load(CORE_ROOT / "adapters" / args.adapter / "adapter.json")
    spec = prepare_cli(manifest, prompt="classification", cwd=args.cwd, model=args.model, effort=args.effort, permission=args.permission, timeout_seconds=args.timeout)
    result = classify_cli(spec, returncode=args.returncode, stdout=Path(args.stdout_file).read_text(), stderr=Path(args.stderr_file).read_text())
    return envelope(data=result.to_dict()), 0


def _service(args: argparse.Namespace) -> Service:
    return Service(Path(args.runtime_dir))


def command_start(args: argparse.Namespace) -> tuple[dict, int]:
    task = json.loads(Path(args.task_file).read_text())
    return envelope(data=_service(args).start(task, args.idempotency_key, args.supersedes_run)), 0


def command_status(args: argparse.Namespace) -> tuple[dict, int]: return envelope(data=_service(args).status(args.run)), 0
def command_events(args: argparse.Namespace) -> tuple[dict, int]: return envelope(data=_service(args).events(args.run, args.after, args.limit)), 0
def command_result(args: argparse.Namespace) -> tuple[dict, int]: return envelope(data=_service(args).result(args.run)), 0
def command_cancel(args: argparse.Namespace) -> tuple[dict, int]: return envelope(data=_service(args).cancel(args.run)), 0
def command_resume(args: argparse.Namespace) -> tuple[dict, int]:
    recovery = json.loads(Path(args.recovery_file).read_text()) if args.recovery_file else None
    return envelope(data=_service(args).resume(args.run, recovery)), 0


class ContractParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ContractError(message)


def parser() -> argparse.ArgumentParser:
    p = ContractParser(prog="squad")
    p.add_argument("--version", action="version", version=f"squad {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor"); doctor.add_argument("--json", action="store_true"); doctor.set_defaults(func=command_doctor)
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
    start = sub.add_parser("start"); start.add_argument("--task-file", required=True); start.add_argument("--idempotency-key", required=True); start.add_argument("--supersedes-run"); start.add_argument("--json", action="store_true"); start.add_argument("--runtime-dir", default=runtime_default); start.set_defaults(func=command_start)
    for name, fn in (("status",command_status),("result",command_result),("cancel",command_cancel),("resume",command_resume)):
        cmd=sub.add_parser(name); cmd.add_argument("run"); cmd.add_argument("--json",action="store_true"); cmd.add_argument("--runtime-dir",default=runtime_default)
        if name == "resume": cmd.add_argument("--recovery-file")
        cmd.set_defaults(func=fn)
    events=sub.add_parser("events"); events.add_argument("run"); events.add_argument("--after",type=int,default=0); events.add_argument("--limit",type=int,default=100); events.add_argument("--json",action="store_true"); events.add_argument("--runtime-dir",default=runtime_default); events.set_defaults(func=command_events)
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        response, code = args.func(args)
        print(json.dumps(response, sort_keys=True))
        return code
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        code = getattr(exc, "code", "INPUT_INVALID")
        print(json.dumps(envelope(error=error_payload(code, str(exc))), sort_keys=True))
        return 75 if isinstance(exc, ConflictError) else 64


if __name__ == "__main__":
    raise SystemExit(main())
